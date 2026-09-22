"""AB-Lab — serves both dashboard layouts from one URL and logs the events
Experiment 001 is decided on.

Assignment happens here, in the app, rather than in the proxy. That costs a
little elegance and buys the thing that matters: an explicit exposure event
written at the moment of bucketing, which is what makes Sample Ratio Mismatch
diagnosable after the fact.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, make_response, render_template, request

import anthropic

import chat
import snapshot
import store
from assignment import assign

EXPERIMENT = os.environ.get("AB_EXPERIMENT", "exp001_layout")
SALT = os.environ.get("AB_SALT", "exp001")
SPLIT = int(os.environ.get("AB_SPLIT", "50"))
COOKIE = "ab_vid"
COOKIE_MAX_AGE = 60 * 60 * 24 * 90

# Substring match on the UA, applied identically to both arms and declared in
# the pre-registration. Crude, but a filter tuned after seeing the results is
# not a filter, it is a knob.
BOT_MARKERS = ("bot", "crawler", "spider", "headless", "lighthouse", "curl", "wget")

app = Flask(__name__)
# The vendored Plotly bundle is versioned in its filename, so cache it hard.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 60 * 60 * 24 * 30
store.init()
chat.init()
# Frozen for the whole window: the content is something both arms hold constant.
SNAP = snapshot.load()


def opted_out() -> bool:
    """Do Not Track / Global Privacy Control.

    Honoring these costs traffic on a site that has little to spare. It is
    still the right default: a visitor who has asked not to be measured is not
    a visitor whose data improves this experiment.
    """
    return request.headers.get("DNT") == "1" or request.headers.get("Sec-GPC") == "1"


def is_bot() -> bool:
    ua = request.headers.get("User-Agent", "").lower()
    return not ua or any(m in ua for m in BOT_MARKERS)


def device_class() -> str:
    """Coarse device class for segmenting results. Only the class is stored,
    never the user agent. Synthetic demo traffic labels itself, so no readout
    can mistake it for real visitors."""
    ua = request.headers.get("User-Agent", "").lower()
    if "ab-lab-synthetic" in ua:
        return "synthetic"
    if "ipad" in ua or "tablet" in ua:
        return "tablet"
    if "mobi" in ua or "android" in ua or "iphone" in ua:
        return "mobile"
    return "desktop"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@app.route("/")
def dashboard():
    # Opted out or non-human: serve the control layout, set no cookie, log
    # nothing. These visits are outside the experiment entirely rather than
    # being counted as a silent arm.
    if opted_out() or is_bot():
        return render_template("dashboard.html", variant="A", tracking=False,
                               experiment=EXPERIMENT, snap=SNAP, chat_enabled=chat.enabled())

    visitor_id = request.cookies.get(COOKIE)
    is_new = visitor_id is None
    if is_new:
        visitor_id = str(uuid.uuid4())

    variant = assign(visitor_id, SALT, SPLIT)
    ts, device = now(), device_class()
    # The exposure is the denominator: one per visitor, enforced by the schema.
    # The pageview is every load, so returning visits are countable without
    # touching that denominator.
    store.record(ts, visitor_id, EXPERIMENT, variant, "exposure", device=device)
    store.record(ts, visitor_id, EXPERIMENT, variant, "pageview", device=device)

    resp = make_response(render_template(
        "dashboard.html", variant=variant, tracking=True, experiment=EXPERIMENT,
        snap=SNAP, chat_enabled=chat.enabled()))
    if is_new:
        resp.set_cookie(COOKIE, visitor_id, max_age=COOKIE_MAX_AGE, httponly=True,
                        samesite="Lax", secure=os.environ.get("COOKIE_SECURE", "1") == "1")
    return resp


@app.route("/api/events", methods=["POST"])
def collect():
    """Beacon endpoint. The variant is NOT taken from the request body --
    it is recomputed server-side from the visitor id, so a client cannot
    report itself into the other arm."""
    if opted_out() or is_bot():
        return "", 204

    visitor_id = request.cookies.get(COOKIE)
    if not visitor_id:
        return "", 204  # never exposed, so nothing to attribute

    payload = request.get_json(silent=True) or {}
    event = payload.get("event")
    if event not in ("detail_click", "interaction"):
        return jsonify(error="unknown event"), 400

    target = (payload.get("target") or "")[:64] or None
    store.record(now(), visitor_id, EXPERIMENT, assign(visitor_id, SALT, SPLIT),
                 event, target)
    return "", 204


@app.route("/api/chat", methods=["POST"])
def ask():
    """Single-turn question about the snapshot. Not part of the experiment:
    nothing here touches the events table, whatever the visitor's arm."""
    if not chat.enabled():
        return jsonify(error="chat is off"), 503
    if is_bot():
        return jsonify(error="unavailable"), 403

    question = ((request.get_json(silent=True) or {}).get("question") or "").strip()
    if not question:
        return jsonify(error="empty question"), 400
    if len(question) > chat.MAX_QUESTION_CHARS:
        return jsonify(error=f"keep it under {chat.MAX_QUESTION_CHARS} characters"), 400

    # Opted-out visitors carry no cookie, so they share one small pool.
    if not chat.take_quota(request.cookies.get(COOKIE) or "anon"):
        return jsonify(error="question limit reached for today"), 429

    try:
        return jsonify(answer=chat.ask(question, SNAP))
    except anthropic.RateLimitError:
        return jsonify(error="busy, try again in a minute"), 503
    except anthropic.APIError as e:
        app.logger.warning("chat failed: %r", e)
        return jsonify(error="couldn't get an answer right now"), 502


@app.route("/healthz")
def healthz():
    return "ok", 200


@app.route("/api/stats")
def stats():
    """Live counts. Deliberately NOT a p-value.

    Exposing significance here would make peeking one click away, and the
    pre-registration commits to a single analysis at a fixed horizon. Run
    analysis/report.py when the experiment ends.
    """
    return jsonify(experiment=EXPERIMENT, counts=store.counts(EXPERIMENT))


if __name__ == "__main__":
    app.run(debug=True, port=5000)
