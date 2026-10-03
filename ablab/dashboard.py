"""The live experiment page (/experiment): aggregates only, blinded until the
stopping rule.

Public on purpose: the data is anonymous (random visitor ids, no IP, no user
agent) and the page shows totals, never a row or an id. What it withholds is
the A-vs-B outcome. Until the smaller arm reaches its target or the time limit
passes, usage is pooled across arms; after that, per-arm rates appear, and
still no p-value: the decision is analysis/report.py's, run once.

Plain HTML and CSS bars, no chart library and no third-party script. Every
label that comes from the database is escaped: drill sources are reported by
the browser.
"""
from __future__ import annotations

import html
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ablab import store

QUICK_EXIT_SECS = 10
SOURCES = {"rank": "Sector ranking", "trend": "Trend chart", "mult": "Small multiples",
           "sector-table": "Sector table"}
TIME_BUCKETS = [(0, 10, "under 10 s"), (10, 30, "10–30 s"), (30, 60, "30–60 s"),
                (60, 180, "1–3 min"), (180, 600, "3–10 min"), (600, None, "10 min +")]


@dataclass
class Visitor:
    variant: str
    device: str
    exposed: datetime
    pageviews: int = 0
    first_source: str | None = None
    drills: int = 0
    visible: int | None = None


@dataclass
class Summary:
    experiment: str
    visitors: dict[str, Visitor] = field(default_factory=dict)
    per_day: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    first: datetime | None = None


def load(experiment: str, conversion: str, path=None) -> Summary:
    s = Summary(experiment)
    with store.connect(path) as conn:
        rows = conn.execute("SELECT ts, visitor_id, variant, event, target, device FROM events"
                            " WHERE experiment = ? ORDER BY id", (experiment,)).fetchall()
    for ts, vid, variant, event, target, device in rows:
        t = datetime.fromisoformat(ts)
        if event == "exposure":
            s.visitors[vid] = Visitor(variant, device or "unknown", t)
            s.per_day[ts[:10]][variant] += 1
            s.first = s.first or t
        elif vid not in s.visitors:
            continue
        elif event == "pageview":
            s.visitors[vid].pageviews += 1
        elif event == conversion:
            v = s.visitors[vid]
            v.drills += 1
            v.first_source = v.first_source or (target or "unknown")
        elif event == "page_time" and (target or "").isdigit():
            v = s.visitors[vid]
            v.visible = (v.visible or 0) + int(target)
    return s


def srm_p(a: int, b: int, split: int) -> float:
    """Chi-square goodness of fit, 1 df, without scipy: p = erfc(sqrt(chi2 / 2))."""
    n = a + b
    if n == 0:
        return 1.0
    ea, eb = n * split / 100, n * (100 - split) / 100
    chi2 = (a - ea) ** 2 / ea + (b - eb) ** 2 / eb
    return math.erfc(math.sqrt(chi2 / 2))


def usage(vs: list[Visitor]) -> dict:
    timed = [v for v in vs if v.visible is not None]
    return {
        "n": len(vs),
        "drill_rate": sum(v.drills > 0 for v in vs) / len(vs) if vs else None,
        "median_visible": statistics.median(v.visible for v in timed) if timed else None,
        "timed_share": len(timed) / len(vs) if vs else None,
        "quick_exit": (sum(v.visible < QUICK_EXIT_SECS and not v.drills for v in timed) / len(timed)
                       if timed else None),
    }


# --- formatting --------------------------------------------------------------------

def esc(x) -> str:
    return html.escape(str(x))


def pct(x: float | None) -> str:
    return "–" if x is None else f"{x:.0%}"


def small_pct(x: float) -> str:
    """A share that isn't zero never reads as 0%."""
    return "<1%" if 0 < x < 0.005 else f"{x:.0%}"


def secs(x: float | None) -> str:
    if x is None:
        return "–"
    return f"{x:.0f} s" if x < 90 else f"{x / 60:.1f} min"


def tile(label: str, value: str, note: str = "") -> str:
    return (f'<div class="tile"><div class="t-label">{esc(label)}</div>'
            f'<div class="t-value">{esc(value)}</div><div class="t-note">{esc(note)}</div></div>')


def meter(label: str, done: float, total: float, unit: str) -> str:
    share = min(done / total, 1) if total else 0
    return (f'<div class="meter"><div class="m-head"><span>{esc(label)}</span>'
            f'<span class="num">{done:,.0f} of {total:,.0f} {esc(unit)}</span></div>'
            f'<div class="m-track" role="img" aria-label="{esc(label)}: {share:.0%}">'
            f'<div class="m-fill" style="width:{share * 100:.1f}%"></div></div></div>')


