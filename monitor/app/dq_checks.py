"""Data-quality checks for the Hiring Lab data: Pydantic row contracts plus dataset checks.

    python dq_checks.py                  # run on the real data, print results
    python dq_checks.py --fault spike    # run on a copy with a planted fault

Two layers, because they answer different questions:

  RECORD LEVEL (Pydantic)   Does every row satisfy its contract? Types, allowed
                            values, bounds, no NaN. Pydantic reports the exact row
                            and field of each failure.
  DATASET LEVEL (functions) Things no single row can tell you: duplicate keys,
                            freshness, missing days, a sector dropping out, a
                            reading far outside its normal variation.

Every check has a DMBOK quality dimension and a severity. "block" means the numbers
can't be trusted; "warn" means review, still usable. Results are typed CheckResult
models carrying up to 50 evidence rows, so the dashboard can drill into them.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

# All generated data lives in DATA_DIR (default: app/var). In Docker it's a mounted volume.
VAR = Path(os.environ.get("DATA_DIR", Path(__file__).parent / "var"))
HISTORY_DB = VAR / "dq_history.db"      # separate file: build_hiringlab.py rebuilds hiringlab.db

Dimension = Literal["Validity", "Uniqueness", "Completeness", "Timeliness", "Consistency", "Reasonableness"]
Severity = Literal["block", "warn"]
Series = Literal["total postings", "new postings"]

FRESHNESS_DAYS = 14                       # source refreshes weekly; two missed refreshes = stale
CONTROL_YEARS, CONTROL_WINDOW, Z = 3, 29, 3
EVIDENCE_ROWS = 50


# ---------------------------------------------------------------------------
# Record-level contracts (the published grain, written down)
# ---------------------------------------------------------------------------
class SectorRow(BaseModel):
    """One row per date × sector × series. idx is an index (Feb 1, 2020 = 100)."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    date: date
    variable: Series
    sector: str = Field(min_length=1)
    idx: float = Field(gt=0, lt=1000, allow_inf_nan=False)


class NationalRow(BaseModel):
    """One row per date × series, national."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    date: date
    variable: Series
    idx: float = Field(gt=0, lt=1000, allow_inf_nan=False)


CONTRACTS = {"sector": (SectorRow, ["date", "variable", "sector", "idx"]),
             "national": (NationalRow, ["date", "variable", "idx"])}


class CheckResult(BaseModel):
    name: str
    dimension: Dimension
    severity: Severity
    passed: bool
    observed: str
    expected: str
    detail: str
    rows_affected: int = 0
    evidence: list[dict] = Field(default_factory=list)

    @property
    def status(self) -> str:
        return "pass" if self.passed else ("warn" if self.severity == "warn" else "fail")

    def legacy(self) -> dict:
        """The shape hiringlab_dashboard.dq_strip expects."""
        return {"name": self.name, "ok": self.passed, "detail": self.detail, "severity": self.severity}


def _records(df: pd.DataFrame, cols: list[str]) -> list[dict]:
    out = df[cols].copy()
    out["date"] = out["date"].dt.date
    return out.to_dict("records")


def contract_check(name: str, table: str, df: pd.DataFrame) -> tuple[CheckResult, pd.DataFrame]:
    """Validate every row with Pydantic. Returns the result and a per-error frame."""
    model, cols = CONTRACTS[table]
    recs = _records(df, cols)
    try:
        TypeAdapter(list[model]).validate_python(recs)
        errors = []
    except ValidationError as e:
        errors = e.errors(include_url=False)
    err = pd.DataFrame([{"row": x["loc"][0], "field": x["loc"][1] if len(x["loc"]) > 1 else "",
                         "error": x["type"], "message": x["msg"], "value": repr(x.get("input"))[:40]}
                        for x in errors])
    bad_rows = err["row"].nunique() if len(err) else 0
    evidence = []
    if len(err):
        for r in err.head(EVIDENCE_ROWS).itertuples():
            rec = recs[r.row]
            evidence.append({"row": r.row, "field": r.field, "error": r.error, "value": r.value,
                             **{k: str(v) for k, v in rec.items() if k != r.field}})
    rules = ", ".join(f"{f}: {info.annotation.__name__ if hasattr(info.annotation, '__name__') else info.annotation}"
                      for f, info in model.model_fields.items())
    return CheckResult(
        name=name, dimension="Validity", severity="block", passed=bad_rows == 0,
        observed=f"{bad_rows:,} of {len(recs):,} rows fail",
        expected="0 rows fail the contract",
        detail=f"Pydantic {model.__name__} contract ({rules})",
        rows_affected=int(bad_rows), evidence=evidence), err


def contract_rules() -> list[dict]:
    """Human-readable contract: field, type, rule. Shown on the DQ page."""
    rules = []
    for table, (model, _) in CONTRACTS.items():
        for f, info in model.model_fields.items():
            ann = str(info.annotation).replace("typing.", "").replace("<class '", "").replace("'>", "")
            meta = ", ".join(str(m) for m in info.metadata) or "—"
            rules.append({"table": table, "field": f, "type": ann, "rule": meta})
    return rules


# ---------------------------------------------------------------------------
# Control limits (shared with the dashboard's normal-variation band)
# ---------------------------------------------------------------------------
def control_band(s: pd.Series, latest: pd.Timestamp) -> tuple[pd.Series, float]:
    """Centered rolling trend and σ of the readings' wobble around it (last N years)."""
    roll = dict(window=CONTROL_WINDOW, center=True, min_periods=CONTROL_WINDOW // 2 + 1)
    trend = s.rolling(**roll).mean()
    since = latest - pd.DateOffset(years=CONTROL_YEARS)
    return trend, float((s / trend - 1).loc[since:].std())


# ---------------------------------------------------------------------------
# Dataset-level checks
# ---------------------------------------------------------------------------
def _ev(df: pd.DataFrame) -> list[dict]:
    out = df.head(EVIDENCE_ROWS).copy()
    for c in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[c]):
            out[c] = out[c].dt.strftime("%Y-%m-%d")
    return out.astype(object).where(out.notna(), None).to_dict("records")


