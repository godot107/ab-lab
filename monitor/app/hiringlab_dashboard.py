"""Build a one-page demo dashboard from Indeed Hiring Lab data.

    python build_hiringlab.py --refresh      # pull the latest weekly data
    python hiringlab_dashboard.py            # writes hiringlab_dashboard.html
    python hiringlab_dashboard.py --cdn      # smaller file; loads plotly.js online

A static, single-file build of the Hiring Demand Monitor. Every design choice
maps to a principle (see the README). The interactive version is hiringlab_app.py.

  PURPOSE   Built for a weekly GTM (go-to-market) leadership review. The
            question: where is employer hiring demand growing, and how
            broad is it? Headline first, breakdown second, detail on demand.
  ENCODING  Position/length only (lines, bars). One axis per chart. Color
            carries one meaning everywhere: blue = growing YoY, red =
            shrinking YoY (a validated diverging pair). Everything else is
            grey context.
  TRUST     Data-quality checks run BEFORE anything renders, and the result
            is shown on the page. Data-through date, build time, source,
            definitions and caveats are all visible.

Data: Indeed Hiring Lab job postings index (CC BY 4.0), github.com/hiring-lab/
job_postings_tracker. Each series is seasonally adjusted, a 7-day trailing
average, indexed to 100 on its OWN Feb 1, 2020 level. So you can compare
growth across sectors, never size.
"""
from __future__ import annotations

import argparse
import os
import html
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from plotly.offline import get_plotlyjs

import references

# All generated data lives in DATA_DIR (default: app/var). In Docker it's a mounted volume.
VAR = Path(os.environ.get("DATA_DIR", Path(__file__).parent / "var"))
DB = VAR / "hiringlab.db"
OUT = VAR / "hiringlab_dashboard.html"

VARIABLES = {"total postings": "Total postings", "new postings": "New postings"}
FRESHNESS_DAYS = 14      # source refreshes weekly; two missed refreshes = stale
TOP_N = 10               # risers / fallers shown in the ranking chart
MULTIPLES = 4            # risers and fallers each get this many small multiples

# Chart tokens, light and dark. Blue/red passed validate_palette.js in both modes.
THEMES = {
    "light": dict(surface="#fcfcfb", ink="#0b0b0b", ink2="#52514e", muted="#898781",
                  grid="#e1e0d9", axis="#c3c2b7", up="#2a78d6", down="#e34948"),
    "dark":  dict(surface="#1a1a19", ink="#ffffff", ink2="#c3c2b7", muted="#898781",
                  grid="#2c2c2a", axis="#383835", up="#3987e5", down="#e66767"),
}
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'

# Recruit Holdings (Indeed's parent) earnings-call disclosures, US HR Technology.
# Fiscal year starts Apr 1. Values are YoY %, as stated ("approximately") on each call.
# US ARPJ = HR Tech US revenue ÷ total US job postings, and the denominator "is measured
# by the Indeed Hiring Lab US Job Postings Index" (Q1 FY2026 call). Sources:
# recruit-holdings.com IR transcripts for Q2 FY2025 (Nov 6, 2025), Q3 FY2025 (Feb 2026),
# Q4 FY2025 (May 15, 2026) and Q1 FY2026 (Aug 2026).
RECRUIT = [
    dict(fq="FY2025 Q2", period="Jul–Sep 2025", postings=-8, arpj=15, revenue=5.8,  call="Nov 2025"),
    dict(fq="FY2025 Q3", period="Oct–Dec 2025", postings=-7, arpj=18, revenue=10.1, call="Feb 2026"),
    dict(fq="FY2025 Q4", period="Jan–Mar 2026", postings=-5, arpj=25, revenue=None, call="May 2026"),
    dict(fq="FY2026 Q1", period="Apr–Jun 2026", postings=-4, arpj=35, revenue=30.0, call="Aug 2026"),
]
RECRUIT_OUTLOOK = "FY2026 outlook (Aug 2026): postings ≈ −4%, US ARPJ ≈ +30%, US revenue +25.1% to $6.6B"


# ---------------------------------------------------------------------------
# Load (the "gold" query layer: metric logic lives here, not in the charts)
# ---------------------------------------------------------------------------
def load(con: sqlite3.Connection) -> dict[str, pd.DataFrame]:
    def q(sql: str) -> pd.DataFrame:
        df = pd.read_sql_query(sql, con)
        # Parse dates here, not via read_sql(parse_dates=...): that path segfaults
        # in pandas 3.0.4 (to_datetime's cache). cache=False avoids it.
        df["date"] = pd.to_datetime(df["date"], format="%Y-%m-%d", cache=False)
        return df
    return {
        "national": q("SELECT date, variable, index_sa AS idx FROM postings_index "
                      "WHERE jobcountry = 'US' ORDER BY date"),
        "sector": q("SELECT date, variable, sector, idx FROM postings_by_sector "
                    "WHERE jobcountry = 'US' ORDER BY date"),
        "titles": pd.read_sql_query("SELECT sector, job_titles FROM sector_titles", con),
    }


