"""Shared fixtures: a synthetic dataset with the same shape as Indeed Hiring Lab's.

Tests never touch the network or the real data. The synthetic data covers the same date
range and the sector names the planted faults refer to, so every check and fault can run.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

import build_hiringlab  # noqa: E402

START, LATEST = pd.Timestamp("2020-02-01"), pd.Timestamp("2026-09-18")
SECTORS = ["Nursing", "Banking & Finance", "Software Development", "Education & Instruction"]
SERIES = ["total postings", "new postings"]


def _series(seed: int, n: int, trend: float) -> np.ndarray:
    """A smooth, seasonal-free index path starting at 100 with gentle noise."""
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    wave = 0.25 * np.sin(2 * np.pi * t / 900)                 # a multi-year cycle, like 2020–2026
    noise = np.convolve(rng.normal(0, 0.002, n), np.ones(7) / 7, mode="same")   # 7-day smoothing
    path = 100 * (1 + wave + trend * t / n + noise)
    return path / path[0] * 100                               # index: first day = 100


@pytest.fixture(scope="session")
def data() -> dict[str, pd.DataFrame]:
    dates = pd.date_range(START, LATEST, freq="D")
    n = len(dates)
    nat, sec = [], []
    for j, var in enumerate(SERIES):
        nat.append(pd.DataFrame({"date": dates, "variable": var, "idx": _series(100 + j, n, 0.02)}))
        for i, s in enumerate(SECTORS):
            sec.append(pd.DataFrame({"date": dates, "variable": var, "sector": s,
                                     "idx": _series(10 * i + j, n, 0.1 * (i - 1.5))}))
    titles = pd.DataFrame({"sector": SECTORS, "job_titles": [f"{s.lower()} title" for s in SECTORS]})
    return {"national": pd.concat(nat, ignore_index=True), "sector": pd.concat(sec, ignore_index=True),
            "titles": titles}


@pytest.fixture(scope="session")
def today() -> pd.Timestamp:
    return LATEST + pd.Timedelta(days=3)


def write_sqlite(d: dict[str, pd.DataFrame], path: Path) -> Path:
    """Write the synthetic data with the production schema (build_hiringlab.SCHEMA)."""
    con = sqlite3.connect(path)
    con.executescript(build_hiringlab.SCHEMA)
    nat = d["national"].assign(date=d["national"]["date"].dt.strftime("%Y-%m-%d"), jobcountry="US",
                               index_nsa=d["national"]["idx"])
    con.executemany("INSERT INTO postings_index VALUES (?,?,?,?,?)",
                    nat[["date", "jobcountry", "idx", "index_nsa", "variable"]].itertuples(index=False))
    sec = d["sector"].assign(date=d["sector"]["date"].dt.strftime("%Y-%m-%d"), jobcountry="US")
    con.executemany("INSERT INTO postings_by_sector VALUES (?,?,?,?,?)",
                    sec[["date", "jobcountry", "idx", "variable", "sector"]].itertuples(index=False))
    con.executemany("INSERT INTO sector_titles VALUES (?,?)", d["titles"].itertuples(index=False))
    con.commit()
    con.close()
    return path


@pytest.fixture(scope="session")
def sqlite_db(data, tmp_path_factory) -> Path:
    return write_sqlite(data, tmp_path_factory.mktemp("db") / "hiringlab.db")