def check_grain(d: dict, today) -> CheckResult:
    key = ["date", "sector", "variable"]
    dup = d["sector"][d["sector"].duplicated(key, keep=False)].sort_values(key)
    n = int(dup.duplicated(key).sum())
    return CheckResult(name="Grain", dimension="Uniqueness", severity="block", passed=n == 0,
                       observed=f"{n:,} duplicate keys", expected="0 (one row per date × sector × series)",
                       detail="Forget `variable` in a GROUP BY, or load a file twice, and every number double-counts.",
                       rows_affected=len(dup), evidence=_ev(dup))


def check_freshness(d: dict, today) -> CheckResult:
    latest = d["national"]["date"].max()
    age = (today - latest).days
    return CheckResult(name="Freshness", dimension="Timeliness", severity="block", passed=age <= FRESHNESS_DAYS,
                       observed=f"{age} days old (data through {latest:%b %d, %Y})",
                       expected=f"≤ {FRESHNESS_DAYS} days",
                       detail="Hiring Lab refreshes weekly; two missed refreshes means the pipeline is stuck.",
                       evidence=[{"latest_date": f"{latest:%Y-%m-%d}", "today": f"{today:%Y-%m-%d}", "age_days": age}])


def check_completeness(d: dict, today) -> CheckResult:
    sec = d["sector"]
    latest = d["national"]["date"].max()
    series = [v for v in Series.__args__]                    # unknown names are the contract's job
    expected = pd.MultiIndex.from_product([sorted(sec["sector"].unique()), series],
                                          names=["sector", "variable"])
    have = pd.MultiIndex.from_frame(sec.loc[sec["date"] == latest, ["sector", "variable"]].drop_duplicates())
    missing = expected.difference(have).to_frame(index=False)
    missing.insert(0, "date", latest)
    return CheckResult(name="Completeness at latest date", dimension="Completeness", severity="block",
                       passed=missing.empty, observed=f"{len(missing)} sector × series missing",
                       expected=f"all {len(expected)} present on {latest:%b %d, %Y}",
                       detail="A sector that silently drops out shrinks every total and ranking.",
                       rows_affected=len(missing), evidence=_ev(missing))


def check_continuity(d: dict, today) -> CheckResult:
    rows = []
    for var, g in d["national"].groupby("variable"):
        full = pd.date_range(g["date"].min(), g["date"].max(), freq="D")
        for m in full.difference(pd.DatetimeIndex(g["date"])):
            rows.append({"date": m, "table": "national", "variable": var})
    miss = pd.DataFrame(rows)
    return CheckResult(name="Continuity", dimension="Completeness", severity="block", passed=miss.empty,
                       observed=f"{len(miss)} missing days", expected="0 gaps in the daily series",
                       detail="A missing day breaks trailing averages and year-ago lookups.",
                       rows_affected=len(miss), evidence=_ev(miss) if len(miss) else [])


def check_extremes(d: dict, today) -> CheckResult:
    """Name WHERE the extremes are so a person can judge them (Dental, Apr 10 2020 is real)."""
    sec = d["sector"].dropna(subset=["idx"])
    ext = pd.concat([sec.nsmallest(3, "idx"), sec.nlargest(3, "idx")])
    lo, hi = sec["idx"].min(), sec["idx"].max()
    return CheckResult(name="Extremes named", dimension="Validity", severity="block",
                       passed=bool(0 < lo and hi < 1000),
                       observed=f"min {lo:.1f} · max {hi:.1f}", expected="0 < index < 1000",
                       detail="Lowest is Dental new postings on Apr 10, 2020 (lockdown): real, not an error. "
                              "Reported, not suppressed.", evidence=_ev(ext))


