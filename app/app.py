"""AB-Lab — serves both dashboard layouts from one URL and logs the events
Experiment 001 is decided on.

Assignment happens here, in the app, rather than in the proxy. That costs a
little elegance and buys the thing that matters: an explicit exposure event
written at the moment of bucketing, which is what makes Sample Ratio Mismatch
diagnosable after the fact.
"""
from __future__ import annotations

import os

from flask import Flask, jsonify, render_template, request

import anthropic

import chat
import snapshot
from ablab import Experiment
from ablab.flask_ext import COOKIE, is_bot

EXP = Experiment(
    os.environ.get("AB_EXPERIMENT", "exp001_layout"),
    salt=os.environ.get("AB_SALT", "exp001"),
    split=int(os.environ.get("AB_SPLIT", "50")),
    cookie_secure=os.environ.get("COOKIE_SECURE", "1") == "1",
)

app = Flask(__name__)
# The vendored Plotly bundle is versioned in its filename, so cache it hard.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 60 * 60 * 24 * 30
EXP.init_app(app)  # /api/events, /api/stats, the visitor cookie
chat.init()
# Frozen for the whole window: the content is something both arms hold constant.
SNAP = snapshot.load()


@app.route("/")
def dashboard():
    # Opted out or non-human: expose() returns None, so serve the control
    # layout, set no cookie, log nothing. These visits are outside the
    # experiment entirely rather than being counted as a silent arm.
    variant = EXP.expose()
    return render_template("dashboard.html", variant=variant or "A",
                           tracking=variant is not None, experiment=EXP.name,
                           snap=SNAP, chat_enabled=chat.enabled())


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


if __name__ == "__main__":
    app.run(debug=True, port=5000)
