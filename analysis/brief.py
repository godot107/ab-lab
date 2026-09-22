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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis.stats import ALPHA, mde, srm_check, two_proportion_test  # noqa: E402

# Pre-registered stopping rule, docs/experiment_design.md section 5.
TARGET_PER_ARM = 250
MAX_DAYS = 28
TARGET_LABEL = {"trend": "Chart", "total": "Tile: all postings", "new": "Tile: new postings",
                "yoy": "Tile: vs. a year ago", "sectors": "Tile: sectors above 2020"}


@dataclass
class Visitor:
    variant: str
    exposed_at: datetime
    device: str
    pageviews: int = 0
    first_click: datetime | None = None
    first_target: str | None = None
    interactions: int = 0


@dataclass
class Data:
    visitors: dict[str, Visitor]
    first_ts: datetime
    last_ts: datetime
    orphans: int                      # clicks with no exposure: should always be 0
    chat: dict = field(default_factory=dict)

    @property
    def days(self) -> float:
        return max((self.last_ts - self.first_ts).total_seconds() / 86400, 1 / 24)

    def arm(self, v: str) -> list[Visitor]:
        return [x for x in self.visitors.values() if x.variant == v]


def load(db: Path, experiment: str) -> Data:
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
        elif event == "detail_click" and visitors[vid].first_click is None:
            visitors[vid].first_click, visitors[vid].first_target = t, target
        elif event == "interaction":
            visitors[vid].interactions += 1

    chat = {}
    try:
        q = conn.execute("SELECT COALESCE(SUM(n), 0), COUNT(DISTINCT day) FROM chat_quota"
                         " WHERE who = '*'").fetchone()
        askers = conn.execute("SELECT COUNT(DISTINCT who) FROM chat_quota"
                              " WHERE who NOT IN ('*', 'anon')").fetchone()[0]
        chat = {"questions": q[0], "days": q[1], "askers": askers}
    except sqlite3.OperationalError:
        pass  # chat never enabled on this deployment
    conn.close()
    return Data(visitors, datetime.fromisoformat(rows[0][0]),
                datetime.fromisoformat(rows[-1][0]), orphans, chat)


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
    }


def usage_lines(u: dict) -> list[str]:
    lines = [f"- **Opened the sector breakdown:** {pct(u['opened'])} of {u['n']:,} visitors."]
    if u["median_secs"] is not None:
        lines.append(f"- **Time to first click:** median {u['median_secs']:.0f} seconds after the page loaded.")
    if u["explored"] is not None:
        lines.append(f"- **Kept exploring:** {pct(u['explored'])} of those who opened it clicked again.")
    lines.append(f"- **Came back:** {pct(u['returning'])} of visitors loaded the page more than once.")
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