# ---------------------------------------------------------------------------
# Validate: run before rendering, and put the result on the page
# ---------------------------------------------------------------------------
def validate(con: sqlite3.Connection, d: dict, today: pd.Timestamp) -> list[dict]:
    checks = []

    def check(name, ok, detail, severity="block"):
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "severity": severity})

    rows, keys = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT date || '|' || sector || '|' || variable) "
        "FROM postings_by_sector").fetchone()
    check("Grain", rows == keys,
          f"one row per date × sector × variable ({rows:,} rows, {keys:,} unique keys)")

    latest = d["national"]["date"].max()
    age = (today - latest).days
    check("Freshness", age <= FRESHNESS_DAYS,
          f"data through {latest:%b %d, %Y}, {age} days old (limit {FRESHNESS_DAYS})")

    n_sectors = d["sector"]["sector"].nunique()
    at_latest = d["sector"][d["sector"]["date"] == latest].groupby("variable")["sector"].nunique()
    check("Completeness", (at_latest == n_sectors).all() and len(at_latest) == 2,
          f"all {n_sectors} sectors reported on the latest date, for both series")

    nat = d["national"][d["national"]["variable"] == "total postings"]["date"]
    expected = (nat.max() - nat.min()).days + 1
    check("Continuity", len(nat) == expected,
          f"{len(nat):,} daily rows, {expected:,} expected, no gaps")

    nulls = int(d["sector"]["idx"].isna().sum() + d["national"]["idx"].isna().sum())
    check("No nulls", nulls == 0, f"{nulls} missing index values")

    # Report WHERE the extremes are, so a human can judge them. (The low, ~2.7, is
    # Dental new postings on Apr 10, 2020: offices closed in lockdown. Real, not an error.)
    sec = d["sector"]
    lo_row, hi_row = sec.loc[sec["idx"].idxmin()], sec.loc[sec["idx"].idxmax()]
    lo = min(lo_row["idx"], d["national"]["idx"].min())
    hi = max(hi_row["idx"], d["national"]["idx"].max())
    check("Plausible range", 0 < lo and hi < 1000,
          f"index values between {lo:.1f} ({lo_row['sector']} {lo_row['variable']}, "
          f"{lo_row['date']:%b %d, %Y}) and {hi:.1f} ({hi_row['sector']} {hi_row['variable']}, "
          f"{hi_row['date']:%b %d, %Y})")

    need = [latest - pd.DateOffset(years=1), latest - pd.Timedelta(days=91)]
    have = set(d["national"]["date"]) & set(d["sector"]["date"])
    check("Comparison dates", all(x in have for x in need),
          "year-ago and 13-weeks-ago dates exist in both tables")
    missing = sorted(set(d["sector"]["sector"]) - set(d["titles"].dropna()["sector"]))
    check("Title lookup coverage", not missing,
          f"{len(missing)} sectors have no example titles in the reference table"
          + (f" ({', '.join(missing)})" if missing else ""), severity="warn")
    return checks


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def metrics(d: dict, variable: str) -> dict:
    nat = d["national"][d["national"]["variable"] == variable].set_index("date")["idx"]
    wide = (d["sector"][d["sector"]["variable"] == variable]
            .pivot(index="date", columns="sector", values="idx"))
    latest = nat.index.max()
    yago, q_ago = latest - pd.DateOffset(years=1), latest - pd.Timedelta(days=91)

    sec = pd.DataFrame({"now": wide.loc[latest], "yago": wide.loc[yago], "q_ago": wide.loc[q_ago]})
    sec["yoy"] = sec["now"] / sec["yago"] - 1          # same base, so exact % change
    sec["mom13"] = sec["now"] / sec["q_ago"] - 1
    sec = sec.join(d["titles"].set_index("sector")["job_titles"]).sort_values("yoy", ascending=False)

    return dict(
        variable=variable, label=VARIABLES[variable], latest=latest, yago=yago,
        nat=nat, wide=wide, sec=sec,
        now=nat[latest], nat_yago=nat[yago],
        yoy=nat[latest] / nat[yago] - 1, mom13=nat[latest] / nat[q_ago] - 1,
        n_up=int((sec["yoy"] > 0).sum()), n=len(sec),
    )


def fiscal_quarters(nat: pd.Series, latest: pd.Timestamp) -> pd.DataFrame:
    """Mean of the daily SA index per Recruit fiscal quarter (FY starts Apr 1), and its YoY.
    The current quarter is partial, so it's flagged QTD (quarter to date)."""
    fy = nat.index.year - (nat.index.month < 4)
    q = (nat.index.month - 4) % 12 // 3 + 1
    label = pd.Series([f"FY{a} Q{b}" for a, b in zip(fy, q)], index=nat.index)
    means = nat.groupby(label.values).mean()
    rows = []
    for k, v in means.items():
        y, qq = k.split()
        prev = f"FY{int(y[2:]) - 1} {qq}"
        if prev in means:
            rows.append(dict(fq=k, idx=v, yoy=(v / means[prev] - 1) * 100))
    out = pd.DataFrame(rows)
    out["qtd"] = out["fq"] == label.loc[latest]
    return out.sort_values("fq", key=lambda c: c.str.replace("FY", "").str.replace(" Q", ".")).tail(8)


