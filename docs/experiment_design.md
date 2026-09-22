# Experiment 001 — Dashboard layout and engagement

**Status:** pre-registered, not yet started
**Author:** godot107
**Pre-registered on:** (fill in the date you lock this file — before any traffic)

This document is written *before* the experiment runs and is not edited after
traffic starts. That is the whole point: everything below is a commitment made
while the outcome is still unknown, so the analysis can't be reverse-engineered
from the result. Amendments go in a dated appendix, never as an edit.

---

## 1. Hypothesis

Leading a labor-market dashboard with a single headline chart, instead of a
row of KPI tiles, increases the share of visitors who open the breakdown by
occupational sector.

The surface is a US job-postings report built on Indeed Hiring Lab's public Job
Postings Index (CC BY 4.0): the national index, new postings, year-over-year
change, and a breakdown across Hiring Lab's 47 occupational sectors. The data
is real and cited, and it is the dataset the role would actually work with.

The content is a snapshot pinned to one upstream commit
(`9177c58`, data through 2026-09-11) and frozen for the whole window --
see `data/build_snapshot.py`. The numbers are something both arms hold
constant; a live feed would change them under the experiment.

**Why it might be true.** KPI tiles answer the question "what is the number?"
completely and immediately, which can end the session. A chart answers "what is
the number?" while raising "why?", which is the question a detail view exists to
serve.

**Why it might be false.** The chart is slower to parse. Visitors who wanted a
number and got a trend line may bounce instead of digging in.

Both directions are plausible, which is the honest test for whether an
experiment is worth running.

## 2. Variants

| | Layout |
|---|---|
| **A (control)** | Four KPI tiles on top (all postings, new postings, vs. a year ago, sectors above Feb 2020), 12-month chart below (Indeed postings index, with BLS JOLTS job openings as a benchmark line) |
| **B (treatment)** | 12-month index chart on top, the same four tiles below |

Identical data, identical detail view, identical styling. The layout order is
the only difference. Anything else that differs is a confound;
`test_arms_differ_only_in_block_order` renders both arms and checks it.

**Held constant in both arms, and outside the metrics:**

- *The chart library.* Plotly (basic bundle, self-hosted) with hover tooltips
  only -- zoom and pan are disabled, so the chart offers no second kind of
  interaction on the element under test. It costs ~390 KB gzipped, which both
  arms pay equally; the LCP guardrail covers it.
- *The "Ask about this data" box,* below the detail view. A single-turn Claude
  call grounded in the snapshot, capped per visitor and site-wide. No events
  are logged for it, and it is excluded from every metric. It is decided before
  traffic starts: switching it on or off mid-window would change the page under
  both arms at once, which the SRM check cannot see.

## 3. Randomization

- **Unit:** visitor, not pageview. A visitor who sees layout A must keep seeing
  layout A on every visit; mixed exposure makes the result uninterpretable.
- **Identifier:** first-party UUID, generated on first visit, stored client-side.
- **Assignment:** deterministic hash of `visitor_id + experiment_salt`, bucketed
  into 100 slots, 0-49 -> A, 50-99 -> B. Deterministic means assignment is
  reproducible from the ID alone and needs no lookup table.
- **Split:** 50/50. Equal allocation maximizes power per visitor.

## 4. Metrics

**Primary (the one the decision is made on — exactly one):**
- `detail_ctr` = visitors who opened the by-sector breakdown / visitors exposed

**Secondary (context, never the decision):**
- interactions per exposed visitor
- time from exposure to first interaction
- ~~scroll depth reaching the tiles/chart below the fold~~ — *not instrumented
  in this build; dropped rather than claimed*

**Guardrails (a win that breaks one of these is not a win):**
- ~~Largest Contentful Paint, p75~~ and ~~client JS error rate~~ — *not
  instrumented in this build.* Both need a client beacon the collector doesn't
  have yet. Adding them means adding them *before* a run's traffic starts,
  never after.
