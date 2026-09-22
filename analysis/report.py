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
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from analysis.stats import mde, srm_check, two_proportion_test  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/events.db")
    ap.add_argument("--experiment", default="exp001_layout")
    args = ap.parse_args()

    import store
    counts = store.counts(args.experiment, path=Path(args.db))
    if set(counts) != {"A", "B"}:
        print(f"Need both arms, got {sorted(counts)}. No traffic yet?")
        return 1

    a, b = counts["A"], counts["B"]
    print(f"EXPERIMENT: {args.experiment}\n" + "=" * 60)

    srm = srm_check(a["exposed"], b["exposed"])
    print(srm)
    if srm.failed:
        print("\nSTOP. Assignment or logging is broken. Do not read the metric "
              "below -- fix the pipeline and rerun the experiment.")
        return 2

    print("\nPRIMARY METRIC — detail_ctr\n" + "-" * 60)
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
