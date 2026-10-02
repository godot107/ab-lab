"""Interactive Hiring Demand Monitor: Plotly Dash version of hiringlab_dashboard.py.

    python build_hiringlab.py --refresh     # latest weekly data
    python hiringlab_app.py                 # http://127.0.0.1:8050

Same data, checks and design system as the static build (it imports them), plus
the interactions a Tableau / Power BI user expects:

  FILTERS (one row, scopes everything)   Tableau equivalent
    Series toggle                        parameter
    Compare-to slider (4/13/26/52 wks)   parameter driving a calculated field
    Date window range slider             date range filter
    Top/bottom N slider                  Top-N set / parameter
    Small-multiples sectors + scale      small multiples on a dimension
  DRILL-THROUGH (click any mark)         "View Data" / drill-through page
    ranking bar, small multiple, table row  → that sector's underlying rows,
                                              with the 2 rows behind the number
                                              highlighted
    trend point                             → every sector on that date
    earnings bar                            → the daily rows averaged into it

Metric logic stays in Python (the callbacks), not in the charts.
"""
from __future__ import annotations

import copy
import html as html_lib
import os
import sqlite3
from datetime import datetime
from functools import lru_cache

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from flask import request
from dash import (ALL, Dash, Input, Output, State, clientside_callback, ctx, dash_table, dcc,
                  html, no_update)

import dq_checks as q
import dq_page
import hiringlab_dashboard as hd
import references
from ablab import Experiment

# ---------------------------------------------------------------------------
# Data: loaded and validated once at startup
# ---------------------------------------------------------------------------
_con = sqlite3.connect(hd.DB)
D = hd.load(_con)
_con.close()
DQ_RESULTS, _ = q.run_checks(D)                    # same checks as the Data quality tab
CHECKS = [r.legacy() for r in DQ_RESULTS]          # header badge format
q.record_history(DQ_RESULTS, D["national"]["date"].max())

LATEST = D["national"]["date"].max()
FIRST = D["national"]["date"].min()
MONTHS = list(pd.date_range(FIRST.to_period("M").to_timestamp(), LATEST, freq="MS"))
TITLES = D["titles"].set_index("sector")["job_titles"]
PERIODS = {4: "4 weeks", 13: "13 weeks", 26: "26 weeks", 52: "1 year"}
STOPS = list(PERIODS)            # the Compare-to slider moves through these, evenly spaced
SERIES = {"total postings": "Total postings", "new postings": "New postings (≤7 days)"}


@lru_cache(maxsize=None)
def series(variable: str) -> tuple[pd.Series, pd.DataFrame]:
    nat = D["national"][D["national"]["variable"] == variable].set_index("date")["idx"]
    wide = (D["sector"][D["sector"]["variable"] == variable]
            .pivot(index="date", columns="sector", values="idx"))
    return nat, wide


# Normal variation (Cairo: show uncertainty; Sebastian-Coleman: 3σ control limits).
# Each reading wobbles around its own trend. σ = std of (index ÷ centered 29-day mean − 1)
# over the last 3 years. A change compares TWO readings, so its noise is ≈ √2·σ, and a
# change counts as "beyond normal variation" only if |change| > 3·√2·σ. A screening
# rule, not a formal test: the series is autocorrelated and seasonally adjusted.
Z = q.Z


@lru_cache(maxsize=None)
def noise(variable: str) -> tuple[float, pd.Series, pd.Series, pd.DataFrame]:
    """σ of each series' wobble around its trend (one definition: dq_checks.control_band)."""
    nat, wide = series(variable)
    nat_tr, sig_nat = q.control_band(nat, LATEST)
    bands = {c: q.control_band(wide[c], LATEST) for c in wide.columns}
    wide_tr = pd.DataFrame({c: b[0] for c, b in bands.items()})
    sig_sec = pd.Series({c: b[1] for c, b in bands.items()})
    return sig_nat, sig_sec, nat_tr, wide_tr


def limit(sigma):
    """Largest change between two readings that's still normal variation."""
    return Z * np.sqrt(2) * sigma


def ref_date(when: pd.Timestamp, weeks: int) -> pd.Timestamp:
    """52 weeks means the same calendar date last year, matching the static build's YoY."""
    return when - pd.DateOffset(years=1) if weeks == 52 else when - pd.Timedelta(weeks=weeks)


def compute(variable: str, weeks: int) -> dict:
    nat, wide = series(variable)
    ref = ref_date(LATEST, weeks)
    sec = pd.DataFrame({"now": wide.loc[LATEST], "then": wide.loc[ref]})
    sec["chg"] = sec["now"] / sec["then"] - 1
    sig_nat, sig_sec, nat_tr, wide_tr = noise(variable)
    sec["sigma"] = sig_sec
    sec["lim"] = limit(sig_sec)
    sec["beyond"] = sec["chg"].abs() > sec["lim"]
    sec = sec.sort_values("chg", ascending=False)
    yago = LATEST - pd.DateOffset(years=1)
    q13 = LATEST - pd.Timedelta(weeks=13)
    chg = nat[LATEST] / nat[ref] - 1
    return dict(variable=variable, weeks=weeks, period=PERIODS[weeks], nat=nat, wide=wide,
                sec=sec, ref=ref, now=nat[LATEST], then=nat[ref], chg=chg,
                yoy=nat[LATEST] / nat[yago] - 1, mom13=nat[LATEST] / nat[q13] - 1,
                n_up=int((sec["chg"] > 0).sum()), n=len(sec), latest=LATEST,
                sigma=sig_nat, lim=limit(sig_nat), nat_tr=nat_tr, wide_tr=wide_tr,
                n_up_beyond=int(((sec["chg"] > 0) & sec["beyond"]).sum()))


def window_dates(window: list[int]) -> tuple[pd.Timestamp, pd.Timestamp]:
    start = MONTHS[window[0]]
    end = LATEST if window[1] >= len(MONTHS) - 1 else MONTHS[window[1] + 1] - pd.Timedelta(days=1)
    return start, end