# ---------------------------------------------------------------------------
# Figures (built once per theme; the page swaps them, not an auto color-flip)
# ---------------------------------------------------------------------------
def base_layout(t: dict, **kw) -> dict:
    axis = dict(showgrid=False, zeroline=False, showline=False, ticks="",
                tickfont=dict(color=t["muted"], size=11), gridcolor=t["grid"], gridwidth=1)
    return dict(
        font=dict(family=FONT, size=12, color=t["ink2"]),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=8, r=8, t=8, b=8), showlegend=False,
        hoverlabel=dict(bgcolor=t["surface"], bordercolor=t["axis"],
                        font=dict(family=FONT, color=t["ink"], size=12)),
        xaxis=dict(axis), yaxis=dict(axis, showgrid=True),
    ) | kw


def fig_trend(m: dict, t: dict) -> go.Figure:
    nat = m["nat"]
    fig = go.Figure(go.Scatter(
        x=nat.index, y=nat.values, mode="lines", line=dict(color=t["up"], width=2),
        hovertemplate="%{x|%b %d, %Y}<br>Index <b>%{y:.1f}</b><extra></extra>"))
    # Context marks: the pre-pandemic baseline and the year-ago comparison point.
    fig.add_hline(y=100, line=dict(color=t["axis"], width=1))
    # Label the baseline where the line is far above it (mid-2023), not at the crowded start.
    fig.add_annotation(x=pd.Timestamp("2021-07-01"), y=100, text="Feb 1, 2020 = 100", showarrow=False,
                       yanchor="top", yshift=-2, font=dict(color=t["muted"], size=11))
    for when, val, label, color, shift in [(m["yago"], m["nat_yago"], "1 yr ago", t["muted"], -18),
                                           (m["latest"], m["now"], "Now", t["up"], 18)]:
        fig.add_trace(go.Scatter(x=[when], y=[val], mode="markers", hoverinfo="skip",
                                 marker=dict(size=9, color=color, line=dict(width=2, color=t["surface"]))))
        fig.add_annotation(x=when, y=val, text=f"{label} <b>{val:.1f}</b>", showarrow=False,
                           yshift=shift, xanchor="right", font=dict(color=t["ink"], size=12))
    fig.update_layout(base_layout(t, height=500, hovermode="x"))
    fig.update_xaxes(showspikes=True, spikemode="across", spikethickness=1,
                     spikecolor=t["axis"], spikedash="solid")
    return fig


def fig_ranking(m: dict, t: dict) -> go.Figure:
    sec = m["sec"]
    top, bottom = sec.head(TOP_N), sec.tail(TOP_N)
    hidden = len(sec) - 2 * TOP_N
    gap_label = f"… {hidden} more sectors (see table)"
    order = list(top.index) + [gap_label] + list(bottom.index)   # top risers first

    fig = go.Figure()
    for name, part, color in [("Growing YoY", pd.concat([top, bottom]).query("yoy > 0"), t["up"]),
                              ("Shrinking YoY", pd.concat([top, bottom]).query("yoy <= 0"), t["down"])]:
        fig.add_trace(go.Bar(
            name=name, y=part.index, x=part["yoy"] * 100, orientation="h",
            marker=dict(color=color), customdata=part[["now"]].values,
            hovertemplate="<b>%{y}</b><br>%{x:+.1f}% YoY · index %{customdata[0]:.1f}<extra></extra>"))
    # Direct-label only the two extremes; the table carries every value.
    # Label sits in the empty space on the far side of zero from a negative bar, so it
    # never collides with the category labels.
    for name in (sec.index[0], sec.index[-1]):
        v = sec.loc[name, "yoy"] * 100
        fig.add_annotation(x=max(v, 0), y=name, text=f"<b>{v:+.0f}%</b>", showarrow=False,
                           xanchor="left", xshift=6, font=dict(color=t["ink"], size=12))
    fig.add_vline(x=0, line=dict(color=t["axis"], width=1))
    fig.update_layout(base_layout(
        t, height=500, barmode="overlay", bargap=0.35, barcornerradius=4, showlegend=True,
        legend=dict(orientation="h", y=1.0, yanchor="bottom", x=0, font=dict(color=t["ink2"])),
        margin=dict(l=8, r=44, t=36, b=8)))
    fig.update_yaxes(categoryorder="array", categoryarray=order[::-1], showgrid=False,
                     tickfont=dict(color=t["ink2"], size=11), ticksuffix="  ")
    lo, hi = sec["yoy"].min() * 100, sec["yoy"].max() * 100
    fig.update_xaxes(showgrid=True, ticksuffix="%", zeroline=False, nticks=5,
                     range=[min(lo, 0) * 1.25 - 2, max(hi, 0) * 1.2 + 2])
    return fig


