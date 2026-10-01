"""The Data Quality tab for hiringlab_app.py.

Layout + callbacks for: a fault simulator, summary tiles, a dimension scorecard, the
checks table (click a row → its evidence rows), the Pydantic contract and its errors,
an individuals / moving-range control chart, and run history.
"""
from __future__ import annotations

from functools import lru_cache

import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, dash_table, dcc, html, no_update

import dq_checks as q
import hiringlab_dashboard as hd

ICON = {"pass": "✓", "warn": "⚠", "fail": "✗"}
DIMENSIONS = ["Validity", "Uniqueness", "Completeness", "Timeliness", "Consistency", "Reasonableness"]
DIM_HELP = {
    "Validity": "rows match the contract; values in range",
    "Uniqueness": "one row per key (the grain)",
    "Completeness": "nothing missing: days, sectors, reference dates",
    "Timeliness": "data arrived when expected",
    "Consistency": "tables and lookups agree",
    "Reasonableness": "values plausible vs their own history",
}


def build(get_data, table_style: dict, graph_cfg: dict, raw):
    """Return (layout, register) bound to the app's data and styles."""

    @lru_cache(maxsize=None)
    def run(fault: str):
        data = q.apply_fault(get_data(), fault)
        results, errors = q.run_checks(data)
        return data, results, errors

    layout = html.Div(id="page-dq", style={"display": "none"}, children=[
        html.H1("Data quality: can these numbers be trusted today?"),
        html.P(["Every check has a quality dimension (DAMA-DMBOK) and a severity. ",
                html.B("Block"), " = the numbers can't be trusted; ", html.B("warn"),
                " = review, still usable. Record-level rules are a Pydantic contract; dataset-level rules are "
                "checks no single row can answer."], className="purpose"),
        html.Div(className="controls dq-controls", children=[
            html.Div(className="ctl", children=[
                html.Span("Simulate a fault", className="lab"),
                dcc.Dropdown(id="dq-fault", clearable=False, value="none",
                             options=[{"label": lbl, "value": k} for k, (lbl, _) in q.FAULTS.items()]),
                html.Div("Plants a known problem in a COPY of the data so you can see which check catches it. "
                         "The Dashboard tab always uses the real data.", className="help")]),
        ]),
        html.Div(id="dq-banner"),
        html.Div(id="dq-tiles"),
        html.Div(id="dq-dims", className="dims"),
        html.Div(className="card", children=[
            html.H2("Checks"),
            html.P(["Sorted by severity · ", html.Span("click a check to see the rows behind it",
                                                         className="hint-click")], className="sub"),
            dash_table.DataTable(
                id="dq-table",
                columns=[{"name": "", "id": "icon"}, {"name": "Check", "id": "name"},
                         {"name": "Dimension", "id": "dimension"}, {"name": "Severity", "id": "severity"},
                         {"name": "Observed", "id": "observed"}, {"name": "Expected", "id": "expected"},
                         {"name": "Rows", "id": "rows_affected", "type": "numeric"}],
                style_data_conditional=[
                    {"if": {"filter_query": '{status} = "pass"', "column_id": "icon"}, "color": "var(--good)"},
                    {"if": {"filter_query": '{status} = "warn"', "column_id": "icon"}, "color": "var(--warn)"},
                    {"if": {"filter_query": '{status} = "fail"', "column_id": "icon"}, "color": "var(--bad)",
                     "fontWeight": 700},
                    {"if": {"filter_query": '{status} = "fail"'}, "backgroundColor": "var(--wash)"}],
                **{**table_style, "page_size": 15, "filter_action": "none"}),
        ]),
        html.Div(id="dq-evidence", className="card drill"),
        html.Div(className="grid", children=[
            html.Div(className="card", children=[
                html.H2("Record contract (Pydantic)"),
                html.P("Every row of both tables is validated against these models. Pydantic reports the exact row "
                       "and field of each failure.", className="sub"),
                dash_table.DataTable(
                    data=q.contract_rules(),
                    columns=[{"name": c.title(), "id": c} for c in ("table", "field", "type", "rule")],
                    **{**table_style, "page_size": 10, "filter_action": "none", "sort_action": "none"}),
                html.Div(id="dq-contract-errors")]),
            html.Div(className="card", children=[
                html.H2("Run history"),
                html.P("Each app start records the real-data results. Trend beats a snapshot "
                       "(Sebastian-Coleman: \"measurement data can be used for trend analysis\").", className="sub"),
                html.Div(id="dq-history")]),
        ]),
        html.Div(className="card", children=[
            html.H2("Control chart: day-over-day change"),
            html.P("Individuals chart: each dot is one day's % change. Lines are ±3σ of the series' own daily "
                   "changes over the last 3 years. Dots outside are special-cause signals: real news or a bad "
                   "load. Try the Spike fault on Banking & Finance.", className="sub"),
            html.Div(className="mult-controls", children=[
                html.Div(className="dd", children=dcc.Dropdown(
                    id="dq-series", clearable=False, value="National",
                    # options must exist at load, or Dash clears the value to None
                    options=["National"] + sorted(get_data()["sector"]["sector"].unique()))),
                dcc.RadioItems(id="dq-var", value="total postings", inline=True, className="seg-radio",
                               options=[{"label": "Total", "value": "total postings"},
                                        {"label": "New", "value": "new postings"}]),
            ]),
            dcc.Graph(id="dq-control", config=graph_cfg)]),
    ])

    def register(app):
        @app.callback(
            Output("dq-banner", "children"), Output("dq-tiles", "children"), Output("dq-dims", "children"),
            Output("dq-table", "data"), Output("dq-contract-errors", "children"), Output("dq-history", "children"),
            Output("dq-series", "options"),
            Input("dq-fault", "value"), Input("page", "value"))
        def render(fault, page):
            if page != "dq":
                return (no_update,) * 7
            data, results, errors = run(fault)
            banner = ("" if fault == "none" else html.Div(className="sim-banner", children=[
                html.B("SIMULATED FAULT: "), q.FAULTS[fault][0], ". These results are for a planted copy, "
                "not the real data."]))
            blocking = [r for r in results if r.severity == "block"]
            failed = [r for r in blocking if not r.passed]
            warns = [r for r in results if r.severity == "warn" and not r.passed]
            n_rows = len(data["sector"]) + len(data["national"])
            bad_rows = sum(r.rows_affected for r in results if r.name.startswith("Contract"))
            tiles = html.Div(className="tiles", children=[
                tile("Blocking checks", f"{len(blocking) - len(failed)}/{len(blocking)}",
                     "all passed: numbers can be trusted" if not failed else
                     f"{len(failed)} failed: treat numbers as unverified", "good" if not failed else "bad"),
                tile("Warnings", str(len(warns)), "review, still usable" if warns else "none", "warn" if warns else "good"),
                tile("Rows validated (Pydantic)", f"{n_rows:,}", f"{bad_rows:,} rows break the contract",
                     "good" if not bad_rows else "bad"),
                tile("Data through", f"{data['national']['date'].max():%b %d, %Y}",
                     f"{len(results)} checks · {len(DIMENSIONS)} dimensions", None),
            ])
            dims = []
            for dim in DIMENSIONS:
                rs = [r for r in results if r.dimension == dim]
                st = "fail" if any(r.status == "fail" for r in rs) else ("warn" if any(r.status == "warn" for r in rs)
                                                                         else "pass")
                dims.append(html.Div(className=f"dim {st}", children=[
                    html.Div([html.Span(ICON[st], className="dim-icon"), f" {dim}"], className="dim-name"),
                    html.Div(f"{len(rs)} check{'s' * (len(rs) != 1)} · {DIM_HELP[dim]}", className="t-note")]))
            order = {"fail": 0, "warn": 1, "pass": 2}
            rows = sorted(({"id": r.name, "icon": ICON[r.status], "status": r.status, "name": r.name,
                            "dimension": r.dimension, "severity": r.severity, "observed": r.observed,
                            "expected": r.expected, "rows_affected": r.rows_affected} for r in results),
                          key=lambda x: (order[x["status"]], x["severity"] != "block"))
            contract = contract_errors(errors)
            series_opts = ["National"] + sorted(data["sector"]["sector"].unique())
            return banner, tiles, dims, rows, contract, history(), series_opts

        @app.callback(Output("dq-evidence", "children"), Output("dq-evidence", "style"),
                      Input("dq-table", "active_cell"), Input("dq-fault", "value"), Input("page", "value"))
        def evidence(cell, fault, page):
            if page != "dq":
                return no_update, no_update
            _, results, _ = run(fault)
            by_name = {r.name: r for r in results}
            r = by_name.get(cell["row_id"]) if cell else None
            if r is None:                                   # default: first failing check, if any
                r = next((x for x in results if x.status == "fail"), None) or \
                    next((x for x in results if x.status == "warn"), None)
            if r is None:
                return [html.H2("Evidence"), html.P("All checks passed. Click any check to see what it looked at.",
                                                    className="sub")], {}
            ev = pd.DataFrame(r.evidence)
            body = (dash_table.DataTable(data=ev.to_dict("records"),
                                         columns=[{"name": c, "id": c} for c in ev.columns],
                                         export_format="csv", **{**table_style, "page_size": 10})
                    if len(ev) else html.P("No rows to show for this check.", className="sub"))
            return [html.Div(className="drill-head", children=[
                        html.Div([html.H2(f"Evidence: {ICON[r.status]} {r.name}"),
                                  html.P(f"{r.dimension} · {r.severity} · observed {r.observed} · expected "
                                         f"{r.expected}", className="sub")])]),
                    html.Div(r.detail, className="calc"),
                    html.P(f"Showing up to {q.EVIDENCE_ROWS} rows of {r.rows_affected:,} affected."
                           if r.rows_affected > q.EVIDENCE_ROWS else "", className="sub"),
                    body], {}

        @app.callback(Output("dq-control", "figure"),
                      Input("dq-series", "value"), Input("dq-var", "value"), Input("dq-fault", "value"),
                      Input("theme", "data"), Input("page", "value"))
        def control(series_name, var, fault, theme, page):
            if page != "dq":
                return no_update
            t = hd.THEMES[theme or "light"]
            data, _, _ = run(fault)
            series_name = series_name or "National"
            if series_name == "National":
                df = data["national"][data["national"]["variable"] == var]
            else:
                df = data["sector"][(data["sector"]["sector"] == series_name) & (data["sector"]["variable"] == var)]
            s = df.set_index("date")["idx"]
            latest = data["national"]["date"].max()
            r, sigma = q.daily_control(s, latest)
            r = r.loc[latest - pd.DateOffset(years=1):].dropna() * 100
            if r.empty:
                return go.Figure().update_layout(hd.base_layout(t, height=320))
            lim = q.Z * sigma * 100
            out = r[r.abs() > lim]
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=r.index, y=r.values, mode="lines+markers", name="Day-over-day change",
                                     line=dict(color=t["muted"], width=1), marker=dict(size=4, color=t["muted"]),
                                     hovertemplate="%{x|%b %d, %Y}<br>%{y:+.2f}%<extra></extra>"))
            fig.add_trace(go.Scatter(x=out.index, y=out.values, mode="markers", name="Outside ±3σ",
                                     marker=dict(size=10, color=t["down"], line=dict(width=2, color=t["surface"])),
                                     hovertemplate="%{x|%b %d, %Y}<br><b>%{y:+.2f}%</b> special cause<extra></extra>"))
            for y, label in [(lim, f"+3σ = {lim:+.2f}%"), (-lim, f"−3σ = {-lim:+.2f}%")]:
                fig.add_hline(y=y, line=dict(color=t["ink2"], width=1))
                fig.add_annotation(x=r.index[0], y=y, text=label, showarrow=False, xanchor="left",
                                   yanchor="bottom", font=dict(color=t["ink2"], size=11))
            fig.add_hline(y=0, line=dict(color=t["axis"], width=1))
            fig.update_layout(hd.base_layout(t, height=320, showlegend=True, hovermode="closest",
                                             legend=dict(orientation="h", y=1.0, yanchor="bottom", x=0,
                                                         font=dict(color=t["ink2"])),
                                             margin=dict(l=8, r=8, t=36, b=8)))
            fig.update_yaxes(ticksuffix="%")
            return fig

    def tile(label, value, note, tone):
        cls = {"good": "ok", "bad": "no", "warn": "wa"}.get(tone, "")
        return html.Div(className="tile", children=[
            html.Div(label, className="t-label"), html.Div(value, className=f"t-value {cls}"),
            html.Div(note, className="t-note")])

    def contract_errors(errors: dict) -> html.Div:
        frames = [e.assign(table=k) for k, e in errors.items() if len(e)]
        if not frames:
            return html.P("✓ 0 contract violations across both tables.", className="sub ok")
        err = pd.concat(frames)
        summary = (err.groupby(["table", "field", "error"]).size().rename("rows").reset_index()
                   .sort_values("rows", ascending=False))
        return html.Div([html.P("✗ Contract violations by field and error type:", className="sub no"),
                         dash_table.DataTable(data=summary.to_dict("records"),
                                              columns=[{"name": c, "id": c} for c in summary.columns],
                                              **{**table_style, "page_size": 8, "filter_action": "none"})])

    def history() -> html.Div:
        h = q.load_history()
        if h.empty:
            return html.P("No runs recorded yet.", className="sub")
        runs = (h.assign(block_fail=(h["severity"] == "block") & (h["passed"] == 0),
                         warn=(h["severity"] == "warn") & (h["passed"] == 0))
                .groupby(["run_at", "data_through"])
                .agg(checks=("name", "count"), blocking_failed=("block_fail", "sum"), warnings=("warn", "sum"))
                .reset_index().sort_values("run_at", ascending=False))
        return html.Div([
            html.P(f"{len(runs)} run(s) recorded in dq_history.db", className="sub"),
            dash_table.DataTable(data=runs.to_dict("records"),
                                 columns=[{"name": c.replace("_", " "), "id": c} for c in runs.columns],
                                 **{**table_style, "page_size": 6, "filter_action": "none"})])

    return layout, register
