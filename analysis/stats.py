"""Frequentist analysis for a two-arm binomial experiment.

The tests are written out rather than called from a library one-liner: the
point of this repo is to show the arithmetic, not to hide it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from scipy import stats

ALPHA = 0.05
SRM_ALPHA = 0.001  # deliberately strict: this is a smoke alarm, not a finding


@dataclass
class SRMResult:
    n_a: int
    n_b: int
    expected_a: float
    chi2: float
    p_value: float

    @property
    def failed(self) -> bool:
        return self.p_value < SRM_ALPHA

    def __str__(self) -> str:
        total = self.n_a + self.n_b
        share = self.n_a / total if total else float("nan")
        verdict = "FAIL — STOP AND DEBUG" if self.failed else "pass"
        return (
            f"SRM check: {self.n_a:,} / {self.n_b:,} "
            f"(A share {share:.3f}, expected {self.expected_a:.3f})  "
            f"chi2={self.chi2:.2f} p={self.p_value:.4f}  [{verdict}]"
        )


@dataclass
class TestResult:
    x_a: int
    n_a: int
    x_b: int
    n_b: int
    alpha: float

    @property
    def p_a(self) -> float:
        return self.x_a / self.n_a

    @property
    def p_b(self) -> float:
        return self.x_b / self.n_b

    @property
    def abs_lift(self) -> float:
        return self.p_b - self.p_a

    @property
    def rel_lift(self) -> float:
        return self.abs_lift / self.p_a if self.p_a else float("nan")

    @property
    def z(self) -> float:
        # Pooled standard error: the null says both arms share one rate, so the
        # test statistic is built under that assumption.
        p_pool = (self.x_a + self.x_b) / (self.n_a + self.n_b)
        se = math.sqrt(p_pool * (1 - p_pool) * (1 / self.n_a + 1 / self.n_b))
        return self.abs_lift / se if se else 0.0

    @property
    def p_value(self) -> float:
        return 2 * (1 - stats.norm.cdf(abs(self.z)))

    @property
    def ci(self) -> tuple[float, float]:
        # Unpooled SE for the interval: here we are estimating the difference,
        # not testing whether it is zero, so the arms keep their own rates.
        se = math.sqrt(
            self.p_a * (1 - self.p_a) / self.n_a + self.p_b * (1 - self.p_b) / self.n_b
        )
        z_crit = stats.norm.ppf(1 - self.alpha / 2)
        return (self.abs_lift - z_crit * se, self.abs_lift + z_crit * se)

    @property
    def significant(self) -> bool:
        return self.p_value < self.alpha

    def __str__(self) -> str:
        lo, hi = self.ci
        verdict = "SIGNIFICANT" if self.significant else "not significant"
        return (
            f"A: {self.x_a:,}/{self.n_a:,} = {self.p_a:.4f}\n"
            f"B: {self.x_b:,}/{self.n_b:,} = {self.p_b:.4f}\n"
            f"abs lift {self.abs_lift:+.4f}  rel lift {self.rel_lift:+.1%}\n"
            f"95% CI on abs lift [{lo:+.4f}, {hi:+.4f}]\n"
            f"z={self.z:.3f}  p={self.p_value:.4f}  [{verdict} at alpha={self.alpha}]"
        )


def srm_check(n_a: int, n_b: int, expected_a: float = 0.5) -> SRMResult:
    """Did the randomizer actually split traffic the way it promised?

    Run this before looking at any metric. A mismatch means assignment or
    logging is broken, and a broken randomizer can manufacture a result on its
    own -- so this failing invalidates the experiment rather than reporting on
    it.
    """
    total = n_a + n_b
    exp_a, exp_b = total * expected_a, total * (1 - expected_a)
    chi2 = (n_a - exp_a) ** 2 / exp_a + (n_b - exp_b) ** 2 / exp_b
    p = 1 - stats.chi2.cdf(chi2, df=1)
    return SRMResult(n_a, n_b, expected_a, chi2, p)


def two_proportion_test(
    x_a: int, n_a: int, x_b: int, n_b: int, alpha: float = ALPHA
) -> TestResult:
    """Two-sided z-test on the difference of two conversion rates."""
    if n_a <= 0 or n_b <= 0:
        raise ValueError("both arms need at least one exposed visitor")
    return TestResult(x_a, n_a, x_b, n_b, alpha)


def required_n(
    baseline: float, rel_lift: float, alpha: float = ALPHA, power: float = 0.80
) -> int:
    """Visitors needed PER ARM to detect a relative lift at the given power."""
    treated = baseline * (1 + rel_lift)
    # Cohen's h -- the arcsine transform stabilizes the variance of a proportion.
    # Var(2*asin(sqrt(p_hat))) ~= 1/n, so the DIFFERENCE of two arms has
    # variance 2/n. That factor of 2 is the whole formula: dropping it
    # understates the requirement by half, which analysis/simulate.py catches
    # as a power curve sitting at 50% where it should be at 80%.
    h = 2 * math.asin(math.sqrt(treated)) - 2 * math.asin(math.sqrt(baseline))
    z_a = stats.norm.ppf(1 - alpha / 2)
    z_b = stats.norm.ppf(power)
    return math.ceil(2 * ((z_a + z_b) / h) ** 2)


def mde(
    n_per_arm: int, baseline: float, alpha: float = ALPHA, power: float = 0.80
) -> float:
    """Smallest ABSOLUTE lift this sample size can detect. The honesty check:
    run it before the experiment, not after a null result."""
    lo, hi = 1e-6, 1 - baseline - 1e-6
    for _ in range(200):
        mid = (lo + hi) / 2
        if required_n(baseline, mid / baseline, alpha, power) > n_per_arm:
            lo = mid
        else:
            hi = mid
    return hi
