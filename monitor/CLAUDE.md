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
- Deploy: `deploy/0*.sh` (EC2 via CloudFormation, Caddy HTTPS). Lint before pushing:
  `cfn-lint infra/ec2.yaml` and ShellCheck (`docker run --rm -v "$PWD:/mnt" -w /mnt koalaman/shellcheck:stable -x -P deploy deploy/*.sh app/entrypoint.sh`).
- **Experiments (ablab).** Off unless `AB_EXPERIMENT` is set (then `AB_SALT` is required and never
  changes mid-test). Exposure = the `/_dash-layout` fetch, which recomputes the arm from the
  cookie, and picks `LAYOUTS[arm]` (exp001: arm B puts the chart grid above the KPI tiles; nothing
  else differs). Drill-throughs are logged server-side in `on_click` as the conversion.
  While an experiment is live, `build_hiringlab.py` refuses to refresh. The image gets `ablab` via a
  compose `additional_contexts` (`ABLAB_DIR`, default `../ablab`).
