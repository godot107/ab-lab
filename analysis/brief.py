"""The stakeholder brief: what the experiment says, in the order a CIO needs it.

    python analysis/brief.py --db data/events.db --experiment exp001_layout
    python analysis/brief.py --db data/events.db --experiment exp001_layout --out brief.md

Two modes, chosen by the pre-registered stopping rule, not by the reader:

- **Interim** (rule not met): progress, data health and how visitors use the
  dashboard with both layouts pooled. No A-vs-B comparison. An interim
  scoreboard is an invitation to stop the moment one arm looks ahead, which is
  how a 5% false-positive rate becomes 28% (analysis/simulate.py).
- **Final** (rule met): the decision, in plain language, with how far to trust
  it, then per-layout usage detail labelled exploratory.

report.py is the analyst's one-time readout; this renders the same single
analysis for people who will act on it. Run both on the same final export.
"""
from __future__ import annotations

import argparse
import math
import sqlite3
import statistics
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis.stats import ALPHA, mde, srm_check, two_proportion_test  # noqa: E402

# Pre-registered stopping rule, docs/experiment_design.md section 5.
TARGET_PER_ARM = 250
MAX_DAYS = 28
# Where a drill-through started (drill_through.target on the monitor).
TARGET_LABEL = {"rank": "Sector ranking", "trend": "Trend chart", "mult": "Small multiples",
                "sector-table": "Sector table"}


@dataclass
class Visitor:
    variant: str
    exposed_at: datetime
    device: str
    pageviews: int = 0
    first_click: datetime | None = None
    first_target: str | None = None
    interactions: int = 0
    visible_secs: int | None = None   # sum of page_time events; None = no timing sent


@dataclass
class Data:
    visitors: dict[str, Visitor]
    first_ts: datetime
    last_ts: datetime
    orphans: int                      # clicks with no exposure: should always be 0

    @property
    def days(self) -> float:
        return max((self.last_ts - self.first_ts).total_seconds() / 86400, 1 / 24)

    def arm(self, v: str) -> list[Visitor]:
        return [x for x in self.visitors.values() if x.variant == v]

    def only(self, synthetic: bool) -> "Data":
        """The same data, restricted to synthetic or to real visitors."""
        return Data({k: v for k, v in self.visitors.items() if (v.device == "synthetic") == synthetic},
                    self.first_ts, self.last_ts, self.orphans)


def load(db: Path, experiment: str, conversion: str = "drill_through") -> Data:
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT ts, visitor_id, variant, event, target, device FROM events"
        " WHERE experiment = ? ORDER BY ts, id", (experiment,)).fetchall()
    if not rows:
        raise SystemExit(f"No events for experiment {experiment!r} in {db}.")

    visitors: dict[str, Visitor] = {}
    orphans = 0
    for ts, vid, variant, event, target, device in rows:
        t = datetime.fromisoformat(ts)
        if event == "exposure":
            visitors[vid] = Visitor(variant, t, device or "unknown")
        elif vid not in visitors:
            orphans += 1
        elif event == "pageview":
            visitors[vid].pageviews += 1
        elif event == conversion and visitors[vid].first_click is None:
            visitors[vid].first_click, visitors[vid].first_target = t, target
        elif event in (conversion, "interaction"):   # any later click counts as exploring
            visitors[vid].interactions += 1
        elif event == "page_time" and (target or "").isdigit():
            visitors[vid].visible_secs = (visitors[vid].visible_secs or 0) + int(target)
    conn.close()
    return Data(visitors, datetime.fromisoformat(rows[0][0]),
                datetime.fromisoformat(rows[-1][0]), orphans)


# --- formatting -------------------------------------------------------------

def pct(x: float, digits: int = 0) -> str:
    return f"{x:.{digits}%}"


def pts(x: float) -> str:
    return f"{x * 100:+.1f} pts"


def odds_phrase(p: float) -> str:
    """A p-value as a frequency a non-statistician can hold on to."""
    if p < 0.001:
        return "less than 1 time in 1,000"
    return f"about 1 time in {max(round(1 / p), 1):,}"


