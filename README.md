# AB-Lab

An A/B test, run end to end, on a US job-postings dashboard built from
[Indeed Hiring Lab](https://github.com/hiring-lab/job_postings_tracker)'s
public Job Postings Index (CC BY 4.0): one URL, two
layouts, sticky 50/50 assignment, first-party telemetry, and a pre-registered
analysis.

The interesting part is not the traffic splitting. It is everything around it —
the hypothesis written down before the data arrives, the sample size worked out
in advance, the randomizer checked for mismatch before any metric is read, and
a null result reported as a null result.

## What it tests

**Does leading a labor-market dashboard with a trend chart, instead of KPI
tiles, get more people to open the breakdown by occupational sector?**

| | Layout |
|---|---|
| **A** (control) | KPI tiles on top — all postings, new postings, vs. a year ago, sectors above Feb 2020 — 12-month index chart below |
| **B** (treatment) | 12-month index chart on top, the same four tiles below |

Same data, same detail view, same styling. Only the order differs; anything
else would be a confound, and a test renders both arms to check it.

The chart adds BLS JOLTS job openings (monthly, public domain), re-based to
Feb 2020 = 100, as a government benchmark for the postings trend. Plotly draws
it (basic bundle, self-hosted, hover only). An optional "Ask about this data"
box sends one question at a time to Claude, grounded in the snapshot, with
per-visitor and site-wide daily caps; it is identical in both arms and outside
the metrics.

The data is a snapshot pinned to one upstream commit and frozen for the
experiment window (`python data/build_snapshot.py` rebuilds it — before traffic
starts only). Independent project; not affiliated with Indeed.

Full pre-registration: [`docs/experiment_design.md`](docs/experiment_design.md).

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r app/requirements-dev.txt -r analysis/requirements.txt
cd app && source ../.venv/bin/activate
AB_DB_PATH=/tmp/ab.db COOKIE_SECURE=0 python app.py    # http://localhost:5000
python -m pytest -q                                     # 22 tests
```

Analysis, once the stopping rule is met:

```bash
python analysis/report.py --db data/events.db     # SRM first, then the metric
python analysis/simulate.py                       # method vs. known ground truth
```

## How assignment works

`variant = hash(visitor_id + salt) % 100 < 50 ? A : B`, computed in the app.

Deterministic, so the same visitor gets the same layout forever without an
assignment table, and so assignment can be recomputed during analysis to audit
what the server did. Salted per experiment so the next test doesn't reuse this
one's split.

The proxy could have split the traffic instead, and that would have been
tidier. Doing it in the app buys the thing that matters: an **exposure event
written at the moment of bucketing**, which is what makes Sample Ratio Mismatch
diagnosable afterward.

## Why the simulation harness exists

A personal site will not produce enough traffic to detect a realistic layout
effect:

| n per arm | Total visitors | Smallest detectable lift |
|---|---|---|
| 250 | 500 | 12.0 pp (40% relative) |
| 1,000 | 2,000 | 5.9 pp (20% relative) |
| 3,762 | 7,524 | 3.0 pp (10% relative) |

So the project reports two things and never blurs them:

1. **The real experiment** — actual traffic, with achieved power stated up
   front. Likely inconclusive, and labelled inconclusive.
2. **The method, validated against known ground truth** — the same analysis
   code run over simulated traffic where the true effect is set by hand.

`analysis/simulate.py` confirms the pipeline holds its false positive rate at
5% under the null, hits 80% power where the math says it should, and quantifies
what peeking costs:

```
checks   false positive rate
     1                  5.2%
     4                 12.5%
    14                 20.3%
    28                 27.6%
```

Checking daily for a month and stopping at the first `p < 0.05` turns a 5% error
rate into 28%. That is why the stopping rule is fixed in advance and why
`/api/stats` reports counts but never a p-value.

That harness has already earned its place: it caught a missing factor of 2 in
the sample-size formula, which had every power calculation understating the
required traffic by half.

## Privacy

One first-party cookie holding a random UUID — required for sticky assignment,
and stated plainly on the page rather than claiming a "cookieless" design it
doesn't have. No PII, no IP at rest, no third-party scripts, no session replay.
Do Not Track and Global Privacy Control are honored: no cookie, no events,
control layout served. Reasoning in
[`docs/telemetry_options.md`](docs/telemetry_options.md), which also surveys
what's out there — PostHog, GrowthBook, OpenReplay, rrweb, Umami, Plausible —
and says why this one is hand-rolled.

## Deploy (EC2 proof of concept)

One EC2 instance (`t4g.small`) running Docker Compose: Caddy for automatic
HTTPS, the Flask app, SQLite on a Docker volume. CloudFormation in
`infra/ec2.yaml`; no SSH (SSM Session Manager), code shipped via a private S3
bucket, the Anthropic key in Parameter Store. Up for a demo, deleted after.

```bash
deploy/up.sh                    # create/update the stack and deploy; prints the URL
python analysis/synthetic_traffic.py --url <site> --visitors 600   # known-truth traffic
deploy/down.sh                  # export events.db to data/, then delete everything
python analysis/report.py --db data/events-ab-lab-*.db --experiment exp001_demo
```

The POC records under `exp001_demo`, never the pre-registered `exp001_layout`.
Cost, how to pull `events.db` mid-demo, and the scale-out path (ALB, Auto
Scaling, shared store -- and why it's single-instance today) are in
[`docs/hosting.md`](docs/hosting.md).

Infrastructure is cost-scanned statically with
[CloudBurn](https://www.npmjs.com/package/cloudburn) -- no AWS credentials,
runs in CI on any change under `infra/`:

```bash
cloudburn scan infra --config .cloudburn.yml
```

## Layout

```
app/          Flask app — assignment, collector, both layouts, chat endpoint, 22 tests
analysis/     stats.py (z-test, CI, SRM, power) · report.py · simulate.py · synthetic_traffic.py
infra/        CloudFormation: EC2 instance, launch template, SG, IAM role, S3 bucket
deploy/       up.sh · down.sh
data/         build_snapshot.py — freezes the Hiring Lab + BLS JOLTS snapshot
docs/         experiment_design.md · telemetry_options.md · hosting.md
```