def hbars(rows: list[tuple[str, int]], total: int, unit: str) -> str:
    """Horizontal bars, one series. Value at the tip; tooltip and table carry the rest."""
    if not rows:
        return '<p class="empty">Nothing yet.</p>'
    top = max(n for _, n in rows)
    out = []
    for label, n in rows:
        share = n / total if total else 0
        tip = f"{label}: {n:,} {unit} ({share:.0%})"
        out.append(f'<div class="hb-row" tabindex="0" title="{esc(tip)}" aria-label="{esc(tip)}">'
                   f'<span class="hb-label">{esc(label)}</span>'
                   f'<span class="hb-track"><span class="hb-bar{"" if n else " zero"}" style="width:{n / top * 100:.1f}%"></span>'
                   f'<span class="hb-val num">{small_pct(share)}</span></span></div>')
    return '<div class="hbars">' + "".join(out) + "</div>"


def table(head: list[str], rows: list[list]) -> str:
    th = "".join(f"<th>{esc(h)}</th>" for h in head)
    tr = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>" for r in rows)
    return (f'<details class="tbl"><summary>Show as table</summary>'
            f'<table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></details>')


def daily_columns(per_day: dict[str, Counter]) -> str:
    """Visitors per day, stacked by version: allocation, not outcome."""
    days = sorted(per_day)
    if not days:
        return '<p class="empty">No visitors yet.</p>'
    top = max(sum(per_day[d].values()) for d in days)
    cols = []
    for d in days:
        a, b = per_day[d]["A"], per_day[d]["B"]
        tip = f"{d}: {a + b:,} visitors (A {a:,}, B {b:,})"
        seg = lambda n, cls: (f'<span class="seg {cls}" style="height:{n / top * 100:.2f}%"></span>'
                              if n else "")
        cols.append(f'<div class="col" tabindex="0" title="{esc(tip)}" aria-label="{esc(tip)}">'
                    f'{seg(b, "s-b")}{seg(a, "s-a")}</div>')
    ticks = (f'<div class="x-ticks"><span>{esc(days[0][5:])}</span>'
             + (f'<span>{esc(days[-1][5:])}</span>' if len(days) > 1 else "") + "</div>")
    return (f'<div class="cols-wrap"><div class="y-max num">{top:,}</div>'
            f'<div class="cols">{"".join(cols)}</div></div>{ticks}')


# --- the page ----------------------------------------------------------------------

def preview(base_url: str) -> str:
    """Link-preview tags; the image is the dashboard's, served by the host app."""
    desc = ("Live, anonymous results of an A/B test on a job-market dashboard. "
            "It won't name a winner until the test is done.")
    tags = {"og:type": "website", "og:title": "Charts first or tiles first? Live A/B test",
            "og:description": desc, "twitter:card": "summary_large_image"}
    if base_url:
        tags.update({"og:image": f"{base_url}/assets/og.png", "og:image:width": "1200",
                     "og:image:height": "627"})
    return ("".join(f'<meta property="{k}" content="{esc(v)}">' for k, v in tags.items())
            + f'<meta name="description" content="{esc(desc)}">')


