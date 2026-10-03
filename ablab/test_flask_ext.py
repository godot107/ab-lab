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


def test_numeric_events_are_bounded(tmp_path, monkeypatch):
    """A browser-reported number (seconds on page) can't be a string or an outlier."""
    monkeypatch.setenv("AB_DB_PATH", str(tmp_path / "t.db"))
    exp = Experiment("exp_num", salt="s1", events=("drill_through", "page_time"),
                     conversion="drill_through", cookie_secure=False, numeric={"page_time": 3600})
    app = Flask(__name__)
    exp.init_app(app)
    app.add_url_rule("/_layout", "layout", lambda: exp.expose() or "untracked")
    c = app.test_client()
    c.get("/_layout", headers=UA)
    post = lambda t: c.post("/api/events", json={"event": "page_time", "target": t}, headers=UA).status_code
    assert [post("42"), post("0"), post("3600")] == [204, 204, 204]
    assert [post("-5"), post("3601"), post("1e9"), post("abc"), post(None)] == [400] * 5


def _seed(n_per_arm, drills=0, target="rank"):
    ts = "2026-10-01T12:00:00+00:00"
    for arm in "AB":
        for i in range(n_per_arm):
            vid = f"{arm}{i}"
            store.record(ts, vid, "exp_host", arm, "exposure", device="desktop")
            store.record(ts, vid, "exp_host", arm, "pageview", device="desktop")
            if i < drills:
                store.record(ts, vid, "exp_host", arm, "drill_through", target)


def test_dashboard_is_blinded_until_the_stopping_rule(host):
    exp, c = host
    _seed(5, drills=2)
    body = c.get("/experiment").get_data(as_text=True)
    assert "Not shown yet, on purpose" in body and "Result by version" not in body
    assert "40%" in body                      # pooled drill rate: 4 of 10
    exp.target_per_arm = 5                    # now the rule is met
    body = c.get("/experiment").get_data(as_text=True)
    assert "Result by version" in body and "p-value" not in body.lower()


def test_dashboard_escapes_browser_reported_labels_and_logs_nothing(host):
    _, c = host
    _seed(1, drills=1, target='<script>alert(1)</script>')
    r = c.get("/experiment", headers=UA)
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    assert "<script>" not in r.get_data(as_text=True)
    assert "ab_vid" not in r.headers.get("Set-Cookie", "")
    assert sum(v["exposed"] for v in store.counts("exp_host").values()) == 2   # only the seeded ones
