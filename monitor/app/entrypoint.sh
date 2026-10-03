#!/bin/sh
# Download the public data on first start (empty volume), then serve with gunicorn.
set -eu

if [ ! -f "$DATA_DIR/hiringlab.db" ]; then
  echo "No data in $DATA_DIR yet: downloading Indeed Hiring Lab data"
  python build_hiringlab.py --refresh
fi

# No access log: it would record every visitor's IP, user agent and referrer, and the
# site promises none of those are kept. Errors are still logged.
# Each worker holds its own copy of the data (~220 MB). One worker with threads fits a
# 1 GB instance; raise WEB_WORKERS on 2 GB+.
exec gunicorn hiringlab_app:server \
  --bind 0.0.0.0:8050 \
  --workers "${WEB_WORKERS:-1}" \
  --threads "${WEB_THREADS:-4}" \
  --timeout 60 \
  --error-logfile -