def render(experiment: str, conversion: str, split: int = 50, target_per_arm: int = 250,
           max_days: int = 28, path=None, base_url: str = "") -> str:
    s = load(experiment, conversion, path)
    vs = list(s.visitors.values())
    arms = {k: [v for v in vs if v.variant == k] for k in "AB"}
    n_a, n_b = len(arms["A"]), len(arms["B"])
    now = datetime.now(timezone.utc)
    days = (now - s.first).total_seconds() / 86400 if s.first else 0.0
    rule_met = min(n_a, n_b) >= target_per_arm or days >= max_days
    pooled = usage(vs)
    p = srm_p(n_a, n_b, split)
    synthetic = sum(v.device == "synthetic" for v in vs)

    split_note = (f"A {n_a:,} · B {n_b:,} · split check passes" if p >= 0.001 else
                  f"A {n_a:,} · B {n_b:,} · SPLIT CHECK FAILS: the pipeline is broken; read nothing")
    tiles = "".join([
        tile("Visitors", f"{len(vs):,}", split_note),
        tile("Drilled into the data", pct(pooled["drill_rate"]), "both versions combined"),
        tile("Time on page", secs(pooled["median_visible"]),
             f"median visible · measured for {pct(pooled['timed_share'])}"),
        tile("Quick exits", pct(pooled["quick_exit"]), f"under {QUICK_EXIT_SECS} s, no drill-through"),
    ])

    firsts = Counter(SOURCES.get(v.first_source, v.first_source) for v in vs if v.first_source)
    drilled = sum(firsts.values())
    timed = [v.visible for v in vs if v.visible is not None]
    buckets = [(label, sum(lo <= t < (hi if hi is not None else 10 ** 9) for t in timed))
               for lo, hi, label in TIME_BUCKETS]
    devices = Counter(v.device for v in vs)

    if rule_met:
        ua, ub = usage(arms["A"]), usage(arms["B"])
        outcome = (
            '<section class="card"><h2>Result by version</h2>'
            '<p class="sub">The stopping rule is met, so the comparison is unsealed. Descriptive '
            'only: the decision, with its confidence interval and the split check, comes from '
            '<code>analysis/report.py</code>, run once.</p>'
            + "".join(f'<p class="arm"><span class="key k-{k.lower()}"></span> <b>Version {k}</b> '
                      f'{esc(name)} · {u["n"]:,} visitors · drilled in {pct(u["drill_rate"])} · '
                      f'time on page {secs(u["median_visible"])} · quick exits {pct(u["quick_exit"])}</p>'
                      for k, name, u in [("A", "(tiles first)", ua), ("B", "(charts first)", ub)])
            + "</section>")
    else:
        outcome = (
            '<section class="card sealed"><h2>Which version is ahead?</h2>'
            '<p>Not shown yet, on purpose. Early results swing widely, and checking a scoreboard '
            'until one side leads turns a 5% chance of a false win into roughly 28%. The '
            f'comparison unseals when both versions reach {target_per_arm:,} visitors or on day '
            f'{max_days}, and is read once. Everything else on this page combines both versions.'
            '</p></section>')

    synth = (f'<p class="note">{synthetic:,} of {len(vs):,} visitors are synthetic demo traffic '
             '(labelled as such when sent), not real people.</p>' if synthetic else "")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>Experiment status</title>
{preview(base_url)}
<style>{CSS}</style></head>
<body><main>
<header>
  <p class="kicker">Experiment {esc(experiment)} · live status</p>
  <h1>Charts first or tiles first?</h1>
  <p class="sub">Does leading the Hiring Demand Monitor with its charts, instead of its KPI tiles, get
  more visitors to drill into the data? Anonymous totals only: no visitor ids, IPs or user agents are
  shown or stored. Refreshes every minute · updated {now:%b %d, %Y %H:%M} UTC.</p>
</header>
{synth}
<section class="tiles">{tiles}</section>
<section class="card"><h2>Progress to the stopping rule</h2>
  {meter("Smaller version", min(n_a, n_b), target_per_arm, "visitors")}
  {meter("Days running", days, max_days, "days")}
</section>
{outcome}
<section class="card"><h2>Visitors per day</h2>
  <p class="sub">Stacked by version: how traffic is split, not how it behaves.</p>
  <div class="legend"><span><i class="key k-a"></i>Version A</span><span><i class="key k-b"></i>Version B</span></div>
  {daily_columns(s.per_day)}
  {table(["Day", "A", "B"], [[d, s.per_day[d]["A"], s.per_day[d]["B"]] for d in sorted(s.per_day)])}
</section>
<div class="grid">
<section class="card"><h2>Where first drill-throughs start</h2>
  <p class="sub">Share of visitors who drilled in · {drilled:,} so far</p>
  {hbars(firsts.most_common(), drilled, "visitors")}
  {table(["Source", "Visitors"], [[k, n] for k, n in firsts.most_common()])}
</section>
<section class="card"><h2>Time on page</h2>
  <p class="sub">Seconds the page was visible, per visitor · {len(timed):,} measured</p>
  {hbars(buckets if timed else [], len(timed), "visitors")}
  {table(["Visible time", "Visitors"], [[k, n] for k, n in buckets])}
</section>
<section class="card"><h2>Devices</h2>
  <p class="sub">Coarse class only; the user agent is never stored</p>
  {hbars(devices.most_common(), len(vs), "visitors")}
  {table(["Device", "Visitors"], [[k, n] for k, n in devices.most_common()])}