def render(d: Data, experiment: str, want_final: bool) -> str:
    a, b = d.arm("A"), d.arm("B")
    smaller = min(len(a), len(b))
    rule_met = smaller >= TARGET_PER_ARM or d.days >= MAX_DAYS
    if want_final and not rule_met:
        raise SystemExit(
            f"Stopping rule not met ({smaller} of {TARGET_PER_ARM} visitors in the smaller "
            f"layout, day {d.days:.1f} of {MAX_DAYS}). The final brief waits for it; "
            f"run without --final for the interim.")
    final = rule_met

    synthetic = sum(v.device == "synthetic" for v in d.visitors.values())
    health_lines, healthy = health(d)
    out = [f"# Dashboard layout test: {'decision brief' if final else 'status update'}",
           "",
           f"*Experiment `{experiment}` · data {d.first_ts:%b %-d} to {d.last_ts:%b %-d, %Y} "
           f"({d.days:.1f} days) · generated {datetime.now(timezone.utc):%b %-d, %Y} (UTC)*", ""]
    if synthetic:
        out += [f"> **Synthetic demo data.** {synthetic:,} of {len(d.visitors):,} visitors "
                "were generated by `analysis/synthetic_traffic.py` with a known, hand-set "
                "effect. This brief demonstrates the pipeline and the reporting; it is not "
                "evidence about real users.", ""]

    out += ["## The question", "",
            "Does putting the trend chart first, instead of the KPI tiles, get more people "
            "to open the breakdown by occupational sector? Opening the breakdown is the "
            "measure of whether the dashboard invites a second question. The measure, the "
            "sample size and the decision rule were fixed before any data arrived "
            "(`docs/experiment_design.md`).", ""]

    if not final:
        per_day = smaller / d.days
        remaining = TARGET_PER_ARM - smaller
        eta = min(remaining / per_day if per_day else float("inf"), MAX_DAYS - d.days)
        when = "within a day" if eta < 1 else f"in about {eta:.0f} more days"
        day = max(1, math.ceil(d.days))
        out += ["## Bottom line", "",
                ("**No decision yet, by design.** " if healthy else
                 "**Data problem: the test is not producing usable data.** ")
                + f"The test is {pct(smaller / TARGET_PER_ARM)} of the way to its planned "
                  f"sample ({smaller:,} of {TARGET_PER_ARM:,} visitors per layout, day "
                  f"{day} of at most {MAX_DAYS}). At the current pace it completes {when}.", "",
                "## Why no A-vs-B numbers yet", "",
                "Early results swing widely, and acting on the first lead turns a 5% chance "
                "of a false win into roughly 28% (measured in `analysis/simulate.py`). The "
                "comparison is sealed until the planned sample is reached, then read once.", "",
                "## Data health", "", *health_lines, "",
                "## How visitors use the dashboard (both layouts combined)", "",
                *usage_lines(usage(list(d.visitors.values()))), "",
                "Where first clicks land:", "",
                md_table(["Element", "First clicks", "Share"],
                         [list(r) for r in share_table(list(d.visitors.values()),
                                                       lambda v: v.first_target,
                                                       lambda k: TARGET_LABEL.get(k, k))]), "",
                "Devices:", "",
                md_table(["Device", "Visitors", "Share"],
                         [list(r) for r in share_table(list(d.visitors.values()),
                                                       lambda v: v.device)]), ""]
        out += chat_lines(d)
        out += ["## Next step", "",
                f"Keep collecting. The decision brief is produced once, when both layouts "
                f"reach {TARGET_PER_ARM} visitors or on day {MAX_DAYS}, whichever comes first.", ""]
        return "\n".join(out)

    # --- final ---------------------------------------------------------------
    if not healthy:
        out += ["## Bottom line", "",
                "**No decision: the data failed its integrity checks.** A broken traffic "
                "split or missing exposures can manufacture a difference on its own, so the "
                "result is withheld rather than reported. The fix is to repair the pipeline "
                "and rerun the test.", "", "## Data health", "", *health_lines, ""]
        return "\n".join(out)

    res = two_proportion_test(sum(bool(v.first_click) for v in a), len(a),
                              sum(bool(v.first_click) for v in b), len(b))
    lo, hi = res.ci
    detectable = mde(smaller, res.p_a)
    if res.significant and res.abs_lift > 0:
        decision = "Adopt layout B (chart first)."
        verdict = (f"Putting the chart first raised the share of visitors who opened the "
                   f"sector breakdown from {pct(res.p_a)} to {pct(res.p_b)}, "
                   f"a gain of {res.abs_lift * 100:.0f} points.")
    elif res.significant:
        decision = "Keep layout A (tiles first)."
        verdict = (f"Putting the chart first *lowered* the share of visitors who opened the "
                   f"sector breakdown, from {pct(res.p_a)} to {pct(res.p_b)}.")
    else:
        decision = "No change: keep layout A, the current default."
        verdict = (f"The two layouts could not be told apart: {pct(res.p_a)} of visitors "
                   f"opened the breakdown with A, {pct(res.p_b)} with B.")

    out += ["## Bottom line", "", f"**{decision}** {verdict}", "",
            "## The evidence", "",
            md_table(["", "Layout A (tiles first)", "Layout B (chart first)"],
                     [["Visitors", f"{res.n_a:,}", f"{res.n_b:,}"],
                      ["Opened the breakdown", f"{res.x_a:,}", f"{res.x_b:,}"],
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
            f"{TARGET_PER_ARM:,}.",
            "- **Audience:** visitors to a portfolio site, mostly recruiters and peers. The "
            "result describes this audience, not every dashboard user.",
            "- **What it measures:** whether people dig into the breakdown, which is "
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
    out += chat_lines(d)
    out += ["## Recommended next step", "",
            {True: "Roll out layout B, then confirm with a follow-up test that adds the "
                   "page-speed and error guardrails this one lacked.",
             False: "Keep the current layout. If the question still matters, rerun with "
                    "more traffic: detecting a difference half this size takes about four "
                    "times the visitors."}[res.significant and res.abs_lift > 0], ""]
    return "\n".join(out)


def usage_rows(ua: dict, ub: dict) -> list[list[str]]:
    def secs(u):
        return "n/a" if u["median_secs"] is None else f"{u['median_secs']:.0f} s"

    def maybe(x):
        return "n/a" if x is None else pct(x)
    return [["Opened the breakdown", pct(ua["opened"]), pct(ub["opened"])],
            ["Median time to first click", secs(ua), secs(ub)],
            ["Kept exploring (of openers)", maybe(ua["explored"]), maybe(ub["explored"])],
            ["Came back", pct(ua["returning"]), pct(ub["returning"])]]


def first_click_rows(a: list[Visitor], b: list[Visitor]) -> list[list[str]]:
    def shares(vs):
        c = Counter(v.first_target for v in vs if v.first_target)
        return c, sum(c.values())
    (ca, ta), (cb, tb) = shares(a), shares(b)
    keys = sorted(set(ca) | set(cb), key=lambda k: -(ca[k] + cb[k]))
    return [[TARGET_LABEL.get(k, k),
             pct(ca[k] / ta) if ta else "n/a", pct(cb[k] / tb) if tb else "n/a"] for k in keys]


def chat_lines(d: Data) -> list[str]:
    if not d.chat or not d.chat["questions"]:
        return []
    return ["## Demand for asking questions", "",
            f"{d.chat['questions']:,} questions over {d.chat['days']} days from "
            f"{d.chat['askers']:,} visitors, through the \"Ask about this data\" box. "
            "Questions are not stored, so this shows demand, not topics.", ""]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default="data/events.db")
    ap.add_argument("--experiment", default="exp001_layout")
    ap.add_argument("--final", action="store_true",
                    help="require the final brief; refuses if the stopping rule isn't met")
    ap.add_argument("--out", type=Path, help="write Markdown here instead of stdout")
    args = ap.parse_args()

    text = render(load(Path(args.db), args.experiment), args.experiment, args.final)
    if args.out:
        args.out.write_text(text + "\n")
        print(f"wrote {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