def fig_earnings(m: dict, t: dict) -> go.Figure:
    """Index YoY by fiscal quarter (bars) vs the postings YoY Recruit reported (markers).
    One axis, same unit (YoY %), so the comparison is honest."""
    fq = fiscal_quarters(m["nat"], m["latest"])
    rep_ = {r["fq"]: r for r in RECRUIT}
    x = [f"{r.fq}{' (QTD)' if r.qtd else ''}" for r in fq.itertuples()]
    fig = go.Figure(go.Bar(
        name="Hiring Lab index (this dashboard)", x=x, y=fq["yoy"],
        marker=dict(color=[t["up"] if v > 0 else t["down"] for v in fq["yoy"]]),
        hovertemplate="%{x}<br>Index YoY <b>%{y:+.1f}%</b><extra></extra>"))
    pts = [(xi, rep_[f]["postings"], rep_[f]["arpj"], rep_[f]["call"])
           for xi, f in zip(x, fq["fq"]) if f in rep_]
    fig.add_trace(go.Scatter(
        name="Recruit-reported postings YoY (earnings call)", x=[p[0] for p in pts], y=[p[1] for p in pts],
        mode="markers", marker=dict(symbol="diamond", size=11, color=t["ink"], line=dict(width=2, color=t["surface"])),
        customdata=[[p[2], p[3]] for p in pts],
        hovertemplate="%{x}<br>Recruit: postings <b>~%{y}%</b>, ARPJ <b>+%{customdata[0]}%</b>"
                      "<br>reported %{customdata[1]}<extra></extra>"))
    fig.add_hline(y=0, line=dict(color=t["axis"], width=1))
    fig.update_layout(base_layout(t, height=300, showlegend=True, bargap=0.45, barcornerradius=4,
                                  legend=dict(orientation="h", y=1.0, yanchor="bottom", x=0,
                                              font=dict(color=t["ink2"])),
                                  margin=dict(l=8, r=8, t=36, b=8)))
    fig.update_yaxes(ticksuffix="%", nticks=5)
    fig.update_xaxes(tickfont=dict(color=t["ink2"], size=11))
    return fig


def multiple_picks(m: dict) -> list[str]:
    return list(m["sec"].index[:MULTIPLES]) + list(m["sec"].index[-MULTIPLES:])


def fig_multiples(m: dict, t: dict) -> dict[str, go.Figure]:
    """Small multiples as separate figures so CSS can reflow them (4 across, 2 on phones).
    The y range is computed once and shared: same scale, so panels are comparable."""
    sec, wide = m["sec"], m["wide"]
    picks = multiple_picks(m)
    since = m["latest"] - pd.DateOffset(years=3)
    window = wide.loc[since:, picks]
    lo, hi = min(window.min().min(), 100), max(window.max().max(), 100)
    pad = (hi - lo) * 0.06
    figs = {}
    for i, s in enumerate(picks):
        series = window[s]
        color = t["up"] if sec.loc[s, "yoy"] > 0 else t["down"]
        fig = go.Figure(go.Scatter(x=series.index, y=series.values, mode="lines",
                                   line=dict(color=color, width=1.75),
                                   hovertemplate="%{x|%b %d, %Y}<br>Index <b>%{y:.1f}</b><extra></extra>"))
        fig.add_trace(go.Scatter(x=[series.index[-1]], y=[series.iloc[-1]], mode="markers",
                                 hoverinfo="skip", marker=dict(size=7, color=color,
                                 line=dict(width=2, color=t["surface"]))))
        fig.add_hline(y=100, line=dict(color=t["axis"], width=1))
        fig.update_layout(base_layout(t, height=150, hovermode="x", margin=dict(l=4, r=8, t=4, b=4)))
        fig.update_xaxes(dtick="M12", tickformat="%Y", tickfont=dict(color=t["muted"], size=10))
        fig.update_yaxes(range=[lo - pad, hi + pad], nticks=4, tickfont=dict(color=t["muted"], size=10))
        figs[f"m{i}"] = fig
    return figs


# ---------------------------------------------------------------------------
# HTML pieces
# ---------------------------------------------------------------------------
def pct(x: float, digits: int = 1) -> str:
    return f"{x * 100:+.{digits}f}%"


def delta(x: float) -> str:
    cls, arrow = ("up", "▲") if x > 0 else ("down", "▼")
    return f'<span class="delta {cls}"><span aria-hidden="true">{arrow}</span> {pct(x)}</span>'


def sparkline(s: pd.Series, w: int = 120, h: int = 30, color_var: str = "--series-up") -> str:
    """Tufte sparkline: data-intense, design-simple, word-sized. Last dot = now."""
    s = s.dropna()
    lo, hi = s.min(), s.max()
    span = (hi - lo) or 1
    xs = [i * (w - 4) / (len(s) - 1) + 2 for i in range(len(s))]
    ys = [h - 3 - (v - lo) / span * (h - 6) for v in s.values]
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" aria-hidden="true">'
            f'<polyline points="{pts}" fill="none" stroke="var({color_var})" stroke-width="1.5" '
            f'stroke-linejoin="round"/><circle cx="{xs[-1]:.1f}" cy="{ys[-1]:.1f}" r="2.5" '
            f'fill="var({color_var})"/></svg>')


