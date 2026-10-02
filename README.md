# AB-Lab

> **Draft — work in progress.** Built and tested locally; the EC2 deployment
> has not been run yet, and no real-traffic results exist. Nothing here reports
> an experiment outcome.

An A/B test, run end to end, on a US job-postings dashboard built from
[Indeed Hiring Lab](https://github.com/hiring-lab/job_postings_tracker)'s
public Job Postings Index (CC BY 4.0): one URL, two
layouts, sticky 50/50 assignment, first-party telemetry, and a pre-registered
analysis.

The interesting part is not the traffic splitting. It is everything around it —
the hypothesis written down before the data arrives, the sample size worked out
in advance, the randomizer checked for mismatch before any metric is read, and
a null result reported as a null result.

The dashboard under test is [`monitor/`](monitor/), the **Hiring Demand
Monitor**: a Dash dashboard and data-quality suite with its own
[README](monitor/README.md). The experimentation layer is the `ablab` package
(assignment, exposure logging, the collector), which mounts on the monitor's
server. (The method was first built on a smaller Flask demo, retired once
exp001 moved to the monitor; it's in git history.)

## What it tests

**Does leading the Hiring Demand Monitor with its charts, instead of its KPI
tiles, get more people to drill through to the rows behind a number?**

| | Order of the dashboard page |
|---|---|
| **A** (control) | Headline · KPI tiles · trend chart + sector ranking · the rest |
| **B** (treatment) | Headline · trend chart + sector ranking · KPI tiles · the rest |

Same components, same data, same styling. Only the order of two blocks
differs; anything else would be a confound, and a test serializes both layouts
to check it. The conversion is a drill-through from any chart or table,
logged by the server when the drill-through panel opens.

The data is frozen while the experiment runs: the daily refresh is a no-op and
the freshness check reports "frozen" instead of turning the header red.
Independent project; not affiliated with Indeed.

Full pre-registration: [`docs/experiment_design.md`](docs/experiment_design.md).

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r monitor/requirements-dev.txt -r analysis/requirements.txt -e .
source .venv/bin/activate
python -m pytest -q ablab/ analysis/                    # 6 extension + 5 brief tests

cd monitor && python -m pytest -q                       # 37 tests
python app/build_hiringlab.py --refresh                 # download the data, first time only
AB_EXPERIMENT=exp001_demo AB_SALT=localdemo COOKIE_SECURE=0 AB_DB_PATH=/tmp/m.db \
  python app/hiringlab_app.py                           # http://localhost:8050, experiment on
```

Without `AB_EXPERIMENT` the monitor runs as a plain dashboard and logs nothing.
Both layouts, on desktop and mobile: [`docs/variants.md`](docs/variants.md).

Analysis, once the stopping rule is met:

```bash
python analysis/report.py --db data/events.db     # SRM first, then the metric
python analysis/brief.py --db data/events.db      # plain-language brief for stakeholders
python analysis/simulate.py                       # method vs. known ground truth
```

## How assignment works

`variant = hash(visitor_id + salt) % 100 < 50 ? A : B`, computed on the
server (`ablab/assignment.py`) when the page's layout is fetched.

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

## Reporting to stakeholders

`analysis/brief.py` writes the readout for decision-makers: the decision and
the evidence behind it in plain language ("a gap this large would show up
about 1 time in 116 if the layouts performed the same"), then how far to trust
it, including what wasn't measured.

While the test runs it produces only a **status update**: progress, data
health, and usage with both layouts pooled. The A-vs-B comparison stays sealed
until the stopping rule is met, because a scoreboard shown mid-test invites
stopping at the first lead -- which is how a 5% false-positive rate becomes
28%. Synthetic demo traffic is labelled as such at the top of any brief it
appears in.

See [`docs/sample_brief.md`](docs/sample_brief.md) for both stages, generated
from synthetic traffic with a known effect.

## Privacy

One first-party cookie holding a random UUID — required for sticky assignment,
and stated plainly at the foot of the page while an experiment runs, rather than claiming a "cookieless" design it
doesn't have. No PII, no IP at rest, no third-party scripts, no session replay.
Do Not Track and Global Privacy Control are honored: no cookie, no events,
control layout served. Reasoning in
[`docs/telemetry_options.md`](docs/telemetry_options.md), which also surveys
what's out there — PostHog, GrowthBook, OpenReplay, rrweb, Umami, Plausible —
and says why this one is hand-rolled.

## Deploy (EC2 proof of concept)

One EC2 instance (`t4g.small`) running Docker Compose: Caddy for automatic
HTTPS, the monitor with `ablab`, SQLite on a Docker volume. CloudFormation in
`infra/ec2.yaml`; no SSH (SSM Session Manager), code shipped via a private S3
bucket, a daily data-refresh timer that stands down while an experiment runs.
Up for a demo, deleted after.

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
monitor/      Hiring Demand Monitor — Dash dashboard, data-quality suite, SQL; the product under test, 37 tests
ablab/        installable package — assignment, event store, Flask extension (collector, cookie, stats); mounts on any Flask or Dash app
analysis/     stats.py (z-test, CI, SRM, power) · report.py · brief.py · simulate.py · synthetic_traffic.py
infra/        CloudFormation: EC2 instance, launch template, SG, IAM role, S3 bucket, refresh timer
deploy/       up.sh · down.sh
docs/         experiment_design.md · variants.md · sample_brief.md · telemetry_options.md · hosting.md
```