- ~~bounce rate (exposed, zero interactions)~~ — *dropped: not independent.*
  Every `interaction` follows a first `detail_click`, so "zero interactions"
  is exactly 1 − `detail_ctr`. A guardrail that is the primary metric's mirror
  image can never catch anything. A real bounce measure needs dwell time or
  scroll, which aren't collected.

**So this build has no guardrail.** The decision rule below is stated
accordingly, and the readout must say so.

**Descriptive usage (context only; reported pooled while the test runs):**
- first-click element (chart vs. each tile), from `detail_click.target`
- returning visitors: share with more than one `pageview` (logged on every
  load, separately from the once-per-visitor `exposure`)
- device mix: a coarse `device` class (mobile / tablet / desktop) stored on
  exposure and pageview rows; the user agent itself is never stored
- chat demand: question counts per day, never the questions

Everything above is derivable from the events table as it stands
(`exposure`, `pageview`, `detail_click`, `interaction`, with timestamps,
targets and device class). Synthetic demo traffic records its device as
`synthetic`, so it can never pass for real visitors in a readout.

**Diagnostic (run before looking at any metric):**
- Sample Ratio Mismatch. Chi-square on observed vs. expected 50/50 exposure
  counts. If p < 0.001, **stop and debug the pipeline** — do not analyze the
  result. An SRM means the randomizer or the logger is broken, and a broken
  randomizer can manufacture a significant result on its own.

## 5. Sample size and stopping rule

Baseline `detail_ctr` assumed at 30% (revise from real traffic once the
control-only warm-up period has run). Two-sided alpha = 0.05, power = 0.80:

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

**Stopping rule:** fixed horizon. Pre-declare the stop condition — first of
`n >= 250 per arm` or `28 days` — and analyze *once*, at the end.

**No peeking.** Checking significance daily and stopping at the first p < 0.05
inflates the false positive rate far above the nominal 5%. `analysis/simulate.py`
measures that inflation on this exact setup rather than asserting it. If
mid-flight decisions are ever needed, the fix is an alpha-spending or sequential
test, not a bare repeated z-test.

## 6. Decision rule (committed in advance)

- **Ship B** if `detail_ctr` lift is significant at alpha = 0.05 and
  positive. (No guardrail is instrumented -- see section 4. A full run should
  add the LCP and JS-error beacons first.)
- **Keep A** if significant and negative.
- **Inconclusive** otherwise — report the confidence interval and the effect
  size the test was actually powered for. Inconclusive is a real outcome and
  gets written up as one, not re-cut until something turns significant.

## 7. Threats to validity, stated up front

- **Low traffic** — the dominant limitation; see section 5.
- **Novelty effect** — repeat visitors reacting to a layout *change* rather than
  the layout. Partly mitigated by reporting first-time visitors separately.
- **Non-representative traffic** — visitors arriving from a LinkedIn post are
  not a random sample of anything; they skew toward recruiters and peers. The
  result generalizes to this site's audience and no further.
- **Bot traffic** — inflates exposures with zero clicks and dilutes the effect.
  Filtered by a user-agent substring list (`BOT_MARKERS` in `app/app.py`),
  applied identically to both arms and declared here rather than tuned later.
  No dwell-time filter: an earlier draft named one, but it isn't built.
- **Single surface, single metric** — one layout change on one dashboard. No
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

## 9. Scope of the proof-of-concept deployment

The design above is for a full run: 28 days or 250 visitors per arm. The EC2
proof of concept (`deploy/up.sh`) is not that run. It exists for days, not
weeks, and uses a separate experiment name, `exp001_demo`, so nothing it
collects can be mistaken for `exp001_layout`.

What the POC demonstrates, and how:

- **The live mechanism.** Sticky assignment, server-side arm attribution,
  one exposure per visitor -- visible by opening the site in two browsers.
- **The pipeline, end to end, against known truth.**
  `analysis/synthetic_traffic.py` sends synthetic visitors with a hand-set
  effect through the real endpoints; `report.py` then reads the result.
  Synthetic data is labelled as such everywhere it appears.
- **The statistics.** `analysis/simulate.py`: false positive rate, power, and
  the cost of peeking.

It makes no claim about which layout is better. That claim needs the full run.
