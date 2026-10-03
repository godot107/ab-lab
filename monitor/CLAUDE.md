# monitor (Hiring Demand Monitor)

Part of ab-lab (merged from the former hiring-demand-monitor repo): an interactive Plotly Dash dashboard + data-quality suite on Indeed
Hiring Lab's public Job Postings Index, deployed to AWS EC2. See README.md for the full picture.

## Run / test

    source ../.venv/bin/activate             # ab-lab venv (shared with the experiment)
    pip install -r requirements-dev.txt -e ..   # -e ..: the ablab package
    python app/build_hiringlab.py --refresh  # data → app/var/ (git-ignored; DATA_DIR overrides)
    python app/hiringlab_app.py [--dev]      # http://127.0.0.1:8050
    python -m pytest                         # synthetic data, no network

## Key decisions / constraints

- **Public repo.** Never add personal notes, job-application material, names of private
  individuals, or credentials.
- **Every claim must be sourced.** Recruit figures come from earnings-call transcripts (linked in
  app/references.py); mark third-party or unverified facts as such.
- The index is **not a revenue proxy**: it's ARPJ's denominator only.
- Data paths all resolve from `DATA_DIR` (default `app/var/`). In Docker it's the `/data` volume.
- The app loads data once at startup: restart after code or data changes (or use `--dev`).
- pandas >= 3.0.6 (3.0.4 segfaults with numpy 2.4). Dash 4: dropdowns need `options` at load or the
  value is cleared to None.
- Deploy is ab-lab's, at the repo root (`deploy/up.sh`, `infra/ec2.yaml`, root `compose.yaml`);
  this folder has no deploy files of its own.
- **Experiments (ablab).** Off unless `AB_EXPERIMENT` is set (then `AB_SALT` is required and never
  changes mid-test). Exposure = the `/_dash-layout` fetch, which recomputes the arm from the
  cookie, and picks `LAYOUTS[arm]` (exp001: arm B puts the chart grid above the KPI tiles; nothing
  else differs). Drill-throughs are logged server-side in `on_click` as the conversion.
  While an experiment is live, `build_hiringlab.py` refuses to refresh. The image gets `ablab` via a
  compose `additional_contexts` (`ABLAB_DIR`, default `../ablab`).
- **Earnings panel is off by default** (`SHOW_EARNINGS=1` turns it on): the card relating the index
  to Recruit Holdings' (Indeed's parent) reported earnings, its drill-through, the About-tab section
  and footer lines. Kept out of the public page and README on purpose; don't re-advertise it. If it
  ever comes back, it's a separate experiment (exp002), never a difference between exp001's arms.