def check_comparison_dates(d: dict, today) -> CheckResult:
    latest = d["national"]["date"].max()
    need = {"1 year ago": latest - pd.DateOffset(years=1)} | \
           {f"{w} weeks ago": latest - pd.Timedelta(weeks=w) for w in (4, 13, 26)}
    have = set(d["national"]["date"]) & set(d["sector"]["date"])
    rows = [{"comparison": k, "date": v, "present": v in have} for k, v in need.items()]
    miss = [r for r in rows if not r["present"]]
    return CheckResult(name="Comparison dates", dimension="Completeness", severity="block", passed=not miss,
                       observed=f"{len(rows) - len(miss)} of {len(rows)} present",
                       expected="every date the Compare-to slider needs exists in both tables",
                       detail="Without the reference date, a % change silently compares to the wrong day.",
                       rows_affected=len(miss), evidence=_ev(pd.DataFrame(rows)))


def check_lookup(d: dict, today) -> CheckResult:
    missing = sorted(set(d["sector"]["sector"]) - set(d["titles"].dropna()["sector"]))
    return CheckResult(name="Title lookup coverage", dimension="Consistency", severity="warn", passed=not missing,
                       observed=f"{len(missing)} sectors without example titles", expected="every sector in the lookup",
                       detail="Degrades a label, not a number, so it's a warning.",
                       rows_affected=len(missing), evidence=[{"sector": s} for s in missing])


def check_alignment(d: dict, today) -> CheckResult:
    n_max, s_max = d["national"]["date"].max(), d["sector"]["date"].max()
    ok = n_max == s_max
    return CheckResult(name="Tables aligned", dimension="Consistency", severity="warn", passed=ok,
                       observed=f"national through {n_max:%Y-%m-%d}, sectors through {s_max:%Y-%m-%d}",
                       expected="same latest date",
                       detail="If one table lags, national vs sector comparisons mix two different days.",
                       evidence=[{"table": "national", "latest": f"{n_max:%Y-%m-%d}"},
                                 {"table": "sector", "latest": f"{s_max:%Y-%m-%d}"}])


def check_reasonableness(d: dict, today) -> CheckResult:
    """Individuals / moving-range style control check [DQAF p.51]: is the latest
    day-over-day change within ±3σ of the series' own day-over-day changes (last 3 years)?
    Day-over-day avoids the lag a trend line has at the end of a trending series."""
    latest = d["national"]["date"].max()
    since = latest - pd.DateOffset(years=CONTROL_YEARS)
    frames = [("National", v, g.set_index("date")["idx"]) for v, g in d["national"].groupby("variable")]
    frames += [(sec, v, g.set_index("date")["idx"]) for (sec, v), g in d["sector"].groupby(["sector", "variable"])]
    rows = []
    for name, var, s in frames:
        s = s[~s.index.duplicated()].sort_index()      # duplicates are the Grain check's job
        r = s.pct_change()
        sigma = r.loc[since:latest - pd.Timedelta(days=1)].std()
        if latest not in r.index or not sigma or pd.isna(r[latest]):
            continue
        z = r[latest] / sigma
        if abs(z) > Z:
            rows.append({"series": name, "variable": var, "date": latest,
                         "value": round(s[latest], 2), "previous": round(s.shift(1)[latest], 2),
                         "day_change_pct": round(r[latest] * 100, 2), "sigma_pct": round(sigma * 100, 3),
                         "z": round(z, 1)})
    out = pd.DataFrame(rows).sort_values("z", key=abs, ascending=False) if rows else pd.DataFrame()
    return CheckResult(name="Latest move within control limits", dimension="Reasonableness", severity="warn",
                       passed=not rows, observed=f"{len(rows)} series outside ±{Z}σ",
                       expected=f"latest day-over-day change within ±{Z}σ of its own history",
                       detail="A special-cause signal: real news or a bad load. Worth a look before anyone "
                              "quotes the number.",
                       rows_affected=len(rows), evidence=_ev(out) if rows else [])


def daily_control(s: pd.Series, latest: pd.Timestamp) -> tuple[pd.Series, float]:
    """Day-over-day % change and its σ over the control period, for the control chart."""
    s = s[~s.index.duplicated()].sort_index()
    r = s.pct_change()
    return r, float(r.loc[latest - pd.DateOffset(years=CONTROL_YEARS):latest - pd.Timedelta(days=1)].std())


