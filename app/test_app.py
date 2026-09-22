"""Tests for the parts of the experiment that can silently corrupt a result.

These are not coverage theatre. Each one guards a failure mode that would still
produce a plausible-looking p-value: a non-uniform randomizer, a visitor who
switches arms, a double-counted exposure, a client that picks its own variant.
"""
from __future__ import annotations

import importlib
import sys

import pytest

from assignment import assign, bucket


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AB_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("COOKIE_SECURE", "0")
    for mod in ("store", "chat", "app"):
        sys.modules.pop(mod, None)
    app_mod = importlib.import_module("app")
    app_mod.app.config["TESTING"] = True
    with app_mod.app.test_client() as c:
        yield c


# --- assignment ----------------------------------------------------------

def test_assignment_is_stable():
    """The same visitor must get the same arm forever. A visitor who sees both
    layouts is a confound that no amount of sample size fixes."""
    assert all(assign("v-123", "s") == assign("v-123", "s") for _ in range(50))


def test_salt_decorrelates_experiments():
    """Without a per-experiment salt, experiment 002 reuses 001's split."""
    a = [assign(f"v{i}", "exp001") for i in range(400)]
    b = [assign(f"v{i}", "exp002") for i in range(400)]
    agree = sum(x == y for x, y in zip(a, b)) / len(a)
    assert 0.35 < agree < 0.65, "salts should decorrelate, not mirror or invert"


def test_split_is_roughly_even():
    """Randomizer smoke test. A biased hash shows up here before it shows up
    as an SRM in production."""
    arms = [assign(f"visitor-{i}", "exp001") for i in range(4000)]
    share_a = arms.count("A") / len(arms)
    assert 0.47 < share_a < 0.53, f"A share {share_a:.3f} — check the hash"


def test_split_ratio_is_honored():
    arms = [assign(f"visitor-{i}", "exp001", split=90) for i in range(4000)]
    assert 0.87 < arms.count("A") / len(arms) < 0.93


def test_buckets_span_the_range():
    seen = {bucket(f"v{i}", "s") for i in range(3000)}
    assert len(seen) == 100, "hash should reach every bucket"


# --- exposure logging ----------------------------------------------------

def test_visit_sets_cookie_and_logs_one_exposure(client):
    import store
    r = client.get("/")
    assert r.status_code == 200
    assert "ab_vid" in r.headers.get("Set-Cookie", "")
    counts = store.counts("exp001_layout")
    assert sum(v["exposed"] for v in counts.values()) == 1


def test_repeat_visits_do_not_double_count(client):
    """A refresh must not inflate the denominator. The unique index enforces
    this in the schema, because application-level dedup drifts."""
    import store
    for _ in range(6):
        client.get("/")
    counts = store.counts("exp001_layout")
    assert sum(v["exposed"] for v in counts.values()) == 1


def test_returning_visitor_keeps_the_same_layout(client):
    first = client.get("/").get_data(as_text=True)
    for _ in range(4):
        assert client.get("/").get_data(as_text=True) == first



def test_arms_differ_only_in_block_order(client):
    """Same numbers, same detail view, same markup -- only the order of the
    tiles and chart blocks may differ. Any other difference is a confound."""
    import re
    import app as app_mod
    from flask import render_template

    def blocks(variant):
        with app_mod.app.test_request_context():
            html = render_template("dashboard.html", variant=variant, tracking=True,
                                   experiment="x", snap=app_mod.SNAP)
        html = " ".join(html.split())  # template tags leave arm-specific whitespace
        head, _, rest = html.partition('<div class="block ')
        body, _, tail = rest.rpartition('<section id="detail"')
        parts = re.split(r'(?=<div class="block )', '<div class="block ' + body)
        return head, [p.strip() for p in parts if p.strip()], tail

    head_a, a, tail_a = blocks("A")
    head_b, b, tail_b = blocks("B")
    assert head_a == head_b and tail_a == tail_b
    assert len(a) == 2 and a == b[::-1]
    assert a[0].startswith('<div class="block tiles"')

