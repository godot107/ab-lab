"""Load Indeed's own published Hiring Lab data into hiringlab.db.

    python build_hiringlab.py

Source: github.com/hiring-lab/job_postings_tracker — the data behind
data.indeed.com. CC BY 4.0; cite Indeed Hiring Lab. Files are already in
data/; re-download with --refresh.

This is real, published Indeed data, and it has a grain trap built in:
job_postings_by_sector is one row per (date, sector, variable), where
variable is 'total postings' OR 'new postings'. Forget `variable` in a GROUP BY
and every number you produce is double-counted.
"""
from __future__ import annotations

import argparse
import os
import csv
import sqlite3
import urllib.request
from pathlib import Path

# All generated data lives in DATA_DIR (default: app/var). In Docker it's a mounted volume.
VAR = Path(os.environ.get("DATA_DIR", Path(__file__).parent / "var"))
DATA = VAR / "data"
DB = VAR / "hiringlab.db"
BASE = "https://raw.githubusercontent.com/hiring-lab/job_postings_tracker/master"
FILES = {
    "aggregate_job_postings_US.csv": f"{BASE}/US/aggregate_job_postings_US.csv",
    "job_postings_by_sector_US.csv": f"{BASE}/US/job_postings_by_sector_US.csv",
    "sector-job-title-examples.csv": f"{BASE}/sector-job-title-examples.csv",
}

SCHEMA = """
DROP TABLE IF EXISTS postings_index;
DROP TABLE IF EXISTS postings_by_sector;
DROP TABLE IF EXISTS sector_titles;

-- National index. Seasonally adjusted and not, so you can measure seasonality.
CREATE TABLE postings_index (
    date        TEXT NOT NULL,
    jobcountry  TEXT NOT NULL,
    index_sa    REAL,
    index_nsa   REAL,
    variable    TEXT NOT NULL,      -- 'total postings' | 'new postings'
    PRIMARY KEY (date, jobcountry, variable));

-- 47 sectors, daily, 2020-02-01 onward. Index = 100 at 2020-02-01 baseline.
CREATE TABLE postings_by_sector (
    date        TEXT NOT NULL,
    jobcountry  TEXT NOT NULL,
    idx         REAL,
    variable    TEXT NOT NULL,
    sector      TEXT NOT NULL,
    PRIMARY KEY (date, jobcountry, variable, sector));
CREATE INDEX ix_sector ON postings_by_sector (sector, variable, date);

CREATE TABLE sector_titles (
    sector      TEXT PRIMARY KEY,
    job_titles  TEXT);
"""


def fetch() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    for name, url in FILES.items():
        print(f"  downloading {name} ...")
        urllib.request.urlretrieve(url, DATA / name)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-download the CSVs")
    a = ap.parse_args()

    missing = [f for f in FILES if not (DATA / f).exists()]
    if a.refresh or missing:
        fetch()

    conn = sqlite3.connect(DB)
    conn.executescript(SCHEMA)

    def load(fname, sql, row):
        with open(DATA / fname, newline="", encoding="utf-8") as fh:
            conn.executemany(sql, (row(r) for r in csv.DictReader(fh)))

    def num(v):
        return float(v) if v not in ("", "NA", None) else None

    load("aggregate_job_postings_US.csv",
         "INSERT OR REPLACE INTO postings_index VALUES (?,?,?,?,?)",
         lambda r: (r["date"], r["jobcountry"],
                    num(r["indeed_job_postings_index_SA"]),
                    num(r["indeed_job_postings_index_NSA"]), r["variable"]))
    load("job_postings_by_sector_US.csv",
         "INSERT OR REPLACE INTO postings_by_sector VALUES (?,?,?,?,?)",
         lambda r: (r["date"], r["jobcountry"], num(r["indeed_job_postings_index"]),
                    r["variable"], r["display_name"]))
    load("sector-job-title-examples.csv",
         "INSERT OR REPLACE INTO sector_titles VALUES (?,?)",
         lambda r: (r["sector"], r["job_titles"]))

    conn.commit()
    c = conn.cursor()
    for t in ("postings_index", "postings_by_sector", "sector_titles"):
        print(f"  {t:<20} {c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]:>8,} rows")
    lo, hi = c.execute("SELECT MIN(date), MAX(date) FROM postings_by_sector").fetchone()
    print(f"\n  date range {lo} .. {hi}")
    print(f"  sectors    {c.execute('SELECT COUNT(DISTINCT sector) FROM postings_by_sector').fetchone()[0]}")
    conn.close()
    print(f"\nBuilt {DB}\n  Source: Indeed Hiring Lab (CC BY 4.0)")


if __name__ == "__main__":
    main()
