# ab-lab

An end-to-end A/B test on a US job-postings dashboard (Indeed Hiring Lab data). One URL serves two
layouts, assignment is sticky and deterministic, telemetry is first-party, and
the analysis is pre-registered.

Built to evidence experimentation skill for BI/analytics roles. The deliverable is the **readout**, not the
running site.

## Build / run

```bash
python3 -m venv .venv && .venv/bin/pip install -r app/requirements-dev.txt -r analysis/requirements.txt
cd app && source ../.venv/bin/activate   # own venv: anthropic is pinned
AB_DB_PATH=/tmp/ab.db COOKIE_SECURE=0 python app.py   # localhost:5000
python -m pytest -q                                    # 22 tests
```

```bash
python analysis/simulate.py                    # method vs. known ground truth
python analysis/report.py --db data/events.db  # the one-time readout
```

Deploy (EC2 POC): `deploy/up.sh` / `deploy/down.sh` -- see `docs/hosting.md`.
Locally: `cp .env.example .env`, then `docker compose up -d --build`.
Demo traffic: `python analysis/synthetic_traffic.py --url <site>` (only ever
against an `exp001_demo` deployment).

Cost scan (CloudBurn, static, no AWS credentials; also runs in CI on `infra/`
changes):

    cloudburn scan infra --config .cloudburn.yml

## Key decisions

- **Assignment lives in the app, not the proxy.** Caddy's `lb_policy cookie`
  across two containers would have been tidier, but assignment would be opaque —
  no exposure event at bucketing time, so SRM becomes undiagnosable. The app
  writes an explicit `exposure` row instead.
- **The client never reports its own variant.** `/api/events` recomputes it
  server-side from the cookie. A request body that could set the arm is a
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

- **Chat box is single-turn with hard caps** (`app/chat.py`): no history, low
  effort, `CHAT_MAX_TOKENS`, and per-visitor + site-wide daily quotas in SQLite
  (shared by both gunicorn workers). The site-wide cap is the cost ceiling. It
  logs no events and is outside the metrics; tests stub the API call.
- **Don't build on data.indeed.com's backend.** The portal reads an internal,
  undocumented GraphQL API; the published, licensed source is the
  `hiring-lab` GitHub repos.

## Constraints

- **Do not edit `docs/experiment_design.md` once traffic starts.** Amendments go
  in a dated appendix. The pre-registration is only worth something because it
  was written first.
- **Run `analysis/report.py` once**, when the stopping rule is met.
- **SRM is read before any metric.** If it fails, the experiment is void — fix
  the pipeline and rerun, don't interpret.
- Dashboard data is a frozen snapshot of Indeed Hiring Lab's public Job
  Postings Index (CC BY 4.0), pinned to one upstream commit in
  `data/build_snapshot.py` → `app/snapshot.json`. Keep the attribution and the
  "not affiliated with Indeed" line on the page. BLS JOLTS openings are frozen
  into the same snapshot as a benchmark line (monthly, lagged, openings not
  postings -- never present it as the same measure). **Never rebuild the snapshot
  once traffic starts** — the content is something both arms hold constant.
- `events.db` is real visitor data: git-ignored, and deleted after the write-up.

## Hosting

EC2 proof of concept (`infra/ec2.yaml`): one `t4g.small`, brought up for a
demo and **deleted** (not stopped) after, via `deploy/down.sh`, which exports
`events.db` first. Records under `exp001_demo`, never `exp001_layout`.
Single instance on purpose: SQLite on local disk can't sit behind a load
balancer without splitting a visitor's events across databases. RDS/ALB/ASG
are the documented scale-out path, deliberately not built. Reasoning,
costs and how to reach `events.db` in `docs/hosting.md`.
