"""Reconciliation: the SQL in sql/ and the pandas in the app compute the same numbers."""
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

import dq_checks as q
import hiringlab_dashboard as hd
from conftest import write_sqlite

SQL = Path(__file__).resolve().parents[1] / "sql"


def run(db, name, **params):
    with sqlite3.connect(db) as con:
        return pd.read_sql_query((SQL / name).read_text(), con, params=params)


def test_sector_change_sql_matches_pandas(data, sqlite_db):
    m = hd.metrics(data, "total postings")
    sql = run(sqlite_db, "sector_change.sql", variable="total postings",
              latest=f"{m['latest']:%Y-%m-%d}", ref=f"{m['yago']:%Y-%m-%d}").set_index("sector")
    pd.testing.assert_series_equal(sql["pct_change"].sort_index(), m["sec"]["yoy"].sort_index(),
                                   check_names=False, rtol=1e-12)


def test_fiscal_quarter_sql_matches_pandas(data, sqlite_db):
    nat = data["national"].query("variable == 'total postings'").set_index("date")["idx"]
    py = hd.fiscal_quarters(nat, nat.index.max()).set_index("fq")["yoy"] / 100
    sql = run(sqlite_db, "fiscal_quarter_yoy.sql")
    sql.index = [f"FY{fy} Q{fq}" for fy, fq in zip(sql["fy"], sql["fq"])]
    common = py.index.intersection(sql.index)
    assert len(common) >= 7
    pd.testing.assert_series_equal(sql.loc[common, "yoy"], py.loc[common], check_names=False, rtol=1e-12)


def test_grain_sql_agrees_with_python_check(data, today, tmp_path):
    clean = run(write_sqlite(data, tmp_path / "clean.db"), "grain_check.sql")
    assert clean.empty
    broken = q.apply_fault(data, "dupes")
    # SQLite's primary key would reject duplicates, so load the broken copy without it.
    db = tmp_path / "dupes.db"
    with sqlite3.connect(db) as con:
        broken["sector"].assign(date=broken["sector"]["date"].dt.strftime("%Y-%m-%d")).to_sql(
            "postings_by_sector", con, index=False)
    sql_dupes = run(db, "grain_check.sql")
    py = next(r for r in q.run_checks(broken, today)[0] if r.name == "Grain")
    assert len(sql_dupes) == int(py.observed.split()[0]) == 50
