# ab-lab

An end-to-end A/B test on the Hiring Demand Monitor, a US job-postings dashboard
(Indeed Hiring Lab data). One URL serves two layouts, assignment is sticky and
deterministic, telemetry is first-party, and the analysis is pre-registered.

Built to evidence experimentation skill for BI/analytics roles. **Status:
draft / work in progress.** The deliverable is the **readout**, not the
running site.

- `monitor/`: the Hiring Demand Monitor (Dash dashboard + data-quality suite;
  merged in from its own repo). The product under test. See `monitor/CLAUDE.md`.
- `ablab/`: the experimentation layer (assignment, event store, collector),
  installed with `pip install -e .` and mounted on the monitor's Flask server.
- `analysis/`: stats, the readout, the stakeholder brief, simulation and
  synthetic traffic. `infra/` + `deploy/`: the one EC2 deploy.
- The original Flask demo (`app/`) and its chat box were retired once exp001
  moved to the monitor; they're in git history.

## Build / run

```bash
python3 -m venv .venv && .venv/bin/pip install -r monitor/requirements-dev.txt -r analysis/requirements.txt -e .
source .venv/bin/activate
python -m pytest -q ablab analysis                     # 6 + 5 tests
cd monitor && python -m pytest -q                      # 36 tests
python app/build_hiringlab.py --refresh                # data, first time only
AB_EXPERIMENT=exp001_demo AB_SALT=localdemo COOKIE_SECURE=0 AB_DB_PATH=/tmp/m.db \
  python app/hiringlab_app.py                          # localhost:8050, experiment on
```

```bash
python analysis/simulate.py                    # method vs. known ground truth
python analysis/report.py --db data/events.db  # the one-time readout
python analysis/brief.py --db data/events.db   # stakeholder brief (blinded until the rule is met)
```

Deploy (EC2 POC): `deploy/up.sh` / `deploy/down.sh` -- see `docs/hosting.md`.
Locally: `cp .env.example .env`, then `docker compose up -d --build`.
Demo traffic: `python analysis/synthetic_traffic.py --url <site>` (only ever
against an `exp001_demo` deployment).

CI (`.github/workflows/ci.yml`): all tests, cfn-lint, ShellCheck, image build.
Cost scan (CloudBurn, static, no AWS credentials; also runs in CI on `infra/`
changes):

    cloudburn scan infra --config .cloudburn.yml

## Key decisions

- **Assignment lives in the app, not the proxy.** Caddy's `lb_policy cookie`
  across two containers would have been tidier, but assignment would be opaque —
  no exposure event at bucketing time, so SRM becomes undiagnosable. The app
  writes an explicit `exposure` row instead.
- **The client never reports its own variant.** Every event (the monitor's
  callbacks, `/api/events`) recomputes it server-side from the cookie. A request body that could set the arm is a
  request body that can skew the result.
- **One exposure per visitor is enforced by a partial unique index**, not by
  application code. A double-counted exposure silently corrupts the denominator
  of every rate metric and SRM won't necessarily catch it.
- **`/api/stats` returns counts but never a p-value.** Peeking must not be one
  click away; `analysis/simulate.py` shows 28 daily checks turn a 5% false
  positive rate into 28%.
- **Underpowered by construction, and it says so.** At realistic traffic the
  MDE is ~12 pp. The real experiment will likely be inconclusive; the
  simulation harness is what demonstrates the method is sound anyway. Never
  present a null result here as "no effect".
- **Hand-rolled collector, not PostHog/GrowthBook.** The claim is experimental
  design, not platform installation — but name the platforms as the production
  answer. See `docs/telemetry_options.md`.
- **No session replay / mouse tracking.** Primarily a multiple-comparisons
  hazard, secondarily a consent burden. If ever added: pre-registered as
  secondary, excluded from the decision rule.

- **`AB_SALT` is written once by the bootstrap and never regenerated.**
  Changing it mid-experiment re-buckets every returning visitor, which destroys
  sticky assignment without any visible error. The template guards this with an
  if-not-exists; don't "helpfully" rotate it.

- **Don't build on data.indeed.com's backend.** The portal reads an internal,
  undocumented GraphQL API; the published, licensed source is the
  `hiring-lab` GitHub repos.

## Constraints

- **The data is frozen while an experiment runs.** With `AB_EXPERIMENT` set,
  the EC2 refresh timer and `build_hiringlab.py` both refuse to refresh, and
  the freshness check reports "frozen" instead of turning the header red. The
  content is something both arms hold constant. Refreshing between
  experiments is fine.
- **Do not edit `docs/experiment_design.md` once traffic starts.** Amendments go
  in a dated appendix. The pre-registration is only worth something because it
  was written first.
- **Run `analysis/report.py` once**, when the stopping rule is met.
- **Never show stakeholders an A-vs-B comparison mid-test.** `analysis/brief.py`
  is blinded until the stopping rule is met (pooled usage only); don't add an
  override. Per-layout usage cuts are exploratory, final brief only.
- **SRM is read before any metric.** If it fails, the experiment is void — fix
  the pipeline and rerun, don't interpret.
- Dashboard data is Indeed Hiring Lab's public Job Postings Index (CC BY 4.0).
  Keep the attribution and the "not affiliated with Indeed" line on the page.
- `events.db` is real visitor data: git-ignored, and deleted after the write-up.

## Hosting

EC2 proof of concept (`infra/ec2.yaml`): one `t4g.small`, brought up for a
demo and **deleted** (not stopped) after, via `deploy/down.sh`, which exports
`events.db` first. Records under `exp001_demo`, never `exp001_layout`.
Single instance on purpose: SQLite on local disk can't sit behind a load
balancer without splitting a visitor's events across databases. RDS/ALB/ASG
are the documented scale-out path, deliberately not built. So are an Elastic
IP and deploy-time domain config: the demo uses the auto-assigned IP and picks
`AB_DOMAIN` before the first `up.sh`, because changing it later replaces the
instance (new IP, salt and data). Reasoning,
costs and how to reach `events.db` in `docs/hosting.md`.