# --- the collector -------------------------------------------------------

def test_click_is_attributed_to_the_server_side_variant(client):
    """The client sends an event name, never an arm. If the body could set the
    variant, anyone could skew the result by hand."""
    import store
    from assignment import assign as _assign
    client.get("/")
    vid = next(c.value for c in client._cookies.values()) if hasattr(client, "_cookies") \
        else client.get_cookie("ab_vid").value
    r = client.post("/api/events", json={"event": "detail_click", "target": "trend",
                                         "variant": "B"})  # attacker-supplied arm
    assert r.status_code == 204
    counts = store.counts("exp001_layout")
    truth = _assign(vid, "exp001", 50)
    assert counts[truth]["converted"] == 1
    assert sum(v["converted"] for v in counts.values()) == 1


def test_event_without_exposure_is_dropped(client):
    import store
    r = client.post("/api/events", json={"event": "detail_click"})
    assert r.status_code == 204
    assert store.counts("exp001_layout") == {}


def test_unknown_event_rejected(client):
    client.get("/")
    assert client.post("/api/events", json={"event": "rm -rf"}).status_code == 400


# --- opt-out and bots ----------------------------------------------------

@pytest.mark.parametrize("header", [{"DNT": "1"}, {"Sec-GPC": "1"}])
def test_opt_out_logs_nothing_and_sets_no_cookie(client, header):
    import store
    r = client.get("/", headers=header)
    assert r.status_code == 200
    assert "ab_vid" not in r.headers.get("Set-Cookie", "")
    assert store.counts("exp001_layout") == {}


def test_bots_are_excluded(client):
    import store
    client.get("/", headers={"User-Agent": "Googlebot/2.1"})
    assert store.counts("exp001_layout") == {}


def test_stats_endpoint_exposes_no_p_value(client):
    """Peeking must not be one click away -- the pre-registration commits to a
    single analysis at a fixed horizon."""
    client.get("/")
    body = client.get("/api/stats").get_data(as_text=True).lower()
    assert "p_value" not in body and "significant" not in body


def test_healthz(client):
    assert client.get("/healthz").status_code == 200


# --- chat box --------------------------------------------------------------

@pytest.fixture()
def chat_client(client, monkeypatch):
    """Chat on, with the Anthropic call stubbed: tests never spend tokens."""
    import chat
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(chat, "ask", lambda q, snap: f"answer to: {q}")
    return client


def test_chat_is_off_without_a_key(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert 'id="ask"' not in client.get("/").get_data(as_text=True)
    assert client.post("/api/chat", json={"question": "hi"}).status_code == 503


def test_chat_answers_and_logs_no_events(chat_client):
    """Chat is outside the experiment: asking must not touch the events table."""
    import store
    chat_client.get("/")
    before = store.counts("exp001_layout")
    r = chat_client.post("/api/chat", json={"question": "which sector is highest?"})
    assert r.status_code == 200 and r.get_json()["answer"].startswith("answer to:")
    assert store.counts("exp001_layout") == before


def test_chat_rejects_long_questions(chat_client):
    r = chat_client.post("/api/chat", json={"question": "x" * 301})
    assert r.status_code == 400


def test_chat_per_visitor_cap(chat_client, monkeypatch):
    import chat
    monkeypatch.setattr(chat, "PER_VISITOR_LIMIT", 2)
    chat_client.get("/")
    codes = [chat_client.post("/api/chat", json={"question": "q"}).status_code
             for _ in range(3)]
    assert codes == [200, 200, 429]


def test_chat_site_wide_cap_spans_visitors(chat_client, monkeypatch):
    """The site-wide cap is the cost ceiling, so it must hold across visitors."""
    import chat
    monkeypatch.setattr(chat, "DAILY_LIMIT", 2)
    codes = []
    for _ in range(3):
        chat_client.delete_cookie("ab_vid")
        chat_client.get("/")
        codes.append(chat_client.post("/api/chat", json={"question": "q"}).status_code)
    assert codes == [200, 200, 429]
