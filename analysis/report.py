"""The readout. Run ONCE, when the pre-registered stopping rule is met.

    python analysis/report.py --db data/events.db

Order matters: SRM first. If the randomizer is broken, the metric below it is
not a weak result -- it is a meaningless one, and reading it anyway is how a
broken pipeline becomes a published finding.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.stats import mde, srm_check, two_proportion_test  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/events.db")
    ap.add_argument("--experiment", default="exp001_layout")
    ap.add_argument("--conversion", default="drill_through",
                    help="the pre-registered primary event")
    args = ap.parse_args()

    from ablab import store

    def count(synthetic=None):
        return store.counts(args.experiment, path=Path(args.db), conversion=args.conversion,
                            synthetic=synthetic)

    everyone, real, synth = count(), count(False), count(True)
    if set(everyone) != {"A", "B"}:
        print(f"Need both arms, got {sorted(everyone)}. No traffic yet?")
        return 1

    # Synthetic visitors carry a hand-set effect. Pooled with real ones, that
    # effect becomes the headline, so when both are present the decision is
    # read on real visitors only and the synthetic ones are a method check.
    if real and synth:
        strata = [("REAL VISITORS (the decision)", real),
                  ("SYNTHETIC VISITORS (method check against the planted effect; "
                   "excluded from the decision)", synth)]
    elif synth:
        strata = [("ALL VISITORS ARE SYNTHETIC (a demo of the method, not evidence "
                   "about users)", synth)]
    else:
        strata = [("ALL VISITORS", real)]

    print(f"EXPERIMENT: {args.experiment}\n" + "=" * 60)
    for label, c in strata:
        srm = srm_check(c.get("A", {}).get("exposed", 0), c.get("B", {}).get("exposed", 0))
        print(f"{label}\n  {srm}")
        if srm.failed:
            print("\nSTOP. Assignment or logging is broken. Do not read the metric "
                  "below -- fix the pipeline and rerun the experiment.")
            return 2

    for label, c in strata:
        print(f"\n{label}\nPRIMARY METRIC — {args.conversion} rate (visitors)\n" + "-" * 60)
        if set(c) != {"A", "B"}:
            print(f"Only arm {sorted(c)} has visitors here; nothing to compare.")
            continue
        a, b = c["A"], c["B"]
        res = two_proportion_test(a["converted"], a["exposed"], b["converted"], b["exposed"])
        print(res)

        smallest = mde(min(a["exposed"], b["exposed"]), res.p_a)
        print(f"\nSmallest lift this sample could detect at 80% power: "
              f"{smallest:.1%} absolute")
        if not res.significant:
            print("NOT SIGNIFICANT. Given the sample size, this means the effect was "
                  f"not measurable below {smallest:.1%} — it does NOT mean there is "
                  "no effect. Report it as inconclusive.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