def action_title(m: dict) -> str:
    vs_base = m["now"] - 100
    where = "above" if vs_base >= 0 else "below"
    leader = m["sec"].index[0]
    return (f"US {m['label'].lower()} are {abs(vs_base):.1f}% {where} pre-pandemic and "
            f"{pct(m['yoy'])} year over year. {m['n_up']} of {m['n']} sectors are growing, "
            f"led by {html.escape(leader)}.")


def tiles(m: dict) -> str:
    year = m["nat"].loc[m["latest"] - pd.DateOffset(years=1):]
    breadth = m["n_up"] / m["n"]
    return f"""
    <div class="tiles">
      <div class="tile"><div class="t-label">Postings index (SA)</div>
        <div class="t-row"><div class="t-value">{m['now']:.1f}</div>{sparkline(year)}</div>
        <div class="t-note">Feb 1, 2020 = 100 · last 12 months</div></div>
      <div class="tile"><div class="t-label">Year over year</div>
        <div class="t-value">{delta(m['yoy'])}</div>
        <div class="t-note">vs {m['nat_yago']:.1f} on {m['yago']:%b %d, %Y}</div></div>
      <div class="tile"><div class="t-label">13-week momentum</div>
        <div class="t-value">{delta(m['mom13'])}</div>
        <div class="t-note">Recent direction, less sensitive to last year's base</div></div>
      <div class="tile"><div class="t-label">Breadth</div>
        <div class="t-value">{m['n_up']} <span class="t-of">of {m['n']}</span></div>
        <div class="meter" role="img" aria-label="{m['n_up']} of {m['n']} sectors growing">
          <span style="width:{breadth * 100:.1f}%"></span></div>
        <div class="t-note">sectors with postings up YoY</div></div>
    </div>"""


def table(m: dict) -> str:
    wide, since = m["wide"], m["latest"] - pd.DateOffset(years=1)
    rows = []
    for s, r in m["sec"].iterrows():
        var = "--series-up" if r["yoy"] > 0 else "--series-down"
        rows.append(
            f"<tr><td>{html.escape(s)}</td>"
            f'<td class="muted">{html.escape(r["job_titles"]) if isinstance(r["job_titles"], str) else "—"}</td>'
            f'<td class="num" data-v="{r["now"]:.3f}">{r["now"]:.1f}</td>'
            f'<td class="num" data-v="{r["yoy"]:.5f}">{delta(r["yoy"])}</td>'
            f'<td class="num" data-v="{r["mom13"]:.5f}">{pct(r["mom13"])}</td>'
            f"<td>{sparkline(wide.loc[since:, s], 100, 22, var)}</td></tr>")
    return f"""
    <table class="sortable">
      <thead><tr><th data-type="text">Sector</th><th data-type="text">Example titles</th>
        <th data-type="num">Index</th><th data-type="num" aria-sort="descending">YoY</th>
        <th data-type="num">13-wk</th><th>Last 12 months</th></tr></thead>
      <tbody>{''.join(rows)}</tbody></table>"""


def dq_strip(checks: list[dict]) -> str:
    """Blocking checks decide whether the numbers are trustworthy; warnings are
    shown for review but don't block. Every check states what it verified."""
    blocking = [c for c in checks if c["severity"] == "block"]
    failed = [c for c in blocking if not c["ok"]]
    warns = [c for c in checks if c["severity"] == "warn" and not c["ok"]]
    ok = not failed
    icon, cls = ("✓", "good") if ok else ("⚠", "bad")
    msg = ("Data checks passed" if ok else "Data checks FAILED: treat numbers as unverified")
    if warns:
        msg += f' <span class="warn">· ⚠ {len(warns)} warning{"s" * (len(warns) > 1)}</span>'
    mark = lambda c: ("good", "✓") if c["ok"] else (("warn", "⚠") if c["severity"] == "warn" else ("bad", "✗"))
    items = "".join(f'<li><span class="{mark(c)[0]}">{mark(c)[1]}</span> <b>{c["name"]}</b>'
                    f'{" (warning)" if c["severity"] == "warn" else ""}: {html.escape(c["detail"])}</li>'
                    for c in checks)
    return (f'<details class="dq {cls}"><summary><span class="badge {cls}">{icon} '
            f'{len(blocking) - len(failed)}/{len(blocking)}</span> {msg}</summary><ul>{items}</ul></details>')


def earnings_tree(m: dict) -> str:
    """Latest reported quarter as a metric tree: revenue = postings × ARPJ, with a check."""
    fq = fiscal_quarters(m["nat"], m["latest"]).set_index("fq")
    last = RECRUIT[-1]
    ours = fq.loc[last["fq"], "yoy"] if last["fq"] in fq.index else float("nan")
    implied = ((1 + last["postings"] / 100) * (1 + last["arpj"] / 100) - 1) * 100
    return f"""<div class="tree">
          <div class="t-label">{last['fq']} ({last['period']}), reported {last['call']}</div>
          <div class="eq"><span class="term"><span class="t-note">US revenue</span><b>{last['revenue']:+.1f}%</b></span>
            <span class="op">=</span>
            <span class="term"><span class="t-note">Job postings</span><b>{last['postings']:+d}%</b>
              <span class="t-note">this index: {ours:+.1f}%</span></span>
            <span class="op">×</span>
            <span class="term"><span class="t-note">ARPJ</span><b>+{last['arpj']}%</b></span></div>
          <p class="t-note">Check: {1 + last['postings'] / 100:.2f} × {1 + last['arpj'] / 100:.2f} − 1 =
            {implied:+.1f}%, matching the reported {last['revenue']:+.1f}%. Postings are the MARKET branch (Indeed
            doesn't control it). ARPJ is the MONETIZATION branch (Premium Sponsored Jobs, matching).</p>
          <p class="t-note">{RECRUIT_OUTLOOK}.</p>
        </div>"""


