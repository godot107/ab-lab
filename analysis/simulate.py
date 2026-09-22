"""Validate the analysis against known ground truth.

A portfolio site will not produce enough traffic to detect a realistic layout
effect (see the MDE table in docs/experiment_design.md). That is a limit on
what the real experiment can conclude -- not on whether the method is correct.

Here the true effect is set by hand, so the same code that analyzes real
traffic can be checked for the three things that actually matter: does it find
an effect it is powered for, does it stay quiet when there is none, and how
badly does peeking break it.

    python analysis/simulate.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis.stats import mde, required_n, two_proportion_test  # noqa: E402

RNG = np.random.default_rng(20260921)
BASE = 0.30


def trial(n_per_arm: int, p_a: float, p_b: float):
    return two_proportion_test(
        int(RNG.binomial(n_per_arm, p_a)), n_per_arm,
        int(RNG.binomial(n_per_arm, p_b)), n_per_arm,
    )


def rate_of_significance(n_per_arm: int, p_a: float, p_b: float, trials: int) -> float:
    return sum(trial(n_per_arm, p_a, p_b).significant for _ in range(trials)) / trials


def peeking_false_positive_rate(n_per_arm: int, checks: int, trials: int) -> float:
    """Both arms have the SAME true rate, so every 'significant' result is a
    false positive. Analyze once and it sits at 5%. Analyze repeatedly as data
    arrives and stop at the first p < 0.05, and it does not."""
    step = n_per_arm // checks
    hits = 0
    for _ in range(trials):
        a = RNG.random(n_per_arm) < BASE
        b = RNG.random(n_per_arm) < BASE
        for k in range(step, n_per_arm + 1, step):
            if two_proportion_test(int(a[:k].sum()), k, int(b[:k].sum()), k).significant:
                hits += 1
                break
    return hits / trials


if __name__ == "__main__":
    TRIALS = 2000
    print(f"Baseline {BASE:.0%}, alpha=0.05, {TRIALS:,} simulated experiments each.\n")

    print("1. FALSE POSITIVE RATE — no true effect, analyzed once")
    for n in (250, 1000):
        r = rate_of_significance(n, BASE, BASE, TRIALS)
        print(f"   n={n:>5,}/arm   {r:6.1%}   (should sit near 5%)")

    print("\n2. POWER — can it find an effect that is really there?")
    print(f"   {'true lift':>10} {'n/arm':>8} {'detected':>9} {'target':>8}")
    for rel in (0.50, 0.25, 0.10):
        n = required_n(BASE, rel)
        r = rate_of_significance(n, BASE, BASE * (1 + rel), TRIALS)
        print(f"   {rel:>9.0%} {n:>8,} {r:>9.1%} {'80%':>8}")

    print("\n3. UNDERPOWERED — the real experiment's likely position")
    n = 250
    print(f"   At n={n}/arm the smallest detectable lift is {mde(n, BASE):.1%} absolute.")
    for rel in (0.10, 0.20):
        r = rate_of_significance(n, BASE, BASE * (1 + rel), TRIALS)
        print(f"   A true {rel:.0%} lift is found only {r:.1%} of the time "
              f"— a null result here means 'not measured', not 'no effect'.")

    print("\n4. PEEKING — the same null data, checked repeatedly")
    print(f"   {'checks':>8} {'false positive rate':>21}")
    for checks in (1, 4, 7, 14, 28):
        r = peeking_false_positive_rate(1000, checks, TRIALS // 2)
        flag = "" if checks == 1 else "  <-- inflated"
        print(f"   {checks:>8} {r:>20.1%}{flag}")
    print("\n   This is why the stopping rule is fixed in advance and /api/stats")
    print("   reports counts but never a p-value.")
