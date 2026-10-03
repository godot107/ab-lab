"""The monitor as an experiment host (ablab). Each test guards something that would
still produce a plausible-looking result if it broke: a visitor counted twice, an arm
the browser chose, a conversion the server never saw, content refreshed mid-test."""
import importlib
import json
import re
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
    for k in ("AB_EXPERIMENT", "AB_SALT", "SHOW_EARNINGS"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    sys.modules.pop("hiringlab_app", None)
    return importlib.import_module("hiringlab_app")


@pytest.fixture()
def live(monkeypatch, sqlite_db, tmp_path):
    return load_app(monkeypatch, sqlite_db, tmp_path, AB_EXPERIMENT="exp_t", AB_SALT="s1",
                    COOKIE_SECURE="0")


def ids_in_order(page_json: str) -> list[str]:
    return re.findall(r'"id":\s*"(tiles|trend|rank)"', page_json)


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


def test_arms_differ_only_in_section_order(live):
    enc = lambda c: json.dumps(c, cls=plotly.utils.PlotlyJSONEncoder, sort_keys=True)
    a = live.LAYOUTS["A"]["page-dashboard"].children
    b = live.LAYOUTS["B"]["page-dashboard"].children
    assert sorted(map(enc, a)) == sorted(map(enc, b)), "same components, nothing added or lost"
    assert ids_in_order(enc(live.LAYOUTS["A"])) == ["tiles", "trend", "rank"]
    assert ids_in_order(enc(live.LAYOUTS["B"])) == ["trend", "rank", "tiles"]


def test_each_visitor_gets_their_arms_layout(live):
    for i in range(6):
        c = live.server.test_client()
        page = c.get(LAYOUT, headers=UA).get_data(as_text=True)
        arm = assign(c.get_cookie("ab_vid").value, "s1")
        assert ids_in_order(page)[0] == ("tiles" if arm == "A" else "trend")


def test_drill_through_is_logged_server_side(live, monkeypatch):
    c = live.server.test_client()
    c.get(LAYOUT, headers=UA)
    vid = c.get_cookie("ab_vid").value
    monkeypatch.setattr(live, "ctx", SimpleNamespace(triggered_id="rank"))
    with live.server.test_request_context(headers={**UA, "Cookie": f"ab_vid={vid}"}):
        out = live.on_click({"points": [{"y": "Nursing"}]}, None, [], None, None, [])
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


def test_frozen_data_reads_as_frozen_not_stale(data, monkeypatch):
    """A blocking freshness failure would turn the header badge red mid-experiment."""
    import pandas as pd
    old = data["national"]["date"].max() + pd.Timedelta(days=40)
    monkeypatch.delenv("AB_EXPERIMENT", raising=False)
    assert not q.check_freshness(data, old).passed
    monkeypatch.setenv("AB_EXPERIMENT", "exp_t")
    r = q.check_freshness(data, old)
    assert r.passed and "frozen for experiment exp_t" in r.observed and "40 days old" in r.observed


def test_cookie_notice_only_while_experimenting(monkeypatch, sqlite_db, tmp_path, live):
    assert "ab-notice" in json.dumps(live.LAYOUTS["B"], cls=plotly.utils.PlotlyJSONEncoder)
    off = load_app(monkeypatch, sqlite_db, tmp_path)
    assert "ab-notice" not in json.dumps(off.LAYOUTS["A"], cls=plotly.utils.PlotlyJSONEncoder)


def test_page_time_script_is_served_and_bounded(live):
    c = live.server.test_client()
    js = c.get("/assets/page_time.js")
    assert js.status_code == 200 and b"sendBeacon" in js.data
    c.get(LAYOUT, headers=UA)
    post = lambda t: c.post("/api/events", json={"event": "page_time", "target": t},
                            headers=UA).status_code
    assert post("37") == 204 and post("99999") == 400


def test_earnings_material_is_off_by_default(monkeypatch, sqlite_db, tmp_path):
    """Nothing about Recruit's earnings in the page, the About tab or the callbacks."""
    app = load_app(monkeypatch, sqlite_db, tmp_path)
    page = json.dumps(app.LAYOUTS["A"], cls=plotly.utils.PlotlyJSONEncoder)
    for word in ("earnings", "Earnings", "ARPJ", "Recruit's"):
        assert word not in page, word
    assert app.CLICK_SOURCES == ["rank", "trend"] and not hasattr(app, "render_earnings")


def test_earnings_panel_returns_with_the_flag(monkeypatch, sqlite_db, tmp_path):
    app = load_app(monkeypatch, sqlite_db, tmp_path, SHOW_EARNINGS="1")
    page = json.dumps(app.LAYOUTS["A"], cls=plotly.utils.PlotlyJSONEncoder)
    assert "earnings-card" in page and "Why this index connects to Indeed" in page
    assert app.CLICK_SOURCES[-1] == "earnings"
    style, fig, _ = app.render_earnings("total postings", "light")
    assert style == {} and len(fig.data) > 0
