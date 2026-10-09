# Experiment 001 — Charts first or tiles first on the Hiring Demand Monitor

**Status:** pre-registered, not yet started
**Author:** godot107
**Pre-registered on:** (fill in the date you lock this file — before any traffic)
**Data frozen at:** (fill in the monitor's "Data through" date when the experiment starts)

This document is written *before* the experiment runs and is not edited after
traffic starts. That is the whole point: everything below is a commitment made
while the outcome is still unknown, so the analysis can't be reverse-engineered
from the result. Amendments go in a dated appendix, never as an edit.

*Revision note, written before any traffic:* an earlier draft of exp001 ran on
the small Flask dashboard in `app/` (since retired) and measured opening a by-sector
breakdown. With no traffic collected, it was rewritten for the Hiring Demand
Monitor (`monitor/`), which is the product. A colour-encoding treatment was
also considered and rejected: at default settings almost every one-year move
is beyond its ±3σ limit, so the arms would have differed by one bar of twenty.

---

## 1. Hypothesis

Leading the Hiring Demand Monitor with its charts (the national trend and the
sector ranking) instead of its row of KPI tiles increases the share of visitors
who drill through to the rows behind a number.

The surface is the monitor: an interactive dashboard on Indeed Hiring Lab's
public Job Postings Index (CC BY 4.0) with a computed headline, four KPI tiles,
a national trend chart, a ranking of 47 occupational sectors, small multiples
and a sector table. Almost every mark can be clicked to open
a drill-through panel showing the records behind it, with the arithmetic.

**Why it might be true.** KPI tiles answer "what is the number?" completely and
immediately, which can end the session. A chart answers it while raising
"why?", and on this dashboard "why?" is one click away.

**Why it might be false.** Charts are slower to parse than tiles. Visitors who
came for a number and got a trend line may leave instead of digging in.

Both directions are plausible, which is the honest test for whether an
experiment is worth running.

## 2. Variants

| | Order of the dashboard page |
|---|---|
| **A (control)** | Headline · KPI tiles · trend chart + sector ranking · (everything below) |
| **B (treatment)** | Headline · trend chart + sector ranking · KPI tiles · (everything below) |

Same components, same ids, same callbacks, same data, same styling. Only the
order of two blocks differs. Screenshots of both arms on desktop and mobile:
[`variants.md`](variants.md). `test_arms_differ_only_in_section_order`
(`monitor/tests/test_experiment.py`) serializes both layouts and checks that
they contain exactly the same components; `test_each_visitor_gets_their_arms_layout`
checks that each visitor is served their own arm's order.

**Held constant in both arms:**

- *The data.* Frozen when the experiment starts. While `AB_EXPERIMENT` is set,
  `build_hiringlab.py` refuses to refresh (tested), so the daily timer is a
  no-op. The freshness check reports "frozen for experiment …" instead of
  failing, so the header's data-quality badge cannot turn red mid-window and
  change the page under both arms at once (tested).
- *The filters' defaults* (total postings, compare to 1 year, Jan 2022 to the
  latest date, top and bottom 10 sectors) and the other tabs.
- *Interactivity.* Hover, filters and drill-through work identically in both
  arms. The chart library and its bundle are the same.
- *The earnings-context panel is off in both arms.* It exists behind a flag
  (`SHOW_EARNINGS`) and stays off for the whole window; turning it on would
  add drill-through targets mid-test.

## 3. Randomization

- **Unit:** visitor, not pageview. A visitor who sees layout A must keep seeing
  layout A on every visit; mixed exposure makes the result uninterpretable.
- **Identifier:** a random first-party UUID in an HttpOnly cookie, set on first
  visit. No IP and no user agent are stored.
- **Exposure:** the layout fetch (`/_dash-layout`) that every page load makes.
  It assigns the arm, logs one `exposure` row per visitor (a unique index
  enforces it) and a `pageview` row on every load, and returns that arm's
  layout.
- **Assignment:** deterministic hash of `visitor_id + experiment_salt`, bucketed
  into 100 slots, 0-49 -> A, 50-99 -> B. Reproducible from the ID alone, with
  no lookup table. The browser never reports its arm; the server recomputes it
  from the cookie for every event.
- **Split:** 50/50. Equal allocation maximizes power per visitor.

## 4. Metrics

**Primary (the one the decision is made on — exactly one):**
- `drill_rate` = exposed visitors with at least one `drill_through`
  / exposed visitors

A `drill_through` is logged by the server when a click opens the drill-through
panel, from any source: a ranking bar, a trend point, a small multiple or a
table row. The source is stored in `target`.

