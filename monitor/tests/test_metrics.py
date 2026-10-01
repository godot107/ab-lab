"""The math behind the dashboard's claims."""
import numpy as np
import pandas as pd
import pytest

import dq_checks as q
import hiringlab_dashboard as hd


def test_index_growth_does_not_depend_on_the_base(data):
    """I_t / I_s = N_t / N_s: rebasing an index never changes its growth rates."""
    s = data["sector"].query("sector == 'Nursing' and variable == 'total postings'").set_index("date")["idx"]
    rebased = s / s.loc["2023-06-01"] * 100               # a different origin
    a, b = pd.Timestamp("2026-09-18"), pd.Timestamp("2025-09-18")
    assert s[a] / s[b] == pytest.approx(rebased[a] / rebased[b], rel=1e-12)


def test_yoy_is_the_ratio_of_two_published_rows(data):
    m = hd.metrics(data, "total postings")
    wide = m["wide"]
    for sector, row in m["sec"].iterrows():
        assert row["yoy"] == pytest.approx(wide.loc[m["latest"], sector] / wide.loc[m["yago"], sector] - 1)


@pytest.mark.parametrize("r", [r for r in hd.RECRUIT if r["revenue"] is not None], ids=lambda r: r["fq"])
def test_revenue_equals_postings_times_arpj(r):
    """(1 + g_rev) = (1 + g_postings)(1 + g_ARPJ), within Recruit's rounding."""
    implied = ((1 + r["postings"] / 100) * (1 + r["arpj"] / 100) - 1) * 100
    assert implied == pytest.approx(r["revenue"], abs=0.6)


@pytest.mark.parametrize("day, label", [
    ("2025-04-01", "FY2025 Q1"), ("2025-06-30", "FY2025 Q1"), ("2025-07-01", "FY2025 Q2"),
    ("2025-12-31", "FY2025 Q3"), ("2026-01-01", "FY2025 Q4"), ("2026-03-31", "FY2025 Q4"),
])
def test_recruit_fiscal_quarters_start_in_april(day, label):
    d = pd.Timestamp(day)
    s = pd.Series(100.0, index=pd.date_range(d - pd.DateOffset(years=2), d, freq="D"))
    fq = hd.fiscal_quarters(s, d)
    assert fq["fq"].iloc[-1] == label


def test_control_band_limit(data):
    """σ is the wobble around the series' own trend; the change limit is 3·√2·σ."""
    s = data["national"].query("variable == 'total postings'").set_index("date")["idx"]
    trend, sigma = q.control_band(s, s.index.max())
    assert 0 < sigma < 0.05
    since = s.index.max() - pd.DateOffset(years=q.CONTROL_YEARS)
    assert sigma == pytest.approx((s / trend - 1).loc[since:].std())
    assert q.Z * np.sqrt(2) * sigma > sigma
