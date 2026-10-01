"""The monitor as an experiment host (ablab). Each test guards something that would
still produce a plausible-looking result if it broke: a visitor counted twice, an arm
the browser chose, a conversion the server never saw, content refreshed mid-test."""
import importlib
import json
import sqlite3
import sys
from types import SimpleNamespace

import plotly
import pytest

import build_hiringlab as build
import dq_checks as q
import hiringlab_dashboard as hd
from ablab import assign, store

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}
LAYOUT = "/_dash-layout"


def load_app(monkeypatch, sqlite_db, tmp_path, **env):
    monkeypatch.setattr(hd, "DB", sqlite_db)
    monkeypatch.setattr(q, "HISTORY_DB", tmp_path / "dq_history.db")
    monkeypatch.setenv("AB_DB_PATH", str(tmp_path / "events.db"))
    for k in ("AB_EXPERIMENT", "AB_SALT"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    sys.modules.pop("hiringlab_app", None)
    return importlib.import_module("hiringlab_app")


@pytest.fixture()
def live(monkeypatch, sqlite_db, tmp_path):
    return load_app(monkeypatch, sqlite_db, tmp_path, AB_EXPERIMENT="exp_t", AB_SALT="s1",
                    COOKIE_SECURE="0")


def render_as(app, vid):
    """Run the render callback as visitor `vid` would trigger it."""
    with app.server.test_request_context(headers={**UA, "Cookie": f"ab_vid={vid}"}):
        return app.render("total postings", 3, app.DEFAULT_WINDOW, 10, [], ["shared"], "light", None)


def test_off_by_default_logs_nothing(monkeypatch, sqlite_db, tmp_path):
    app = load_app(monkeypatch, sqlite_db, tmp_path)
    c = app.server.test_client()
    r = c.get(LAYOUT, headers=UA)
    assert r.status_code == 200 and "ab_vid" not in r.headers.get("Set-Cookie", "")
    # No collector mounted: Dash answers unknown paths with its index page, not stats.
    assert c.get("/api/stats").get_json(silent=True) is None


def test_layout_fetch_is_the_one_exposure(live):
    c = live.server.test_client()
    first = c.get(LAYOUT, headers=UA)
    assert "ab_vid" in first.headers.get("Set-Cookie", "")
    for _ in range(3):
        c.get(LAYOUT, headers=UA)
    c.get("/", headers=UA)   # the index page alone is not an exposure
    assert sum(v["exposed"] for v in store.counts("exp_t").values()) == 1


def test_bots_see_control_and_are_not_counted(live):
    c = live.server.test_client()
    c.get(LAYOUT, headers={"User-Agent": "Googlebot/2.1"})
    assert store.counts("exp_t") == {}


def test_arm_changes_only_the_color_encoding(live):
    a_vid = next(f"v{i}" for i in range(100) if assign(f"v{i}", "s1") == "A")
    b_vid = next(f"v{i}" for i in range(100) if assign(f"v{i}", "s1") == "B")
    a, b = render_as(live, a_vid), render_as(live, b_vid)
    assert a[-1].startswith("Change by sector · grey") and "blue = growing" in b[-1]
    # Serialized as the browser receives them, only the ranking figure and its key differ.
    enc = lambda x: json.dumps(x, cls=plotly.utils.PlotlyJSONEncoder, sort_keys=True)
    assert [i for i, (x, y) in enumerate(zip(a, b)) if enc(x) != enc(y)] == [4, 11]


def test_within_variation_sector_is_grey_in_a_only(live):
    m = dict(live.compute("total postings", 52))
    m["sec"] = m["sec"].copy()
    quiet = m["sec"].index[0]
    m["sec"].loc[quiet, "beyond"] = False
    t = hd.THEMES["light"]
    bars = lambda fig: {y: tr.marker.color for tr in fig.data if tr.type == "bar" for y in tr.y}
    a = bars(live.fig_ranking(m, t, 10, None))
    b = bars(live.fig_ranking(m, t, 10, None, color_all=True))
    assert a[quiet] == t["muted"] and b[quiet] in (t["up"], t["down"])
    assert {k: v for k, v in a.items() if k != quiet} == {k: v for k, v in b.items() if k != quiet}


def test_drill_through_is_logged_server_side(live, monkeypatch):
    c = live.server.test_client()
    c.get(LAYOUT, headers=UA)
    vid = c.get_cookie("ab_vid").value
    monkeypatch.setattr(live, "ctx", SimpleNamespace(triggered_id="rank"))
    with live.server.test_request_context(headers={**UA, "Cookie": f"ab_vid={vid}"}):
        out = live.on_click({"points": [{"y": "Nursing"}]}, None, None, [], None, None, [])
    assert out[0] == {"kind": "sector", "sector": "Nursing"}
    counts = store.counts("exp_t", conversion="drill_through")
    assert counts[assign(vid, "s1")]["converted"] == 1


def test_browser_cannot_post_a_conversion_for_another_event(live):
    c = live.server.test_client()
    c.get(LAYOUT, headers=UA)
    assert c.post("/api/events", json={"event": "detail_click"}, headers=UA).status_code == 400


def test_refresh_is_frozen_while_experiment_is_live(monkeypatch, tmp_path):
    db = tmp_path / "hiringlab.db"
    sqlite3.connect(db).close()
    monkeypatch.setattr(build, "DB", db)
    monkeypatch.setattr(build, "fetch", lambda: pytest.fail("refreshed during a live experiment"))
    monkeypatch.setenv("AB_EXPERIMENT", "exp_t")
    monkeypatch.setattr(sys, "argv", ["build_hiringlab.py", "--refresh"])
    build.main()