**Secondary (context, never the decision):**
- repeat drill-throughs: share of drilling visitors who drilled more than once
- time from exposure to first drill-through
- time on page: median visible seconds per visitor, from `page_time` beacons
  (`monitor/app/assets/page_time.js`). It counts only time the tab is visible,
  summed across a visitor's page views. Phones can close a tab without sending
  it, so the share of visitors with any timing is reported next to it, and the
  median is used, not the mean.
- ~~filter use~~ — *not instrumented; dropped rather than claimed*

**Guardrails (a win that breaks one of these is not a win):**
- ~~Largest Contentful Paint, p75~~ and ~~client JS error rate~~ — *not
  instrumented in this build.* Both need a client beacon. Adding them means
  adding them *before* a run's traffic starts, never after.
- **Quick exits** (visible under 10 seconds, no drill-through), per arm.
  *Measured, but not a gate in this build:* it rests on a browser beacon
  with incomplete coverage, so it informs the write-up but cannot veto the
  decision.

**So this build has no guardrail that can stop a ship.** The decision rule
below is stated accordingly, and the readout must say so.

**Descriptive usage (context only; reported pooled while the test runs):**
- first drill source (ranking, trend, small multiples, table), from
  `drill_through.target`
- returning visitors: share with more than one `pageview`
- device mix: a coarse `device` class (mobile / tablet / desktop) stored on
  exposure and pageview rows; the user agent itself is never stored

Everything above is derivable from the events table as it stands (`exposure`,
`pageview`, `drill_through`, `page_time`, with timestamps, targets and device
class). `page_time` carries whole seconds, which the server bounds to 0-3600.
Synthetic demo traffic records its device as `synthetic`, so it can never pass
for real visitors in a readout.

**Diagnostic (run before looking at any metric):**
- Sample Ratio Mismatch. Chi-square on observed vs. expected 50/50 exposure
  counts. If p < 0.001, **stop and debug the pipeline** — do not analyze the
  result. An SRM means the randomizer or the logger is broken, and a broken
  randomizer can manufacture a significant result on its own.

## 5. Sample size and stopping rule

Baseline `drill_rate` assumed at 30% (a planning value; no real traffic has
been seen). Two-sided alpha = 0.05, power = 0.80, from `analysis/stats.py`:

| Relative lift to detect | Absolute | n per arm | Total visitors |
|---|---|---|---|
| 50% | +15.0 pp | 162 | 324 |
| 33% | +9.9 pp | 363 | 726 |
| 25% | +7.5 pp | 623 | 1,246 |
| 20% | +6.0 pp | 963 | 1,926 |
| 15% | +4.5 pp | 1,692 | 3,384 |
| 10% | +3.0 pp | 3,762 | 7,524 |
| 5% | +1.5 pp | 14,855 | 29,710 |

Read the other direction — what a given traffic budget can actually detect:

| n per arm | Total | Detectable lift | as relative |
|---|---|---|---|
| 100 | 200 | 19.2 pp | 64% |
| 250 | 500 | 12.0 pp | 40% |
| 500 | 1,000 | 8.4 pp | 28% |
| 1,000 | 2,000 | 5.9 pp | 20% |
| 2,500 | 5,000 | 3.7 pp | 12% |

If the real baseline is lower than 30%, every detectable lift above is larger
in relative terms. The readout states the smallest lift its sample could have
detected at the observed baseline, not at this planning value.

**The constraint this project is honest about.** A portfolio site does not get
Indeed's traffic. At a realistic few hundred visitors, this experiment can only
detect an effect so large that no real layout change would produce it. That is
not a flaw to hide — an underpowered test that reports "no significant
difference" has learned nothing, and saying so is the finding.

So the project reports two things, clearly separated:

1. **The real experiment.** Actual traffic, actual n, with the confidence
   interval and the achieved power stated up front. Probably inconclusive, and
   labelled inconclusive.
2. **The method, validated against known ground truth.** The identical analysis
   code run over simulated traffic where the true effect is set by hand. That
   shows the pipeline recovers an effect it is powered for, holds its false
   positive rate at 5% when there is no effect, and quantifies what peeking does
   to that rate.

**Stopping rule:** fixed horizon. Stop at the first of `n >= 250 per arm` or
`28 days`, and analyze *once*, at the end.

**No peeking.** Checking significance daily and stopping at the first p < 0.05
inflates the false positive rate far above the nominal 5%. `analysis/simulate.py`
measures that inflation on this exact setup rather than asserting it. If
mid-flight decisions are ever needed, the fix is an alpha-spending or sequential
test, not a bare repeated z-test.

## 6. Decision rule (committed in advance)

- **Ship B** if the `drill_rate` lift is significant at alpha = 0.05 and
  positive. (No guardrail is instrumented — see section 4. A full run should
  add the LCP and JS-error beacons first.)