def earnings_card(m: dict, key: str) -> str:
    if key != "total":
        return ('<div class="card"><h2>Why this index matters to Indeed\'s earnings</h2>'
                '<p class="sub">Recruit\'s ARPJ uses <b>total</b> postings. Switch to Total postings to see it.</p></div>')
    return f"""
    <div class="card"><h2>Why this index matters to Indeed's earnings</h2>
      <p class="sub">Recruit computes US ARPJ (average revenue per job posting) = HR Tech US revenue ÷ total US
        job postings, and the denominator "is measured by the Indeed Hiring Lab US Job Postings Index."
        This dashboard shows that denominator.</p>
      <div class="earn">
        <div class="plot" id="earnings-{key}"></div>
        {earnings_tree(m)}
      </div></div>"""


def multiples_html(m: dict, key: str) -> str:
    out = []
    for i, s in enumerate(multiple_picks(m)):
        out.append(f'<div class="panel"><div class="p-title">{html.escape(s)} '
                   f'{delta(m["sec"].loc[s, "yoy"])}</div><div id="m{i}-{key}"></div></div>')
    return "".join(out)


def section(m: dict, key: str) -> str:
    return f"""
  <section class="view" data-view="{key}" hidden>
    <h1>{action_title(m)}</h1>
    {tiles(m)}
    <div class="grid">
      <div class="card wide"><h2>National postings index</h2>
        <p class="sub">Seasonally adjusted, 7-day trailing average · Feb 1, 2020 = 100</p>
        <div class="plot" id="trend-{key}"></div></div>
      <div class="card"><h2>Which sectors are growing?</h2>
        <p class="sub">YoY change in postings, top and bottom {TOP_N} of {m['n']} sectors</p>
        <div class="plot" id="rank-{key}"></div></div>
    </div>
    <div class="card"><h2>Biggest movers, last 3 years</h2>
      <p class="sub">Top {MULTIPLES} risers, then top {MULTIPLES} fallers, by YoY · same scale on every panel · grey line = Feb 2020 baseline</p>
      <div class="multiples">{multiples_html(m, key)}</div></div>
    {earnings_card(m, key)}
    <details class="card table-card"><summary><h2>All {m['n']} sectors: table view</h2>
      <span class="sub">Click a column to sort</span></summary>{table(m)}</details>
  </section>"""