def movers(variable: str, weeks: int, k: int = hd.MULTIPLES) -> list[str]:
    sec = compute(variable, weeks)["sec"]
    return list(sec.index[:k]) + list(sec.index[-k:])


def rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha})"


def band(fig: go.Figure, trend: pd.Series, sigma: float, t: dict, name: str = "Normal variation"):
    """±3σ band around a series' own trend: readings inside it are ordinary wobble."""
    tr = trend.dropna()
    fig.add_trace(go.Scatter(x=tr.index, y=tr * (1 + Z * sigma), mode="lines", line=dict(width=0),
                             hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(x=tr.index, y=tr * (1 - Z * sigma), mode="lines", line=dict(width=0),
                             fill="tonexty", fillcolor=rgba(t["muted"], 0.18), hoverinfo="skip",
                             name=name, showlegend=False))


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------
def fig_trend(m: dict, t: dict, start, end) -> go.Figure:
    nat = m["nat"].loc[start:end]
    fig = go.Figure()
    band(fig, m["nat_tr"].loc[start:end], m["sigma"], t)
    fig.add_trace(go.Scatter(
        x=nat.index, y=nat.values, mode="lines", line=dict(color=t["up"], width=2),
        hovertemplate="%{x|%b %d, %Y}<br>Index <b>%{y:.1f}</b><br><i>click to see every sector on this date</i>"
                      "<extra></extra>"))
    fig.add_hline(y=100, line=dict(color=t["axis"], width=1))
    fig.add_annotation(x=start, y=100, text="Feb 1, 2020 = 100", showarrow=False, xanchor="left",
                       yanchor="top", yshift=-2, font=dict(color=t["muted"], size=11))
    for when, val, label, color, shift in [(m["ref"], m["then"], f"{m['period']} ago", t["muted"], -18),
                                           (m["latest"], m["now"], "Now", t["up"], 18)]:
        if start <= when <= end:
            fig.add_trace(go.Scatter(x=[when], y=[val], mode="markers", hoverinfo="skip",
                                     marker=dict(size=9, color=color, line=dict(width=2, color=t["surface"]))))
            fig.add_annotation(x=when, y=val, text=f"{label} <b>{val:.1f}</b>", showarrow=False,
                               yshift=shift, xanchor="right", font=dict(color=t["ink"], size=12))
    fig.update_layout(hd.base_layout(t, height=420, hovermode="x", clickmode="event"))
    fig.update_xaxes(showspikes=True, spikemode="across", spikethickness=1, spikecolor=t["axis"],
                     spikedash="solid")
    return fig


def fig_ranking(m: dict, t: dict, n: int, selected: str | None) -> go.Figure:
    sec = m["sec"]
    if 2 * n >= len(sec) - 1:                      # slider at "all": every sector, no gap row
        top, bottom = sec, sec.iloc[0:0]
    else:
        top, bottom = sec.head(n), sec.tail(n)
    hidden = len(sec) - len(top) - len(bottom)
    shown = pd.concat([top, bottom])
    order = list(top.index) + ([f"… {hidden} more sectors (see table)"] if hidden else []) + list(bottom.index)
    fig = go.Figure()
    groups = [("Growing, beyond normal variation", shown.query("chg > 0 and beyond"), t["up"]),
              ("Shrinking, beyond normal variation", shown.query("chg <= 0 and beyond"), t["down"]),
              ("Within normal variation", shown.query("not beyond"), t["muted"])]
    for name, part, color in groups:
        if part.empty:
            continue
        opacity = [1.0 if (selected is None or s == selected) else 0.3 for s in part.index]
        verdict = ["beyond" if b else "within" for b in part["beyond"]]
        fig.add_trace(go.Bar(
            name=name, y=part.index, x=part["chg"] * 100, orientation="h",
            marker=dict(color=color, opacity=opacity),
            customdata=np.column_stack([part["now"], part["then"], part["lim"] * 100, verdict]),
            hovertemplate="<b>%{y}</b><br>%{x:+.1f}% vs " + m["period"] + " ago"
                          "<br>index %{customdata[1]:.1f} → %{customdata[0]:.1f}"
                          "<br>normal variation ±%{customdata[2]:.1f}% → <b>%{customdata[3]}</b>"
                          "<br><i>click to drill through</i><extra></extra>"))
    # The ±3σ limit for each sector as thin ticks: the "padding" around each estimate.
    for sign in (1, -1):
        fig.add_trace(go.Scatter(
            y=shown.index, x=sign * shown["lim"] * 100, mode="markers", hoverinfo="skip",
            name="±3σ limit" if sign == 1 else None, showlegend=sign == 1,
            marker=dict(symbol="line-ns", size=11, line=dict(width=1.5, color=t["ink2"]))))
    for name in (sec.index[0], sec.index[-1]):
        v = sec.loc[name, "chg"] * 100
        fig.add_annotation(x=max(v, 0), y=name, text=f"<b>{v:+.0f}%</b>", showarrow=False,
                           xanchor="left", xshift=6, font=dict(color=t["ink"], size=12))
    fig.add_vline(x=0, line=dict(color=t["axis"], width=1))
    lo, hi = sec["chg"].min() * 100, sec["chg"].max() * 100
    fig.update_layout(hd.base_layout(
        t, height=max(300, 24 * len(order) + 70), barmode="overlay", bargap=0.35, barcornerradius=4,
        showlegend=True, clickmode="event",
        legend=dict(orientation="h", y=1.0, yanchor="bottom", x=0, font=dict(color=t["ink2"])),
        margin=dict(l=8, r=44, t=36, b=8)))
    fig.update_yaxes(categoryorder="array", categoryarray=order[::-1], showgrid=False,
                     tickfont=dict(color=t["ink2"], size=11), ticksuffix="  ")
    fig.update_xaxes(showgrid=True, ticksuffix="%", zeroline=False, nticks=5,
                     range=[min(lo, 0) * 1.25 - 2, max(hi, 0) * 1.2 + 2])
    return fig


def fig_multiple(m: dict, t: dict, sector: str, start, end, yrange) -> go.Figure:
    s = m["wide"].loc[start:end, sector]
    r = m["sec"].loc[sector]
    color = t["muted"] if not r["beyond"] else (t["up"] if r["chg"] > 0 else t["down"])
    fig = go.Figure(go.Scatter(x=s.index, y=s.values, mode="lines", line=dict(color=color, width=1.75),
                               hovertemplate="%{x|%b %d, %Y}<br>Index <b>%{y:.1f}</b>"
                                             "<br><i>click to drill through</i><extra></extra>"))
    fig.add_trace(go.Scatter(x=[s.index[-1]], y=[s.iloc[-1]], mode="markers", hoverinfo="skip",
                             marker=dict(size=7, color=color, line=dict(width=2, color=t["surface"]))))
    fig.add_hline(y=100, line=dict(color=t["axis"], width=1))
    fig.update_layout(hd.base_layout(t, height=150, hovermode="x", clickmode="event",
                                     margin=dict(l=4, r=8, t=4, b=4)))
    fig.update_xaxes(tickfont=dict(color=t["muted"], size=10), nticks=4)
    fig.update_yaxes(nticks=4, tickfont=dict(color=t["muted"], size=10),
                     **({"range": yrange} if yrange else {}))
    return fig


# ---------------------------------------------------------------------------
# HTML pieces (components, not strings, except reused SVG/HTML from the static build)
# ---------------------------------------------------------------------------
def raw(html_str: str, **kw) -> dcc.Markdown:
    return dcc.Markdown(html_str, dangerously_allow_html=True, **kw)


def delta_n(x: float, lim: float) -> str:
    """Like hd.delta, but a change inside normal variation renders neutral with ≈."""
    if abs(x) <= lim:
        return f'<span class="delta flat"><span aria-hidden="true">≈</span> {hd.pct(x)}</span>'
    return hd.delta(x)


def verdict(x: float, lim: float) -> str:
    return (f"within normal variation (±{lim * 100:.1f}%)" if abs(x) <= lim
            else f"beyond normal variation (±{lim * 100:.1f}%)")


def tiles(m: dict, start) -> html.Div:
    spark = hd.sparkline(m["nat"].loc[max(start, LATEST - pd.DateOffset(years=1)):])
    third = (("13-week momentum", m["mom13"], "vs 13 weeks ago") if m["weeks"] == 52
             else ("Year over year", m["yoy"], "fixed reference, whatever the slider says"))
    return html.Div(className="tiles", children=[
        html.Div(className="tile", children=[
            html.Div("Postings index (SA)", className="t-label"),
            html.Div(className="t-row", children=[html.Div(f"{m['now']:.1f}", className="t-value"), raw(spark)]),
            html.Div("Feb 1, 2020 = 100 · last 12 months", className="t-note")]),
        html.Div(className="tile", children=[
            html.Div(f"Change vs {m['period']} ago", className="t-label"),
            raw(f'<div class="t-value">{delta_n(m["chg"], m["lim"])}</div>'),
            html.Div(f"vs {m['then']:.1f} on {m['ref']:%b %d, %Y} · {verdict(m['chg'], m['lim'])}",
                     className="t-note")]),
        html.Div(className="tile", children=[
            html.Div(third[0], className="t-label"),
            raw(f'<div class="t-value">{delta_n(third[1], m["lim"])}</div>'),
            html.Div(f"{third[2]} · {verdict(third[1], m['lim'])}", className="t-note")]),
        html.Div(className="tile", children=[
            html.Div("Breadth", className="t-label"),
            html.Div([f"{m['n_up']} ", html.Span(f"of {m['n']}", className="t-of")], className="t-value"),
            html.Div(className="meter", children=html.Span(style={"width": f"{m['n_up'] / m['n'] * 100:.1f}%"})),
            html.Div(f"sectors up vs {m['period']} ago · {m['n_up_beyond']} of them beyond normal variation",
                     className="t-note")]),
    ])


def title(m: dict) -> str:
    vs = m["now"] - 100
    label = "total postings" if m["variable"] == "total postings" else "new postings"
    return (f"US {label} are {abs(vs):.1f}% {'above' if vs >= 0 else 'below'} pre-pandemic and "
            f"{hd.pct(m['chg'])} vs {m['period']} ago"
            f"{' (within normal variation)' if abs(m['chg']) <= m['lim'] else ''}. "
            f"{m['n_up']} of {m['n']} sectors are growing, "
            f"led by {m['sec'].index[0]}.")


def table_rows(m: dict) -> list[dict]:
    return [dict(id=s, sector=s, titles=TITLES.get(s) if isinstance(TITLES.get(s), str) else "—",
                 index=round(r["now"], 1), chg=round(r["chg"] * 100, 1),
                 lim=round(r["lim"] * 100, 1), beyond="yes" if r["beyond"] else "no",
                 yoy=round((r["now"] / m["wide"].loc[LATEST - pd.DateOffset(years=1), s] - 1) * 100, 1))
            for s, r in m["sec"].iterrows()]


TABLE_STYLE = dict(
    style_as_list_view=True, page_size=12, sort_action="native", filter_action="native",
    style_table={"overflowX": "auto"},          # wide tables scroll inside their card on phones
    style_header={"backgroundColor": "var(--surface)", "color": "var(--ink2)", "fontWeight": 600,
                  "borderBottom": "1px solid var(--axis)"},
    style_filter={"backgroundColor": "var(--surface)", "color": "var(--ink)"},
    style_cell={"backgroundColor": "var(--surface)", "color": "var(--ink)", "border": "none",
                "borderBottom": "1px solid var(--grid)", "fontFamily": hd.FONT, "fontSize": 13,
                "padding": "6px", "textAlign": "left"},
    style_cell_conditional=[{"if": {"column_type": "numeric"}, "textAlign": "right",
                             "fontVariantNumeric": "tabular-nums"}],
)


def records_table(df: pd.DataFrame, highlight_dates: list[str] | None = None, tid: str = "drill-records"):
    cols = [{"name": c, "id": c, "type": "numeric" if pd.api.types.is_numeric_dtype(df[c]) else "text"}
            for c in df.columns]
    cond = [{"if": {"filter_query": f'{{date}} = "{d}"'}, "backgroundColor": "var(--wash)", "fontWeight": 600}
            for d in (highlight_dates or [])]
    return dash_table.DataTable(id=tid, data=df.to_dict("records"), columns=cols, export_format="csv",
                                export_headers="display", style_data_conditional=cond, **TABLE_STYLE)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
app = Dash(__name__, title="Hiring Demand Monitor", suppress_callback_exceptions=True)

# ---------------------------------------------------------------------------
# Experiment (ablab). Off unless AB_EXPERIMENT is set: everyone sees arm A, nothing
# is logged. While it is on, build_hiringlab.py refuses to refresh the data: the
# content is something both arms hold constant. Design: ../docs/experiment_design.md
# ---------------------------------------------------------------------------
EXP = None
if os.environ.get("AB_EXPERIMENT"):
    EXP = Experiment(os.environ["AB_EXPERIMENT"], salt=os.environ["AB_SALT"],
                     split=int(os.environ.get("AB_SPLIT", "50")),
                     events=("drill_through",), conversion="drill_through",
                     cookie_secure=os.environ.get("COOKIE_SECURE", "1") == "1")
    EXP.init_app(app.server)
EXTRA_CSS = """
.controls{display:grid;grid-template-columns:auto repeat(3,minmax(0,1fr));gap:12px 24px;align-items:start;
  background:var(--surface);box-shadow:0 0 0 1px var(--ring);border-radius:10px;padding:12px 14px;margin:14px 0 4px}
.ctl label,.ctl .lab{display:block;font-size:12.5px;color:var(--ink2);margin-bottom:4px;font-weight:600}
.ctl .help{font-size:11.5px;color:var(--muted);margin-top:2px}
.seg-radio{display:inline-flex;border:1px solid var(--ring);border-radius:8px;padding:2px}
.seg-radio label{margin:0!important;padding:5px 10px;border-radius:6px;cursor:pointer;color:var(--ink2);font-weight:400!important}
.seg-radio input{display:none}
.seg-radio label:has(input:checked){background:var(--wash);color:var(--ink);font-weight:600!important}
.mult-controls{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center;margin:4px 0 8px}
.mult-controls .dd{flex:1 1 420px;min-width:0}
.btn{border:1px solid var(--ring);background:var(--surface);color:var(--ink);border-radius:8px;padding:5px 10px;
  font:inherit;font-size:12.5px;cursor:pointer} .btn:hover{background:var(--wash)}
.drill{border-left:3px solid var(--series-up)}
.drill-head{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}
.calc{background:var(--wash);border-radius:8px;padding:10px 12px;margin:8px 0;font-size:13px}
.calc code{font-size:12.5px}
.hint-click{color:var(--muted);font-size:12px}
.panel .p-title{cursor:default}
.panel > div{min-width:0;overflow:hidden}
.delta.flat{color:var(--ink2)}
.tabs{display:inline-flex;gap:4px;border-bottom:1px solid var(--axis);margin:14px 0 0;width:100%}
.tabs label{padding:8px 14px;cursor:pointer;color:var(--ink2);border-bottom:2px solid transparent;margin:0 0 -1px!important}
.tabs input{display:none}
.tabs label:has(input:checked),.tabs label.selected{color:var(--ink)!important;font-weight:600;border-bottom-color:var(--series-up)}
.dims{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:8px;margin-bottom:12px}
.dim{background:var(--surface);box-shadow:0 0 0 1px var(--ring);border-radius:10px;padding:10px 12px}
.dim-name{font-weight:600;font-size:13px}
.dim.pass .dim-icon{color:var(--good)} .dim.warn .dim-icon{color:var(--warn)} .dim.fail .dim-icon{color:var(--bad)}
.dim.fail{box-shadow:0 0 0 1.5px var(--bad)}
.t-value.ok,.sub.ok{color:var(--good)} .t-value.no,.sub.no{color:var(--bad)} .t-value.wa{color:var(--warn)}
.sim-banner{background:var(--wash);border-left:3px solid var(--bad);border-radius:6px;padding:10px 12px;margin:8px 0 12px}
.dq-controls{grid-template-columns:minmax(0,1fr)}
@media (max-width:960px){.dims{grid-template-columns:repeat(2,minmax(0,1fr))}}
.dash-slider-mark{color:var(--ink2)!important;font-size:11.5px}
.dash-slider-mark-outside-selection{color:var(--muted)!important}
.dash-slider-track{background:var(--grid)!important}
.dash-slider-range{background:var(--series-up)!important}
.dash-slider-thumb{background:var(--surface)!important;border:2px solid var(--series-up)!important;box-shadow:none!important}
.dash-dropdown,.Select-control{background:var(--surface)!important;color:var(--ink)!important}
.dash-table-container .previous-next-container{color:var(--ink2)}
.dash-spreadsheet-menu button{background:var(--surface);color:var(--ink);border:1px solid var(--ring);border-radius:6px}
@media (max-width:960px){.controls{grid-template-columns:minmax(0,1fr)}}
"""
app.index_string = f"""<!DOCTYPE html>
<html lang="en"><head>{{%metas%}}<title>{{%title%}}</title>{{%favicon%}}{{%css%}}
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>{hd.CSS}{EXTRA_CSS}{references.CSS}</style></head>
<body>{{%app_entry%}}<footer>{{%config%}}{{%scripts%}}{{%renderer%}}</footer></body></html>"""

DQ_LAYOUT, register_dq = dq_page.build(lambda: D, TABLE_STYLE, {"displayModeBar": False},
                                       lambda h: raw(h))

DEFAULT_WINDOW = [MONTHS.index(pd.Timestamp("2022-01-01")), len(MONTHS) - 1]
year_marks = {i: {"label": str(mo.year)} for i, mo in enumerate(MONTHS) if mo.month == 1}
GRAPH_CFG = {"displayModeBar": False}   # height comes from each figure's layout

LAYOUT = html.Main([
    dcc.Location(id="url"),
    dcc.Store(id="theme", data="light"),
    dcc.Store(id="drill", data=None),
    html.Header([
        html.P("Hiring demand monitor · US", className="kicker"),
        html.P(["For a weekly go-to-market review: ",
                html.B("where is employer hiring demand growing, and how broad is it?")], className="purpose"),
        html.Div(className="meta", children=[
            html.Span(["Data through ", html.B(f"{LATEST:%b %d, %Y}")]),
            html.Span(f"Loaded {datetime.now():%b %d, %Y %H:%M}"),
            html.Span("Source: Indeed Hiring Lab, CC BY 4.0"), html.Span("Owner: godot107"),
            raw(hd.dq_strip(CHECKS))]),
    ]),
    dcc.RadioItems(id="page", value="dashboard", inline=True, className="tabs",
                   options=[{"label": "Dashboard", "value": "dashboard"},
                            {"label": "Data quality", "value": "dq"},
                            {"label": "About & references", "value": "about"}]),
    html.Div(id="page-dashboard", children=[
    # ---- one filter row, scoping everything below ----
    html.Div(className="controls", children=[
        html.Div(className="ctl", children=[
            html.Span("Series", className="lab"),
            dcc.RadioItems(id="series", options=[{"label": v, "value": k} for k, v in SERIES.items()],
                           value="total postings", className="seg-radio", inline=True),
            html.Div("New postings react to demand first", className="help")]),
        html.Div(className="ctl", children=[
            html.Span("Compare to", className="lab"),
            dcc.Slider(id="weeks", min=0, max=len(STOPS) - 1, step=1, value=len(STOPS) - 1,
                       marks={i: PERIODS[w].replace(" weeks", " wk") for i, w in enumerate(STOPS)},
                       allow_direct_input=False),
            html.Div("Drives every % change, the ranking and breadth", className="help")]),
        html.Div(className="ctl", children=[
            html.Span("Date window", className="lab"),
            dcc.RangeSlider(id="window", min=0, max=len(MONTHS) - 1, step=1, value=DEFAULT_WINDOW,
                            marks=year_marks, allowCross=False, updatemode="mouseup",
                            allow_direct_input=False),
            html.Div(id="window-label", className="help")]),
        html.Div(className="ctl", children=[
            html.Span("Sectors in ranking (top + bottom)", className="lab"),
            dcc.Slider(id="topn", min=3, max=24, step=1, value=10,   # 24 = "all" (clear of 23)
                       marks={3: "3", 10: "10", 15: "15", 24: "all"},
                       tooltip={"placement": "bottom", "always_visible": False}, allow_direct_input=False),
            html.Div("Slide to \"all\" to rank every sector", className="help")]),
    ]),
    html.H1(id="title"),
    html.Div(id="tiles"),
    html.Div(className="grid", children=[
        html.Div(className="card wide", children=[
            html.H2("National postings index"),
            html.P(["Seasonally adjusted, 7-day trailing average · Feb 1, 2020 = 100 · "
                    "grey band = normal variation (±3σ around the 29-day trend) · ",
                    html.Span("click the line to see every sector on that date", className="hint-click")],
                   className="sub"),
            dcc.Graph(id="trend", config=GRAPH_CFG)]),
        html.Div(className="card", children=[
            html.H2("Which sectors are growing?"),
            html.P(["Change by sector · grey = within normal variation, ticks = each sector's ±3σ limit · ",
                    html.Span("click a bar to drill through",
                                                                 className="hint-click")], className="sub"),
            dcc.Graph(id="rank", config=GRAPH_CFG)]),
    ]),
    # ---- drill-through panel (appears on click) ----
    html.Div(id="drill-panel", className="card drill", style={"display": "none"},
             children=[html.Button(id="drill-clear")]),   # Close button must exist from the start
    html.Div(className="card", children=[
        html.H2("Small multiples"),
        html.P(["Pick any sectors to compare · ", html.Span("click a panel to drill through",
                                                             className="hint-click")], className="sub"),
        html.Div(className="mult-controls", children=[
            html.Div(className="dd", children=dcc.Dropdown(
                id="mult-sectors", options=sorted(D["sector"]["sector"].unique()),
                value=movers("total postings", 52), multi=True, placeholder="Choose sectors…")),
            html.Button("Reset to biggest movers", id="mult-reset", className="btn"),
            dcc.Checklist(id="shared", options=[{"label": " Same y-scale on every panel", "value": "shared"}],
                          value=["shared"], inline=True),
        ]),
        html.Div(id="shared-note", className="hint-click"),
        html.Div(id="multiples", className="multiples"),
    ]),
    html.Div(id="earnings-card", className="card", children=[
        html.H2("Why this index matters to Indeed's earnings"),
        html.P(["Recruit's US ARPJ = HR Tech US revenue ÷ total US job postings, and that denominator "
                "\"is measured by the Indeed Hiring Lab US Job Postings Index.\" · ",
                html.Span("click a bar to see the daily rows averaged into it", className="hint-click")],
               className="sub"),
        html.Div(className="earn", children=[dcc.Graph(id="earnings", config=GRAPH_CFG), html.Div(id="tree")]),
    ]),
    html.Div(className="card", children=[
        html.H2("All sectors: table view"),
        html.P(["Sort, filter, or export · ", html.Span("click a row to drill through", className="hint-click")],
               className="sub"),
        dash_table.DataTable(
            id="sector-table", export_format="csv",
            columns=[{"name": "Sector", "id": "sector"}, {"name": "Example titles", "id": "titles"},
                     {"name": "Index", "id": "index", "type": "numeric"},
                     {"name": "Change %", "id": "chg", "type": "numeric"},
                     {"name": "Normal ±%", "id": "lim", "type": "numeric"},
                     {"name": "Beyond?", "id": "beyond"},
                     {"name": "YoY %", "id": "yoy", "type": "numeric"}],
            style_data_conditional=[
                {"if": {"filter_query": '{beyond} = "yes" && {chg} > 0', "column_id": "chg"}, "color": "var(--good)"},
                {"if": {"filter_query": '{beyond} = "yes" && {chg} <= 0', "column_id": "chg"}, "color": "var(--bad)"},
                {"if": {"filter_query": '{beyond} = "no"', "column_id": "chg"}, "color": "var(--ink2)"},
                {"if": {"column_id": "titles"}, "color": "var(--muted)"}],
            **TABLE_STYLE),
    ]),
    raw("""<footer class="card"><h2>Definitions &amp; caveats</h2><ul>
      <li><b>Index</b>: seasonally adjusted Indeed job postings, 7-day trailing average, 100 = Feb 1, 2020.</li>
      <li><b>Change</b> = index at the latest date ÷ index N weeks earlier − 1 (the "Compare to" slider).
        "1 year" uses the same calendar date last year. Both points share one base, so it's an exact % change.</li>
      <li><b>Growth, not size.</b> Every sector is indexed to its own 2020 level and there are no counts.
        Rankings show direction, not where most jobs are.</li>
      <li><b>Normal variation</b>: each series wobbles around its own 29-day trend; σ is that wobble over
        the last 3 years. A change compares two readings, so it counts as <b>beyond normal variation</b> only if it
        exceeds 3·√2·σ (a 3σ control limit). Grey and "≈" mean the move is within ordinary wobble. It's a screening
        rule, not a significance test: the series is smoothed and autocorrelated.</li>
      <li><b>Base effects</b>: short windows are noisy, long windows carry last year's shocks. Compare two before
        calling a trend.</li>
      <li><b>Postings are demand signals</b>, not hires and not revenue. Public Hiring Lab data, not internal data.
        Earnings figures are as stated on Recruit's earnings-call transcripts.</li>
      <li>Full glossary, the earnings-link reasoning, methods, and source links: <b>About &amp; references</b> tab.</li></ul></footer>"""),
    ]),                                             # end page-dashboard
    DQ_LAYOUT,
    html.Div(id="page-about", style={"display": "none"}, children=[
        html.H1("About this dashboard: definitions, methods, and sources"),
        raw(references.about_html())]),
    # Same text in both arms, and only while an experiment is running.
    *([html.P("This page is running an A/B test on its own layout. One first-party cookie "
              "holds a random id so your layout stays consistent; no personal data, no IP, "
              "no third-party scripts. Honors Do Not Track and Global Privacy Control.",
              className="hint-click", id="ab-notice")] if EXP else []),
])



def charts_first(layout: html.Main) -> html.Main:
    """exp001 arm B: the trend + ranking grid above the KPI tiles. Same components,
    same ids, same callbacks; only the order differs, so order is the only confound."""
    layout = copy.deepcopy(layout)
    kids = layout["page-dashboard"].children
    i = next(k for k, c in enumerate(kids) if getattr(c, "id", None) == "tiles")
    kids[i], kids[i + 1] = kids[i + 1], kids[i]
    return layout


LAYOUTS = {"A": LAYOUT, "B": charts_first(LAYOUT)}


def serve_layout():
    """Dash fetches the layout once per page load (/_dash-layout): that request is the
    exposure, and the arm picks the layout. Anything else that calls this (startup
    validation) logs nothing and gets the control."""
    variant = EXP.expose() if EXP and request.path.endswith("_dash-layout") else None
    return LAYOUTS[variant or "A"]


app.layout = serve_layout
register_dq(app)

PAGES = ["dashboard", "dq", "about"]


@app.callback([Output(f"page-{p}", "style") for p in PAGES], Input("page", "value"))
def switch_page(page):
    return [{} if p == page else {"display": "none"} for p in PAGES]

# Pick light/dark from the viewer's OS setting (figures are built per theme, not auto-inverted).
clientside_callback(
    "function(_) { const t = document.documentElement.dataset.theme;"
    " return t ? t : (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'); }",
    Output("theme", "data"), Input("url", "pathname"))


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------
@app.callback(Output("mult-sectors", "value"),
              Input("series", "value"), Input("weeks", "value"), Input("mult-reset", "n_clicks"),
              prevent_initial_call=True)
def reset_multiples(variable, stop, _):
    return movers(variable, STOPS[stop])


@app.callback(
    Output("title", "children"), Output("tiles", "children"), Output("window-label", "children"),
    Output("trend", "figure"), Output("rank", "figure"),
    Output("multiples", "children"), Output("shared-note", "children"),
    Output("earnings-card", "style"), Output("earnings", "figure"), Output("tree", "children"),
    Output("sector-table", "data"),
    Input("series", "value"), Input("weeks", "value"), Input("window", "value"), Input("topn", "value"),
    Input("mult-sectors", "value"), Input("shared", "value"), Input("theme", "data"), Input("drill", "data"))
def render(variable, stop, window, topn, picks, shared, theme, drill):
    weeks = STOPS[stop]
    t = hd.THEMES[theme or "light"]
    m = compute(variable, weeks)
    start, end = window_dates(window)
    selected = drill.get("sector") if drill else None

    picks = [p for p in (picks or []) if p in m["sec"].index]
    yrange = None
    if "shared" in (shared or []) and picks:
        w = m["wide"].loc[start:end, picks]
        lo, hi = min(w.min().min(), 100), max(w.max().max(), 100)
        yrange = [lo - (hi - lo) * 0.06, hi + (hi - lo) * 0.06]
    panels = [html.Div(className="panel", children=[
        raw(f'<div class="p-title">{html_lib.escape(s)} '
            f'{delta_n(m["sec"].loc[s, "chg"], m["sec"].loc[s, "lim"])}</div>'),
        dcc.Graph(id={"type": "mult", "index": s}, config=GRAPH_CFG,
                  figure=fig_multiple(m, t, s, start, end, yrange))]) for s in picks]
    note = ("" if yrange else "⚠ Independent y-scales: each panel stretches to fill its box, so small moves "
            "look as dramatic as big ones. Turn the shared scale back on to compare panels honestly.")

    earn_style = {} if variable == "total postings" else {"display": "none"}
    if variable == "total postings":
        em = dict(nat=m["nat"], latest=LATEST)
        earn_fig, tree = hd.fig_earnings(em, t), raw(hd.earnings_tree(em))
    else:
        earn_fig, tree = go.Figure(), ""

    return (title(m), tiles(m, start), f"{start:%b %Y} – {end:%b %d, %Y}",
            fig_trend(m, t, start, end), fig_ranking(m, t, topn, selected),
            panels, note, earn_style, earn_fig, tree, table_rows(m))


@app.callback(
    Output("drill", "data"),
    Output("rank", "clickData"), Output("trend", "clickData"), Output("earnings", "clickData"),
    Output({"type": "mult", "index": ALL}, "clickData"), Output("sector-table", "active_cell"),
    Input("rank", "clickData"), Input("trend", "clickData"), Input("earnings", "clickData"),
    Input({"type": "mult", "index": ALL}, "clickData"), Input("sector-table", "active_cell"),
    Input("drill-clear", "n_clicks"),
    State({"type": "mult", "index": ALL}, "id"),
    prevent_initial_call=True)
def on_click(rank, trend, earn, mults, cell, _clear, mult_ids):
    """One place that turns any click into a drill target, then clears the click so
    clicking the same mark again still fires."""
    trig = ctx.triggered_id
    target = no_update
    if trig == "drill-clear":
        target = None
    elif trig == "rank" and rank:
        y = rank["points"][0]["y"]
        if not y.startswith("…"):
            target = {"kind": "sector", "sector": y}
    elif trig == "trend" and trend:
        target = {"kind": "date", "date": trend["points"][0]["x"][:10]}
    elif trig == "earnings" and earn:
        target = {"kind": "quarter", "fq": earn["points"][0]["x"].replace(" (QTD)", "")}
    elif isinstance(trig, dict) and trig.get("type") == "mult":
        target = {"kind": "sector", "sector": trig["index"]}
    elif trig == "sector-table" and cell:
        target = {"kind": "sector", "sector": cell["row_id"]}
    if EXP and isinstance(target, dict):
        # The conversion, logged when the drill actually opens. The arm comes from the
        # cookie inside track(), never from the click.
        EXP.track("drill_through", "mult" if isinstance(trig, dict) else trig)
    return target, None, None, None, [None] * len(mult_ids), None


@app.callback(Output("drill-panel", "children"), Output("drill-panel", "style"),
              Input("drill", "data"), Input("series", "value"), Input("weeks", "value"),
              Input("window", "value"), Input("theme", "data"))
def render_drill(drill, variable, stop, window, theme):
    weeks = STOPS[stop]
    hidden = [html.Button(id="drill-clear")], {"display": "none"}
    if not drill:
        return hidden
    t = hd.THEMES[theme or "light"]
    m = compute(variable, weeks)
    start, end = window_dates(window)
    nat, wide = m["nat"], m["wide"]
    head = lambda title_, sub: html.Div(className="drill-head", children=[
        html.Div([html.H2(f"Drill-through: {title_}"), html.P(sub, className="sub")]),
        html.Button("✕ Close", id="drill-clear", className="btn")])

    if drill["kind"] == "sector":
        s = drill["sector"]
        if s not in wide.columns:
            return hidden
        col = wide.loc[start:end, s]
        lagged = wide[s].reindex(col.index.map(lambda d: ref_date(d, weeks)))
        recs = pd.DataFrame({"date": col.index.strftime("%Y-%m-%d"), "sector": s, "series": variable,
                             "index": col.values.round(2),
                             f"index {m['period']} earlier": lagged.values.round(2),
                             f"change vs {m['period']} %": ((col.values / lagged.values - 1) * 100).round(1),
                             "national index": nat.reindex(col.index).values.round(2)}).iloc[::-1]
        now, then = wide.loc[LATEST, s], wide.loc[m["ref"], s]
        calc = html.Div(className="calc", children=raw(
            f"The bar is built from exactly two rows (highlighted below):<br>"
            f"<code>{LATEST:%Y-%m-%d}</code> index <b>{now:.2f}</b> ÷ "
            f"<code>{m['ref']:%Y-%m-%d}</code> index <b>{then:.2f}</b> − 1 = <b>{(now / then - 1) * 100:+.1f}%</b>"
            f"<br>Normal variation for this sector: readings wobble σ = {m['sec'].loc[s, 'sigma'] * 100:.2f}% around "
            f"their trend, so a change needs to exceed ±{m['sec'].loc[s, 'lim'] * 100:.1f}% (3·√2·σ) → "
            f"<b>{'beyond' if m['sec'].loc[s, 'beyond'] else 'within'} normal variation</b>"
            f"<br>Example titles: {html_lib.escape(TITLES.get(s)) if isinstance(TITLES.get(s), str) else '— (not in lookup table)'}"))
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=nat.loc[start:end].index, y=nat.loc[start:end].values, name="National",
                                 mode="lines", line=dict(color=t["muted"], width=1.5),
                                 hovertemplate="National %{y:.1f}<extra></extra>"))
        r = m["sec"].loc[s]
        color = t["muted"] if not r["beyond"] else (t["up"] if r["chg"] > 0 else t["down"])
        band(fig, m["wide_tr"].loc[start:end, s], r["sigma"], t)
        fig.add_trace(go.Scatter(x=col.index, y=col.values, name=s, mode="lines", line=dict(color=color, width=2),
                                 hovertemplate=f"{html_lib.escape(s)} %{{y:.1f}}<extra></extra>"))
        fig.add_hline(y=100, line=dict(color=t["axis"], width=1))
        fig.update_layout(hd.base_layout(t, height=260, hovermode="x unified", showlegend=True,
                                         legend=dict(orientation="h", y=1.0, yanchor="bottom", x=0,
                                                     font=dict(color=t["ink2"])),
                                         margin=dict(l=8, r=8, t=30, b=8)))
        return [head(s, f"{len(recs):,} daily records in the date window · {SERIES[variable]} · "
                        "grey = national, for context · export with the button above the table"),
                calc, dcc.Graph(figure=fig, config=GRAPH_CFG),
                records_table(recs, [f"{LATEST:%Y-%m-%d}", f"{m['ref']:%Y-%m-%d}"])], {}

    if drill["kind"] == "date":
        d = pd.Timestamp(drill["date"])
        if d not in wide.index:
            return hidden
        ref = ref_date(d, weeks)
        recs = pd.DataFrame({"date": d.strftime("%Y-%m-%d"), "sector": wide.columns,
                             "index": wide.loc[d].values.round(2),
                             f"index {m['period']} earlier": wide.reindex([ref]).iloc[0].values.round(2)})
        recs[f"change vs {m['period']} %"] = ((recs["index"] / recs[f"index {m['period']} earlier"] - 1) * 100).round(1)
        recs = recs.sort_values(f"change vs {m['period']} %", ascending=False)
        up = int((recs[f"change vs {m['period']} %"] > 0).sum())
        calc = html.Div(className="calc", children=raw(
            f"National index on <code>{d:%Y-%m-%d}</code>: <b>{nat[d]:.2f}</b> · "
            f"vs {m['period']} earlier ({ref:%Y-%m-%d}): <b>{hd.pct(nat[d] / nat[ref] - 1) if ref in nat.index else 'n/a'}</b>"
            f" · breadth that day: <b>{up} of {len(recs)}</b> sectors up"))
        return [head(f"{d:%b %d, %Y}", f"All {len(recs)} sectors on this date · {SERIES[variable]}"),
                calc, records_table(recs)], {}

    if drill["kind"] == "quarter":
        fq = drill["fq"]
        fy, q = int(fq[2:6]), int(fq[-1])
        q_start = pd.Timestamp(year=fy, month=4, day=1) + pd.DateOffset(months=3 * (q - 1))
        q_end = q_start + pd.DateOffset(months=3) - pd.Timedelta(days=1)
        p_start, p_end = q_start - pd.DateOffset(years=1), q_end - pd.DateOffset(years=1)
        tot, _ = series("total postings")
        cur, prev = tot.loc[q_start:q_end], tot.loc[p_start:p_end]
        recs = pd.concat([pd.DataFrame({"date": cur.index.strftime("%Y-%m-%d"), "fiscal quarter": fq,
                                        "national index": cur.values.round(2)}),
                          pd.DataFrame({"date": prev.index.strftime("%Y-%m-%d"),
                                        "fiscal quarter": f"FY{fy - 1} Q{q} (prior year)",
                                        "national index": prev.values.round(2)})])
        rep = {r["fq"]: r for r in hd.RECRUIT}.get(fq)
        calc = html.Div(className="calc", children=raw(
            f"Mean of <b>{len(cur)}</b> daily rows ({q_start:%b %d, %Y} – {min(q_end, LATEST):%b %d, %Y}) = "
            f"<b>{cur.mean():.2f}</b> · mean of <b>{len(prev)}</b> prior-year rows = <b>{prev.mean():.2f}</b> · "
            f"YoY = <b>{(cur.mean() / prev.mean() - 1) * 100:+.1f}%</b>"
            + (f"<br>Recruit reported postings <b>~{rep['postings']}%</b>, ARPJ <b>+{rep['arpj']}%</b> "
               f"(call: {rep['call']})" if rep else "<br>No Recruit figure for this quarter.")
            + ("<br>⚠ Quarter in progress (QTD)." if q_end > LATEST else "")))
        return [head(f"{fq} (Recruit fiscal quarter)", "Total postings, national · the rows behind the bar"),
                calc, records_table(recs)], {}
    return hidden


# ---------------------------------------------------------------------------
# Serving
# ---------------------------------------------------------------------------
server = app.server          # WSGI entry point: gunicorn hiringlab_app:server


@server.route("/healthz")
def healthz():
    """Liveness + trust in one probe: is the data fresh and are blocking checks passing?"""
    blocking = [r for r in DQ_RESULTS if r.severity == "block"]
    failed = [r.name for r in blocking if not r.passed]
    body = {"status": "ok" if not failed else "degraded", "data_through": f"{LATEST:%Y-%m-%d}",
            "blocking_passed": f"{len(blocking) - len(failed)}/{len(blocking)}", "failed": failed}
    return body, 200          # 200 either way: the app is up; "degraded" is for humans and monitors


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Run the Hiring Demand Monitor (dev server).")
    ap.add_argument("--dev", action="store_true", help="hot reload + Dash dev tools (never in production)")
    args = ap.parse_args()
    app.run(debug=args.dev or os.environ.get("DASH_DEBUG") == "1",
            host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8050")))