DATASET_CHECKS: list[Callable[[dict, pd.Timestamp], CheckResult]] = [
    check_grain, check_freshness, check_completeness, check_continuity, check_extremes,
    check_comparison_dates, check_alignment, check_lookup, check_reasonableness]


def run_checks(d: dict, today: pd.Timestamp | None = None) -> tuple[list[CheckResult], dict[str, pd.DataFrame]]:
    today = today or pd.Timestamp(datetime.now().date())
    results, errors = [], {}
    for name, table in [("Contract: sector rows", "sector"), ("Contract: national rows", "national")]:
        r, err = contract_check(name, table, d[table])
        results.append(r)
        errors[table] = err
    results += [fn(d, today) for fn in DATASET_CHECKS]
    return results, errors


# ---------------------------------------------------------------------------
# Planted faults: prove the checks catch what they claim to
# ---------------------------------------------------------------------------
def _copy(d: dict) -> dict:
    return {k: v.copy() for k, v in d.items()}


def _dupes(d):
    d = _copy(d); d["sector"] = pd.concat([d["sector"], d["sector"].tail(50)]); return d


def _stale(d):
    d = _copy(d); cut = d["national"]["date"].max() - pd.Timedelta(days=20)
    d["national"] = d["national"][d["national"]["date"] <= cut]
    d["sector"] = d["sector"][d["sector"]["date"] <= cut]; return d


def _dropout(d):
    d = _copy(d); s = d["sector"]; latest = s["date"].max()
    d["sector"] = s[~((s["sector"] == "Nursing") & (s["date"] == latest))]; return d


def _gap(d):
    d = _copy(d); d["national"] = d["national"][d["national"]["date"] != pd.Timestamp("2025-03-15")]; return d


def _bad_values(d):
    d = _copy(d); s = d["sector"].copy(); idx = s.index[-5:]
    s.loc[idx[:2], "idx"] = np.nan; s.loc[idx[2:4], "idx"] = -12.0; s.loc[idx[4], "variable"] = "all postings"
    d["sector"] = s; return d


def _spike(d):
    d = _copy(d); s = d["sector"].copy(); latest = s["date"].max()
    m = (s["sector"] == "Banking & Finance") & (s["date"] == latest) & (s["variable"] == "total postings")
    s.loc[m, "idx"] = s.loc[m, "idx"] * 1.6; d["sector"] = s; return d


FAULTS: dict[str, tuple[str, Callable[[dict], dict] | None]] = {
    "none":       ("Real data (no fault)", None),
    "dupes":      ("Duplicate load: last 50 rows loaded twice", _dupes),
    "stale":      ("Stale pipeline: last 20 days never arrived", _stale),
    "dropout":    ("Sector drop-out: Nursing missing on the latest date", _dropout),
    "gap":        ("Missing day: Mar 15, 2025 gone from the national table", _gap),
    "bad_values": ("Bad values: NaN, negative index, an unknown series name", _bad_values),
    "spike":      ("Spike: Banking & Finance latest reading ×1.6", _spike),
}


def apply_fault(d: dict, fault: str) -> dict:
    fn = FAULTS[fault][1]
    return fn(d) if fn else d


# ---------------------------------------------------------------------------
# History (DQAF: measurement results over time)
# ---------------------------------------------------------------------------
def record_history(results: list[CheckResult], data_through: pd.Timestamp) -> None:
    with sqlite3.connect(HISTORY_DB) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS dq_results (
            run_at TEXT, data_through TEXT, name TEXT, dimension TEXT, severity TEXT,
            passed INTEGER, observed TEXT, rows_affected INTEGER)""")
        run_at = datetime.now().isoformat(timespec="seconds")
        con.executemany("INSERT INTO dq_results VALUES (?,?,?,?,?,?,?,?)",
                        [(run_at, f"{data_through:%Y-%m-%d}", r.name, r.dimension, r.severity,
                          int(r.passed), r.observed, r.rows_affected) for r in results])


def load_history() -> pd.DataFrame:
    if not HISTORY_DB.exists():
        return pd.DataFrame()
    with sqlite3.connect(HISTORY_DB) as con:
        return pd.read_sql_query("SELECT * FROM dq_results ORDER BY run_at", con)


if __name__ == "__main__":
    import hiringlab_dashboard as hd
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fault", choices=list(FAULTS), default="none")
    args = ap.parse_args()
    con = sqlite3.connect(hd.DB)
    data = apply_fault(hd.load(con), args.fault)
    con.close()
    res, _ = run_checks(data)
    print(f"Fault: {FAULTS[args.fault][0]}")
    for r in res:
        mark = {"pass": "✓", "warn": "⚠", "fail": "✗"}[r.status]
        print(f"  {mark} {r.name:<38} {r.dimension:<15} {r.severity:<5} {r.observed}")