- **Keep A** if significant and negative.
- **Inconclusive** otherwise — report the confidence interval and the effect
  size the test was actually powered for. Inconclusive is a real outcome and
  gets written up as one, not re-cut until something turns significant.

The analysis is `python analysis/report.py --db <db> --experiment exp001_layout`
(conversion event `drill_through`, the default).

## 7. Threats to validity, stated up front

- **Low traffic.** The dominant limitation; see section 5.
- **Position is part of the treatment.** Moving the charts up also moves two
  of the drill-through targets (ranking bars, trend points) higher on the
  page. B may win simply because those targets are easier to reach, not
  because charts invite questions. This test cannot separate the two; it
  answers "which order", not "why".
- **Screen size.** On a phone the filter panel fills the first screen, so A's
  tiles and B's charts both start at its bottom edge and the two first screens
  look almost the same ([`variants.md`](variants.md)). The treatment is
  weaker on mobile. Device class is recorded; a per-device cut is exploratory
  only.
- **Novelty effect.** Repeat visitors may react to a layout *change* rather than
  the layout. Not mitigated in this build: the share of returning visitors is
  reported, but there is no first-visit-only cut.
- **Non-representative traffic.** Visitors arriving from a LinkedIn post are
  not a random sample of anything; they skew toward recruiters and peers. The
  result generalizes to this site's audience and no further.
- **Bot traffic.** Inflates exposures with zero clicks and dilutes the effect.
  Filtered by a user-agent substring list (`BOT_MARKERS` in
  `ablab/flask_ext.py`), applied identically to both arms and declared here
  rather than tuned later. Do Not Track and Global Privacy Control are honored
  the same way: those visitors see A and are never logged.
- **Aging data.** The content is frozen, so by day 28 the "data through" date
  is a month old. Both arms see the same age on the same day.
- **Single surface, single metric.** One layout change on one dashboard. No
  claim is made about dashboards in general.

## 8. Reporting to stakeholders

`analysis/brief.py` renders the result for people who act on it (a CIO, a
product owner), in plain language: the decision first, then the evidence, then
how far to trust it.

**The A-vs-B comparison is blinded until the stopping rule is met.** While the
test runs, the brief is a status update: progress to the planned sample, data
health, and usage with both layouts pooled. No per-layout rates, no gap, no
"B is ahead". An interim scoreboard shown to decision-makers is an invitation
to stop at the first lead, which inflates a 5% false-positive rate to roughly
28% (`analysis/simulate.py`). The final brief is produced once, from the same
data as `report.py`, and `--final` refuses to run before the rule is met.

Per-layout usage cuts appear only in the final brief, labelled exploratory:
leads for the next experiment, never part of this decision.

The public status page (`/experiment`) follows the same rule: anonymous
totals with both layouts pooled until the stopping rule is met, then
descriptive per-layout rates, never a p-value. It isn't linked from the
dashboard, so the page under test doesn't change.

## 9. Scope of the proof-of-concept deployment

The design above is for a full run: 28 days or 250 visitors per arm. The EC2
proof of concept is not that run. It exists for days, not weeks, and uses a
separate experiment name, `exp001_demo`, so nothing it collects can be
mistaken for `exp001_layout`.

What the POC demonstrates, and how:

- **The live mechanism.** Sticky assignment, server-side arm attribution,
  one exposure per visitor — visible by opening the monitor in two browsers.
- **The pipeline, end to end, against known truth.**
  `analysis/synthetic_traffic.py` sends synthetic visitors with a hand-set
  effect through the monitor's real endpoints; `report.py` and `brief.py`
  then read the result. Synthetic data is labelled as such everywhere it
  appears.
- **The statistics.** `analysis/simulate.py`: false positive rate, power, and
  the cost of peeking.

It makes no claim about which layout is better. That claim needs the full run.

**The demo's own stopping rule (set 2026-10-03, before the demo was shared
publicly):** 250 visitors per layout or **7 days** from the demo's first
visitor, whichever comes first, via `AB_MAX_DAYS=7`. The live page and the
final brief unseal on that rule. The demo's data mixes labelled synthetic
traffic with real visitors, so its result illustrates the readout; it is not
evidence about which layout is better.

## Appendix A: amendments

**2026-10-09: demo stopped early.** `exp001_demo` was stopped at
2026-10-09 14:35 UTC, about 28 hours before its 7-day rule (2026-10-10 18:09
UTC). No visitor had arrived since 2026-10-07, so the remaining day was
unlikely to change the data. The final brief was rendered with
`--max-days 4`, the span of the data. Disclosure: the per-arm counts from
`/api/stats` were seen once, shortly before the decision to stop. Because
88% of the traffic was synthetic with a known effect, stopping early has no
bearing on any claim about real users. The
readout is in `docs/exp001_demo_readout.md`. This doesn't affect
`exp001_layout`, which has not run.