CSS = """
:root{color-scheme:light;--plane:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--grid:#e1e0d9;--axis:#c3c2b7;--ring:rgba(11,11,11,.10);--series-up:#2a78d6;--series-down:#e34948;
--good:#006300;--bad:#d03b3b;--warn:#8a5a00;--wash:rgba(11,11,11,.04)}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--plane:#0d0d0d;
--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;
--ring:rgba(255,255,255,.10);--series-up:#3987e5;--series-down:#e66767;--good:#0ca30c;--bad:#e66767;--warn:#fab219;
--wash:rgba(255,255,255,.05)}}
:root[data-theme="dark"]{color-scheme:dark;--plane:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;
--muted:#898781;--grid:#2c2c2a;--axis:#383835;--ring:rgba(255,255,255,.10);--series-up:#3987e5;
--series-down:#e66767;--good:#0ca30c;--bad:#e66767;--warn:#fab219;--wash:rgba(255,255,255,.05)}
*{box-sizing:border-box}
body{margin:0;background:var(--plane);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1320px;margin:0 auto;padding:20px 16px 40px}
header .kicker{color:var(--muted);font-size:12px;letter-spacing:.04em;text-transform:uppercase;margin:0}
header .purpose{color:var(--ink2);margin:4px 0 12px}
.meta{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;color:var(--ink2);font-size:12.5px}
.meta b{color:var(--ink)}
.controls{display:flex;gap:12px;align-items:center;margin:16px 0 4px;flex-wrap:wrap}
.seg{display:inline-flex;border:1px solid var(--ring);border-radius:8px;padding:2px;background:var(--surface)}
.seg button{border:0;background:none;color:var(--ink2);font:inherit;padding:6px 12px;border-radius:6px;cursor:pointer}
.seg button[aria-pressed="true"]{background:var(--wash);color:var(--ink);font-weight:600}
.controls .hint{color:var(--muted);font-size:12.5px}
h1{font-size:20px;line-height:1.35;font-weight:600;margin:14px 0 14px;max-width:70ch}
h2{font-size:14px;font-weight:600;margin:0;color:var(--ink);display:inline}
.sub{color:var(--muted);font-size:12.5px;margin:2px 0 8px}
.tiles{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:12px}
.tile,.card{background:var(--surface);border-radius:10px;box-shadow:0 0 0 1px var(--ring)}
.tile{padding:12px 14px}
.t-label{color:var(--ink2);font-size:12.5px}
.t-row{display:flex;align-items:flex-end;gap:12px;justify-content:space-between}
.t-value{font-size:28px;font-weight:600;line-height:1.2;margin:2px 0}
.t-value .delta{font-size:28px}
.t-of{font-size:16px;color:var(--ink2);font-weight:400}
.t-note{color:var(--muted);font-size:12px}
.meter{height:6px;border-radius:3px;background:var(--grid);margin:6px 0 4px;overflow:hidden}
.meter span{display:block;height:100%;background:var(--series-up);border-radius:3px}
.delta.up{color:var(--good)} .delta.down{color:var(--bad)}
.grid{display:grid;grid-template-columns:minmax(0,7fr) minmax(0,5fr);gap:12px;margin-bottom:12px}
.card{padding:14px 14px 8px;margin-bottom:12px}
.grid .card{margin-bottom:0}
.multiples{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:4px 16px}
.panel .p-title{font-size:12.5px;color:var(--ink);margin:6px 0 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.panel .p-title .delta{font-weight:600}
.earn{display:grid;grid-template-columns:minmax(0,7fr) minmax(0,5fr);gap:16px;align-items:center}
.tree{background:var(--wash);border-radius:8px;padding:12px 14px}
.eq{display:flex;flex-wrap:wrap;align-items:flex-end;gap:6px 10px;margin:8px 0}
.eq .term{display:flex;flex-direction:column}
.eq .term b{font-size:22px;font-weight:600;line-height:1.2}
.eq .op{font-size:20px;color:var(--muted);padding-bottom:2px}
.tree p{margin:6px 0 0}
.table-card summary{cursor:pointer;list-style:none;padding-bottom:6px}
.table-card summary::-webkit-details-marker{display:none}
.table-card summary h2::before{content:"▸ ";color:var(--muted)}
.table-card[open] summary h2::before{content:"▾ "}
.table-card .sub{margin-left:8px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--ink2);font-weight:600;border-bottom:1px solid var(--axis);padding:8px 6px;cursor:pointer;white-space:nowrap}
th[aria-sort="ascending"]::after{content:" ▲";font-size:10px} th[aria-sort="descending"]::after{content:" ▼";font-size:10px}
td{border-bottom:1px solid var(--grid);padding:6px;vertical-align:middle}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
th:nth-child(3),th:nth-child(4),th:nth-child(5){text-align:right}
td.muted{color:var(--muted);max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
tbody tr:hover{background:var(--wash)}
.dq{font-size:12.5px;color:var(--ink2)} .dq summary{cursor:pointer}
.dq ul{margin:6px 0 0;padding-left:18px} .dq li{margin:2px 0}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-weight:600}
.badge.good,.dq .good{color:var(--good)} .dq .warn{color:var(--warn)} .badge.bad,.dq .bad{color:var(--bad)}
.badge.good{box-shadow:0 0 0 1px var(--good)} .badge.bad{box-shadow:0 0 0 1px var(--bad)}
footer{color:var(--ink2);font-size:12.5px;margin-top:8px}
footer h2{font-size:13px} footer ul{padding-left:18px;margin:6px 0} footer li{margin:3px 0}
@media (max-width:960px){.grid,.earn{grid-template-columns:minmax(0,1fr)}.tiles{grid-template-columns:repeat(2,minmax(0,1fr))}.multiples{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:520px){.tiles{grid-template-columns:minmax(0,1fr)}h1{font-size:18px}td.muted,th:nth-child(2){display:none}}
"""

JS = """
const FIGS = __FIGS__;
const isDark = () => {
  const t = document.documentElement.dataset.theme;
  return t ? t === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
};
const CONFIG = {displayModeBar: false, responsive: true};
let current = 'total';
function render() {
  const theme = isDark() ? 'dark' : 'light';
  for (const [name, fig] of Object.entries(FIGS[current][theme])) {
    Plotly.react(`${name}-${current}`, fig.data, fig.layout, CONFIG);
  }
}
function show(view) {
  current = view;
  document.querySelectorAll('.view').forEach(s => s.hidden = s.dataset.view !== view);
  document.querySelectorAll('.seg button').forEach(b =>
    b.setAttribute('aria-pressed', String(b.dataset.view === view)));
  render();
}
document.querySelectorAll('.seg button').forEach(b => b.addEventListener('click', () => show(b.dataset.view)));
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', render);
new MutationObserver(render).observe(document.documentElement, {attributes: true, attributeFilter: ['data-theme']});
// Sortable table: click a header; numbers sort on data-v, text alphabetically.
document.querySelectorAll('table.sortable').forEach(tbl => {
  tbl.querySelectorAll('th[data-type]').forEach((th, col) => th.addEventListener('click', () => {
    const asc = th.getAttribute('aria-sort') !== 'ascending';
    tbl.querySelectorAll('th').forEach(h => h.removeAttribute('aria-sort'));
    th.setAttribute('aria-sort', asc ? 'ascending' : 'descending');
    const num = th.dataset.type === 'num', body = tbl.tBodies[0];
    const key = r => num ? parseFloat(r.cells[col].dataset.v) : r.cells[col].textContent;
    [...body.rows].sort((a, b) => (num ? key(a) - key(b) : key(a).localeCompare(key(b))) * (asc ? 1 : -1))
      .forEach(r => body.appendChild(r));
  }));
});
show('total');
"""


