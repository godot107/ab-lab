"""The extension as a host app sees it: no ab-lab templates, its own routes,
its own primary event. Stands in for hiring-demand-monitor's Dash server."""
from __future__ import annotations

import pytest
from flask import Flask

from ablab import Experiment, assign, store

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}


@pytest.fixture()
def host(tmp_path, monkeypatch):
    monkeypatch.setenv("AB_DB_PATH", str(tmp_path / "t.db"))
    exp = Experiment("exp_host", salt="s1", events=("drill_through",),
                     conversion="drill_through", cookie_secure=False)
    app = Flask(__name__)
    exp.init_app(app)

    @app.route("/_layout")  # where a Dash layout function would run
    def layout():
        return exp.expose() or "untracked"

    return exp, app.test_client()


def test_conversion_must_be_a_collected_event():
    with pytest.raises(ValueError):
        Experiment("x", salt="s", events=("a",), conversion="b")


def test_expose_outside_a_request_is_untracked():
    """Dash can call a layout function at startup; that must not log."""
    assert Experiment("x", salt="s").expose() is None


def test_host_route_gets_cookie_and_one_exposure(host):
    exp, c = host
    first = c.get("/_layout", headers=UA)
    assert "ab_vid" in first.headers.get("Set-Cookie", "")
    vid = c.get_cookie("ab_vid").value
    assert first.get_data(as_text=True) == assign(vid, "s1")
    second = c.get("/_layout", headers=UA)
    assert "ab_vid" not in second.headers.get("Set-Cookie", "")
    assert sum(v["exposed"] for v in store.counts("exp_host").values()) == 1


def test_host_events_and_conversion(host):
    exp, c = host
    c.get("/_layout", headers=UA)
    assert c.post("/api/events", json={"event": "detail_click"}, headers=UA).status_code == 400
    assert c.post("/api/events", json={"event": "drill_through"}, headers=UA).status_code == 204
    counts = c.get("/api/stats").get_json()["counts"]
    assert sum(v["converted"] for v in counts.values()) == 1


def test_bots_stay_out_of_host_experiment(host):
    _, c = host
    assert c.get("/_layout", headers={"User-Agent": "Googlebot"}).get_data(as_text=True) == "untracked"
    assert store.counts("exp_host") == {}


def test_track_needs_an_exposed_visitor_and_a_known_event(host):
    exp, c = host
    with c.application.test_request_context(headers=UA):
        assert exp.track("drill_through") is False            # no cookie: never exposed
        with pytest.raises(ValueError):
            exp.track("detail_click")
    c.get("/_layout", headers=UA)
    vid = c.get_cookie("ab_vid").value
    with c.application.test_request_context(headers={**UA, "Cookie": f"ab_vid={vid}"}):
        assert exp.track("drill_through", "rank") is True
    assert store.counts("exp_host", conversion="drill_through")[assign(vid, "s1")]["converted"] == 1