</section>
</div>
<footer>Pre-registered design, method and code:
<a href="https://github.com/godot107/ab-lab">github.com/godot107/ab-lab</a>.
Data: Indeed Hiring Lab Job Postings Index, CC BY 4.0. Not affiliated with Indeed.</footer>
</main></body></html>"""


CSS = """
:root{color-scheme:light;--plane:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--grid:#e1e0d9;--ring:rgba(11,11,11,.10);--s1:#2a78d6;--s2:#eb6834;--track:#cde2fb;--bad:#c42b2b}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--plane:#121211;
--surface:#1a1a19;--ink:#ffffff;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--ring:rgba(255,255,255,.10);
--s1:#3987e5;--s2:#d95926;--track:#184f95;--bad:#e66767}}
:root[data-theme="dark"]{color-scheme:dark;--plane:#121211;--surface:#1a1a19;--ink:#ffffff;--ink2:#c3c2b7;
--muted:#898781;--grid:#2c2c2a;--ring:rgba(255,255,255,.10);--s1:#3987e5;--s2:#d95926;--track:#184f95;--bad:#e66767}
*{box-sizing:border-box}
body{margin:0;background:var(--plane);color:var(--ink);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1080px;margin:0 auto;padding:24px 16px 40px}
.kicker{color:var(--muted);font-size:12px;letter-spacing:.04em;text-transform:uppercase;margin:0}
h1{font-size:24px;margin:4px 0 6px} h2{font-size:15px;margin:0 0 2px}
.sub{color:var(--ink2);margin:0 0 12px;font-size:13px} .note{color:var(--ink2);font-size:13px}
.num{font-variant-numeric:tabular-nums}
.card,.tile{background:var(--surface);border:1px solid var(--ring);border-radius:12px;padding:16px}
.card{margin:12px 0}
.tiles{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:16px 0 0}
.t-label{color:var(--ink2);font-size:13px} .t-value{font-size:32px;font-weight:600;line-height:1.2;margin:2px 0}
.t-note{color:var(--muted);font-size:12px}
.meter{margin:10px 0} .m-head{display:flex;justify-content:space-between;color:var(--ink2);font-size:13px}
.m-track{height:10px;border-radius:5px;background:var(--track);margin-top:4px;overflow:hidden}
.m-fill{height:100%;background:var(--s1);border-radius:5px}
.sealed p{margin:0;color:var(--ink2)}
.arm{margin:6px 0;color:var(--ink2)} .arm b{color:var(--ink);white-space:nowrap}
.legend{display:flex;gap:16px;color:var(--ink2);font-size:13px;margin:4px 0 8px}
.legend span{display:flex;align-items:center;gap:6px}
.key{display:inline-block;width:12px;height:12px;border-radius:3px} .k-a{background:var(--s1)} .k-b{background:var(--s2)}
.cols-wrap{position:relative;padding-left:36px}
.y-max{position:absolute;left:0;top:-4px;color:var(--muted);font-size:11px}
.cols{display:flex;align-items:flex-end;gap:2px;height:160px;border-bottom:1px solid var(--grid);
border-top:1px solid var(--grid)}
.col{flex:1 1 0;max-width:24px;height:100%;display:flex;flex-direction:column;justify-content:flex-end;gap:2px;outline:none}
.seg{display:block;width:100%;min-height:2px} .seg:first-child{border-radius:4px 4px 0 0}
.s-a{background:var(--s1)} .s-b{background:var(--s2)}
.col:hover .seg,.col:focus .seg{filter:brightness(1.15)}
.x-ticks{display:flex;justify-content:space-between;padding-left:36px;color:var(--muted);font-size:11px;margin-top:4px}
.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
.grid .card{margin:0}
.hbars{display:flex;flex-direction:column;gap:8px}
.hb-row{display:grid;grid-template-columns:minmax(90px,40%) 1fr;align-items:center;gap:8px;outline:none}
.hb-label{color:var(--ink2);font-size:13px;overflow-wrap:anywhere}
.hb-track{display:flex;align-items:center;gap:6px}
.hb-bar{display:block;height:16px;max-height:24px;background:var(--s1);border-radius:0 4px 4px 0;min-width:2px}
.hb-bar.zero{min-width:0}
.hb-row:hover .hb-bar,.hb-row:focus .hb-bar{filter:brightness(1.15)}
.hb-val{color:var(--ink);font-size:12px}
.empty{color:var(--muted);margin:4px 0}
details.tbl{margin-top:10px;font-size:13px} details.tbl summary{color:var(--ink2);cursor:pointer}
details.tbl table{border-collapse:collapse;margin-top:6px;font-variant-numeric:tabular-nums}
details.tbl th,details.tbl td{text-align:left;padding:3px 12px 3px 0;border-bottom:1px solid var(--grid)}
code{font-size:12px}
footer{color:var(--muted);font-size:12px;margin-top:20px} footer a{color:var(--s1)}
@media (max-width:820px){.tiles{grid-template-columns:repeat(2,minmax(0,1fr))}.grid{grid-template-columns:1fr}}
"""