def build(inline: bool) -> Path:
    con = sqlite3.connect(DB)
    d = load(con)
    today = pd.Timestamp(datetime.now().date())
    import dq_checks                                  # same checks as the app's Data quality tab
    checks = [r.legacy() for r in dq_checks.run_checks(d, today)[0]]
    con.close()

    views, figs = [], {}
    for variable, key in [("total postings", "total"), ("new postings", "new")]:
        m = metrics(d, variable)
        views.append(section(m, key))
        figs[key] = {}
        for theme, t in THEMES.items():
            built = {"trend": fig_trend(m, t), "rank": fig_ranking(m, t)} | fig_multiples(m, t)
            if key == "total":
                built["earnings"] = fig_earnings(m, t)
            figs[key][theme] = {name: json.loads(pio.to_json(f)) for name, f in built.items()}
    latest = d["national"]["date"].max()

    plotly_js = (f"<script>{get_plotlyjs()}</script>" if inline else
                 '<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@3/plotly.min.js"></script>')
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hiring Demand Monitor</title>
<style>{CSS}{references.CSS}</style>{plotly_js}</head>
<body><main>
  <header>
    <p class="kicker">Hiring demand monitor · US</p>
    <p class="purpose">For a weekly go-to-market review: <b>where is employer hiring demand growing, and how broad is it?</b></p>
    <div class="meta">
      <span>Data through <b>{latest:%b %d, %Y}</b></span>
      <span>Built {datetime.now():%b %d, %Y %H:%M}</span>
      <span>Source: Indeed Hiring Lab, CC BY 4.0</span>
      <span>Owner: godot107</span>
      {dq_strip(checks)}
    </div>
  </header>
  <div class="controls">
    <div class="seg" role="group" aria-label="Series">
      <button data-view="total" aria-pressed="true">Total postings</button>
      <button data-view="new" aria-pressed="false">New postings (≤7 days)</button>
    </div>
    <span class="hint">Scopes every number and chart below. New postings react to demand first.</span>
  </div>
  {''.join(views)}
  <footer class="card">
    <h2>Definitions &amp; caveats</h2>
    <ul>
      <li><b>Index</b>: seasonally adjusted job postings on Indeed as a 7-day trailing average, where 100 =
        the level on Feb 1, 2020 (pre-pandemic). 103.5 means 3.5% above that level.</li>
      <li><b>YoY</b> = index today ÷ index on the same date last year − 1. Both points share one base, so
        this is the exact % change in postings. <b>13-week</b> is the same comparison against 91 days ago.</li>
      <li><b>Growth, not size.</b> Every sector is indexed to its <i>own</i> 2020 level and Indeed publishes no
        counts. A small sector's +30% can be fewer jobs than a large sector's +3%. Rankings show direction,
        not where most jobs are.</li>
      <li><b>Base effects.</b> A sector that fell sharply last year shows a big YoY rebound. Check 13-week
        momentum before calling it a trend.</li>
      <li><b>Postings are demand signals, not hires</b>, and not Indeed revenue. This is Hiring Lab's public
        index, not internal data.</li>
      <li><b>Earnings panel.</b> Recruit figures are as stated on its earnings calls (mostly "approximately").
        Index YoY per fiscal quarter here = mean of the daily index vs the same quarter a year earlier; Recruit's
        exact aggregation may differ slightly. Recruit's fiscal year starts April 1.</li>
      <li>Hiring Lab revised its seasonal-adjustment method in Nov 2024 and restated history, so figures may
        differ from older reports. Sectors are Indeed's categories based on normalized job titles.</li>
    </ul>
    <h2>References &amp; links</h2>
    {references.references_html()}
  </footer>
</main>
<script>{JS.replace("__FIGS__", json.dumps(figs))}</script>
</body></html>"""
    OUT.write_text(page, encoding="utf-8")
    blocking = [c for c in checks if c["severity"] == "block"]
    failed = [c["name"] for c in blocking if not c["ok"]]
    warns = [c["name"] for c in checks if c["severity"] == "warn" and not c["ok"]]
    print(f"Wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")
    print(f"  data through {latest:%Y-%m-%d} · blocking checks {len(blocking) - len(failed)}/{len(blocking)} passed"
          + (f" · FAILED: {', '.join(failed)}" if failed else "")
          + (f" · warnings: {', '.join(warns)}" if warns else ""))
    return OUT


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cdn", action="store_true", help="load plotly.js from a CDN instead of inlining it")
    build(inline=not ap.parse_args().cdn)