def share_table(visitors: list[Visitor], key, label=str) -> list[tuple[str, int, str]]:
    counts = Counter(key(v) for v in visitors if key(v) is not None)
    total = sum(counts.values())
    return [(label(k), n, pct(n / total)) for k, n in counts.most_common()]


def md_table(header: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


# --- usage (the UX questions) ------------------------------------------------

def usage(vs: list[Visitor]) -> dict:
    clicked = [v for v in vs if v.first_click]
    secs = [(v.first_click - v.exposed_at).total_seconds() for v in clicked]
    return {
        "n": len(vs),
        "opened": len(clicked) / len(vs) if vs else 0.0,
        "median_secs": statistics.median(secs) if secs else None,
        "explored": (sum(v.interactions > 0 for v in clicked) / len(clicked)) if clicked else None,
        "returning": sum(v.pageviews > 1 for v in vs) / len(vs) if vs else 0.0,
        **time_on_page(vs),
    }


QUICK_EXIT_SECS = 10


def time_on_page(vs: list[Visitor]) -> dict:
    """Visible seconds per visitor, from page_time beacons. Phones can close a tab
    without sending one, so coverage is reported and the median is used, not the
    mean (a few tabs left open for an hour would drag a mean anywhere)."""
    timed = [v for v in vs if v.visible_secs is not None]
    return {
        "timed_share": len(timed) / len(vs) if vs else 0.0,
        "median_visible": statistics.median(v.visible_secs for v in timed) if timed else None,
        "quick_exit": (sum(v.visible_secs < QUICK_EXIT_SECS and not v.first_click for v in timed)
                       / len(timed)) if timed else None,
    }


def usage_lines(u: dict) -> list[str]:
    lines = [f"- **Drilled into the data:** {pct(u['opened'])} of {u['n']:,} visitors."]
    if u["median_secs"] is not None:
        lines.append(f"- **Time to first click:** median {u['median_secs']:.0f} seconds after the page loaded.")
    if u["explored"] is not None:
        lines.append(f"- **Kept exploring:** {pct(u['explored'])} of those who drilled in did it again.")
    lines.append(f"- **Came back:** {pct(u['returning'])} of visitors loaded the page more than once.")
    if u["median_visible"] is not None:
        lines.append(f"- **Time on page:** median {u['median_visible']:.0f} seconds visible per visitor "
                     f"(measured for {pct(u['timed_share'])} of visitors).")
        lines.append(f"- **Quick exits:** {pct(u['quick_exit'])} left within {QUICK_EXIT_SECS} seconds "
                     "without drilling in.")
    return lines


# --- the brief -----------------------------------------------------------------

def health(d: Data) -> tuple[list[str], bool]:
    a, b = len(d.arm("A")), len(d.arm("B"))
    srm = srm_check(a, b)
    ok = not srm.failed and d.orphans == 0
    lines = [
        f"- **Traffic split:** {a:,} / {b:,} visitors (A / B). "
        + ("Consistent with the planned 50/50." if not srm.failed else
           "**Not consistent with 50/50: assignment or logging is broken. "
           "Nothing below can be trusted until it is fixed and the test rerun.**"),
        "- **Event integrity:** " + ("every click traces back to an exposed visitor."
                                      if d.orphans == 0 else
                                      f"**{d.orphans} clicks have no matching exposure.** Investigate before reading on."),
    ]
    return lines, ok


def render(d: Data, experiment: str, want_final: bool,
           target: int = TARGET_PER_ARM, max_days: int = MAX_DAYS) -> str:
    a, b = d.arm("A"), d.arm("B")
    smaller = min(len(a), len(b))
    rule_met = smaller >= target or d.days >= max_days
    if want_final and not rule_met:
        raise SystemExit(
            f"Stopping rule not met ({smaller} of {target} visitors in the smaller "
            f"layout, day {d.days:.1f} of {max_days}). The final brief waits for it; "
            f"run without --final for the interim.")
    final = rule_met

    synthetic = sum(v.device == "synthetic" for v in d.visitors.values())
    # Synthetic visitors carry a hand-set effect. Mixed with real ones, the final
    # decision is read on the real visitors only; the synthetic ones get a
    # method-check section of their own.
    mixed = 0 < synthetic < len(d.visitors)
    health_lines, healthy = health(d)
    out = [f"# Dashboard layout test: {'decision brief' if final else 'status update'}",
           "",
           f"*Experiment `{experiment}` · data {d.first_ts:%b %-d} to {d.last_ts:%b %-d, %Y} "
           f"({d.days:.1f} days) · generated {datetime.now(timezone.utc):%b %-d, %Y} (UTC)*", ""]
    if mixed and final:
        out += [f"> **Part synthetic.** {synthetic:,} of {len(d.visitors):,} visitors "
                "were generated by `analysis/synthetic_traffic.py` with a known, hand-set "
                f"effect. The decision below uses only the {len(d.visitors) - synthetic:,} "
                "real visitors; the synthetic ones appear separately, as a check of the "
                "method.", ""]
    elif synthetic:
        out += [f"> **Synthetic demo data.** {synthetic:,} of {len(d.visitors):,} visitors "
                "were generated by `analysis/synthetic_traffic.py` with a known, hand-set "
                "effect. This brief demonstrates the pipeline and the reporting; it is not "
                "evidence about real users.", ""]

    out += ["## The question", "",
            "Does putting the charts first (the trend and the sector ranking), instead of "
            "the KPI tiles, get more people to drill into the rows behind a number? "
            "Drilling in is the measure of whether the dashboard invites a second "
            "question. The measure, the "
            "sample size and the decision rule were fixed before any data arrived "
            "(`docs/experiment_design.md`).", ""]

    if not final:
        per_day = smaller / d.days
        remaining = target - smaller
        eta = min(remaining / per_day if per_day else float("inf"), max_days - d.days)
        when = "within a day" if eta < 1 else f"in about {eta:.0f} more days"
        day = max(1, math.ceil(d.days))
        out += ["## Bottom line", "",
                ("**No decision yet, by design.** " if healthy else
                 "**Data problem: the test is not producing usable data.** ")
                + f"The test is {pct(smaller / target)} of the way to its planned "
                  f"sample ({smaller:,} of {target:,} visitors per layout, day "
                  f"{day} of at most {max_days}). At the current pace it completes {when}.", "",
                "## Why no A-vs-B numbers yet", "",
                "Early results swing widely, and acting on the first lead turns a 5% chance "
                "of a false win into roughly 28% (measured in `analysis/simulate.py`). The "
                "comparison is sealed until the planned sample is reached, then read once.", "",
                "## Data health", "", *health_lines, "",
                "## How visitors use the dashboard (both layouts combined)", "",
                *usage_lines(usage(list(d.visitors.values()))), ""]
        firsts = share_table(list(d.visitors.values()), lambda v: v.first_target,
                             lambda k: TARGET_LABEL.get(k, k))
        out += (["Where first clicks land:", "",
                 md_table(["Element", "First clicks", "Share"], [list(r) for r in firsts]), ""]
                if firsts else ["No one has drilled in yet, so there are no first clicks to show.", ""])
        out += ["Devices:", "",
                md_table(["Device", "Visitors", "Share"],
                         [list(r) for r in share_table(list(d.visitors.values()),
                                                       lambda v: v.device)]), ""]
        out += ["## Next step", "",
                f"Keep collecting. The decision brief is produced once, when both layouts "
                f"reach {target} visitors or on day {max_days}, whichever comes first.", ""]
        return "\n".join(out)

    # --- final ---------------------------------------------------------------
    if mixed:
        method_check = synthetic_check(d.only(True))
        d = d.only(False)
        a, b = d.arm("A"), d.arm("B")
        smaller = min(len(a), len(b))
        health_lines, healthy = health(d)
    else:
        method_check = []
    if not healthy or not smaller:
        reason = ("**No decision: the data failed its integrity checks.** A broken traffic "
                  "split or missing exposures can manufacture a difference on its own, so the "
                  "result is withheld rather than reported. The fix is to repair the pipeline "
                  "and rerun the test." if not healthy else
                  "**No decision: one layout has no real visitors**, so there is nothing to "
                  "compare.")
        out += ["## Bottom line", "", reason, "", "## Data health", "", *health_lines, "",
                *method_check]
        return "\n".join(out)

    res = two_proportion_test(sum(bool(v.first_click) for v in a), len(a),
                              sum(bool(v.first_click) for v in b), len(b))
    lo, hi = res.ci
    detectable = mde(smaller, res.p_a)
    if res.significant and res.abs_lift > 0:
        decision = "Adopt layout B (charts first)."
        verdict = (f"Putting the charts first raised the share of visitors who drilled "
                   f"into the data from {pct(res.p_a)} to {pct(res.p_b)}, "
                   f"a gain of {res.abs_lift * 100:.0f} points.")
    elif res.significant:
        decision = "Keep layout A (tiles first)."
        verdict = (f"Putting the charts first *lowered* the share of visitors who drilled "
                   f"into the data, from {pct(res.p_a)} to {pct(res.p_b)}.")
    else:
        decision = "No change: keep layout A, the current default."
        verdict = (f"The two layouts could not be told apart: {pct(res.p_a)} of visitors "
                   f"drilled in with A, {pct(res.p_b)} with B.")

    out += ["## Bottom line", "", f"**{decision}** {verdict}", "",
            "## The evidence", "",
            md_table(["", "Layout A (tiles first)", "Layout B (charts first)"],
                     [["Visitors", f"{res.n_a:,}", f"{res.n_b:,}"],
                      ["Drilled into the data", f"{res.x_a:,}", f"{res.x_b:,}"],
                      ["**Rate**", f"**{pct(res.p_a, 1)}**", f"**{pct(res.p_b, 1)}**"]]), "",
            f"- **Difference:** {pts(res.abs_lift)} for B. Plausible range, given the "
            f"sample: {pts(lo)} to {pts(hi)}.",
            f"- **Could it be chance?** If the layouts truly performed the same, a gap this "
            f"large would show up {odds_phrase(res.p_value)}. The bar set in advance was "
            f"1 in {round(1 / ALPHA)}.",
            f"- **Sensitivity:** with this many visitors the test reliably detects "
            f"differences of {detectable * 100:.0f} points or more."
            + ("" if res.significant else
               " A smaller real difference could exist and go unseen, so this is "
               "*not* evidence that the layouts are equivalent."), "",
            "## How far to trust it", "", *health_lines,
            f"- **Sample:** {smaller:,} visitors in the smaller layout against a plan of "
            f"{target:,}.",
            "- **Audience:** visitors to a portfolio site, mostly recruiters and peers. The "
            "result describes this audience, not every dashboard user.",
            "- **What it measures:** whether people dig into the data, which is "
            "engagement, not proof of usefulness. A layout that answers the question "
            "faster could lower it.",
            "- **Not checked:** page speed and error rates were not instrumented, so the "
            "decision has no guardrail against a layout that is slower or breaks.", "",
            "## How each layout was used (exploratory)", "",
            "Context for the decision, not part of it. These cuts were not the test's "
            "question, so a difference here is a lead for the next experiment, not a finding.", "",
            md_table(["", "Layout A", "Layout B"],
                     usage_rows(usage(a), usage(b))), "",
            "First clicks by element:", "",
            md_table(["Element", "Layout A", "Layout B"], first_click_rows(a, b)), ""]
    out += method_check
    out += ["## Recommended next step", "",
            {True: "Roll out layout B, then confirm with a follow-up test that adds the "
                   "page-speed and error guardrails this one lacked.",
             False: "Keep the current layout. If the question still matters, rerun with "
                    "more traffic: detecting a difference half this size takes about four "
                    "times the visitors."}[res.significant and res.abs_lift > 0], ""]
    return "\n".join(out)


def synthetic_check(s: Data) -> list[str]:
    """The synthetic visitors' own result, kept out of the decision. Their effect
    was set by hand, so this shows whether the pipeline recovers a known truth."""
    a, b = s.arm("A"), s.arm("B")
    srm = srm_check(len(a), len(b))
    lines = ["## Method check (synthetic traffic, not part of the decision)", "",
             f"{len(s.visitors):,} synthetic visitors carried an effect set by hand in "
             "`analysis/synthetic_traffic.py`. They test the pipeline, not the layouts, so "
             "none of the numbers above include them.", "",
             f"- **Traffic split:** {len(a):,} / {len(b):,} (A / B)"
             + (", consistent with 50/50." if not srm.failed else ", **not consistent with 50/50.**")]
    if a and b:
        res = two_proportion_test(sum(bool(v.first_click) for v in a), len(a),
                                  sum(bool(v.first_click) for v in b), len(b))
        lo, hi = res.ci
        lines.append(f"- **Drilled into the data:** {pct(res.p_a, 1)} with A, {pct(res.p_b, 1)} "
                     f"with B: {pts(res.abs_lift)} (plausible range {pts(lo)} to {pts(hi)}). "
                     "The method works if that range covers the effect that was planted.")
    return lines + [""]


def usage_rows(ua: dict, ub: dict) -> list[list[str]]:
    def secs(u):
        return "n/a" if u["median_secs"] is None else f"{u['median_secs']:.0f} s"

    def maybe(x):
        return "n/a" if x is None else pct(x)

    def vis(u):
        return "n/a" if u["median_visible"] is None else f"{u['median_visible']:.0f} s"
    return [["Drilled into the data", pct(ua["opened"]), pct(ub["opened"])],
            ["Median time to first click", secs(ua), secs(ub)],
            ["Kept exploring (of those who drilled in)", maybe(ua["explored"]), maybe(ub["explored"])],
            ["Came back", pct(ua["returning"]), pct(ub["returning"])],
            ["Median time on page (visible)", vis(ua), vis(ub)],
            [f"Quick exits (< {QUICK_EXIT_SECS} s, no drill)", maybe(ua["quick_exit"]), maybe(ub["quick_exit"])]]


def first_click_rows(a: list[Visitor], b: list[Visitor]) -> list[list[str]]:
    def shares(vs):
        c = Counter(v.first_target for v in vs if v.first_target)
        return c, sum(c.values())
    (ca, ta), (cb, tb) = shares(a), shares(b)
    keys = sorted(set(ca) | set(cb), key=lambda k: -(ca[k] + cb[k]))
    return [[TARGET_LABEL.get(k, k),
             pct(ca[k] / ta) if ta else "n/a", pct(cb[k] / tb) if tb else "n/a"] for k in keys]



def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default="data/events.db")
    ap.add_argument("--experiment", default="exp001_layout")
    ap.add_argument("--conversion", default="drill_through",
                    help="the pre-registered primary event")
    ap.add_argument("--target-per-arm", type=int, default=TARGET_PER_ARM,
                    help="stopping rule: visitors in the smaller arm (pre-registered: 250)")
    ap.add_argument("--max-days", type=int, default=MAX_DAYS,
                    help="stopping rule: days running (pre-registered: 28; the EC2 demo: 7)")
    ap.add_argument("--final", action="store_true",
                    help="require the final brief; refuses if the stopping rule isn't met")
    ap.add_argument("--out", type=Path, help="write Markdown here instead of stdout")
    args = ap.parse_args()

    text = render(load(Path(args.db), args.experiment, args.conversion), args.experiment, args.final,
                  args.target_per_arm, args.max_days)
    if args.out:
        args.out.write_text(text + "\n")
        print(f"wrote {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
