"""Freeze a snapshot of Indeed Hiring Lab's US Job Postings Index for the
dashboard.

The dashboard content is part of what both arms hold constant, so it must not
change during the experiment window. Hence a snapshot pinned to one commit of
the upstream repo, committed as JSON, rather than a live fetch: if the numbers
moved mid-experiment, a reviewer could fairly ask whether the content changed
under one arm's traffic more than the other's.

Rebuild only BEFORE traffic starts (or for a new experiment):

    python data/build_snapshot.py            # pinned SHA below
    python data/build_snapshot.py --sha <commit>

Sources:
- Indeed Hiring Lab, github.com/hiring-lab/job_postings_tracker, CC BY 4.0.
  Seasonally adjusted, 7-day trailing average, Feb 1 2020 = 100.
- BLS JOLTS job openings, total nonfarm, seasonally adjusted (public domain),
  re-based to Feb 2020 = 100 so it reads on the same axis. JOLTS is monthly,
  lags about two months, and counts openings at employers rather than postings
  on one site -- a benchmark for the postings trend, not the same measure.
  BLS revises recent months; the snapshot freezes whatever was current when
  it was built, and records that date.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = "hiring-lab/job_postings_tracker"
PINNED_SHA = "9177c58cfe6d755c955180f5d94e83de96f7ad41"  # 2026-09-15 data update
OUT = Path(__file__).resolve().parent.parent / "app" / "snapshot.json"

CHART_DAYS = 364   # one year, ending on the latest observation
CHART_STEP = 7     # weekly points; daily is noise at this width
RECENT_DAYS = 28   # "recent change" window for the tiles and table

BLS_API = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
JOLTS_OPENINGS = "JTS000000000000000JOL"  # total nonfarm, SA, thousands


def fetch_csv(sha: str, path: str) -> list[dict]:
    url = f"https://raw.githubusercontent.com/{REPO}/{sha}/{path}"
    with urllib.request.urlopen(url, timeout=60) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode("utf-8"))))


def series(rows: list[dict], value_col: str) -> dict[date, float]:
    return {date.fromisoformat(r["date"]): float(r[value_col]) for r in rows}


def fetch_jolts(start: date, end: date) -> list[dict]:
    """Monthly JOLTS openings, re-based to Feb 2020 = 100. Keyless v2 access
    is rate-limited (25 requests/day), which a one-off build never approaches."""
    body = json.dumps({"seriesid": [JOLTS_OPENINGS],
                       "startyear": "2020", "endyear": str(end.year)}).encode()
    req = urllib.request.Request(BLS_API, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.load(r)
    if resp["status"] != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS API: {resp.get('message')}")
    months = {date(int(d["year"]), int(d["period"][1:]), 1): float(d["value"])
              for d in resp["Results"]["series"][0]["data"] if d["period"] != "M13"}
    base = months[date(2020, 2, 1)]
    return [{"date": m.isoformat(), "index": round(v / base * 100, 2),
             "openings_k": int(v)}
            for m, v in sorted(months.items()) if m >= start.replace(day=1)]


def summarize(s: dict[date, float], latest: date) -> dict:
    """Latest level, change over RECENT_DAYS in index points, and change vs.
    the same weekday a year earlier in percent."""
    now = s[latest]
    recent = s[latest - timedelta(days=RECENT_DAYS)]
    year_ago = s[latest - timedelta(days=364)]
    return {
        "index": round(now, 2),
        "recent_change_pts": round(now - recent, 2),
        "yoy_pct": round((now / year_ago - 1) * 100, 1),
    }


def build(sha: str) -> dict:
    agg = fetch_csv(sha, "US/aggregate_job_postings_US.csv")
    total = series([r for r in agg if r["variable"] == "total postings"],
                   "indeed_job_postings_index_SA")
    new = series([r for r in agg if r["variable"] == "new postings"],
                 "indeed_job_postings_index_SA")
    latest = max(total)

    by_sector: dict[str, dict[date, float]] = {}
    for r in fetch_csv(sha, "US/job_postings_by_sector_US.csv"):
        if r["variable"] == "total postings":
            by_sector.setdefault(r["display_name"], {})[date.fromisoformat(r["date"])] = \
                float(r["indeed_job_postings_index"])
    # summarize() raises KeyError if a sector lacks the latest date -- loud,
    # rather than quietly comparing stale numbers.
    sectors = sorted(
        ({"name": name, **summarize(s, latest)} for name, s in by_sector.items()),
        key=lambda x: x["index"], reverse=True)

    start = latest - timedelta(days=CHART_DAYS)
    trend = [{"date": d.isoformat(), "index": round(total[d], 2)}
             for d in (start + timedelta(days=i) for i in range(0, CHART_DAYS + 1, CHART_STEP))]

    jolts = fetch_jolts(start, latest)

    return {
        "built_at": datetime.now(timezone.utc).date().isoformat(),
        "jolts": jolts,
        "jolts_source": {
            "name": "BLS JOLTS, job openings, total nonfarm (SA)",
            "url": f"https://data.bls.gov/timeseries/{JOLTS_OPENINGS}",
            "note": "Monthly; re-based to Feb 2020 = 100. Public domain.",
        },
        "source": {
            "name": "Indeed Hiring Lab, Job Postings Index",
            "url": f"https://github.com/{REPO}",
            "commit": sha,
            "license": "CC BY 4.0",
            "note": "Seasonally adjusted, 7-day trailing average, Feb 1 2020 = 100.",
        },
        "as_of": latest.isoformat(),
        "recent_days": RECENT_DAYS,
        "total": summarize(total, latest),
        "new": summarize(new, latest),
        "sectors_above_baseline": sum(s["index"] > 100 for s in sectors),
        "sectors": sectors,
        "trend": trend,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sha", default=PINNED_SHA)
    snap = build(ap.parse_args().sha)
    OUT.write_text(json.dumps(snap, indent=1) + "\n")
    print(f"wrote {OUT} (as of {snap['as_of']}, {len(snap['sectors'])} sectors, "
          f"JOLTS through {snap['jolts'][-1]['date'][:7]})")


if __name__ == "__main__":
    main()
