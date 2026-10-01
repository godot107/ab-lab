"""Flask integration: one Experiment per running test.

Everything that decides who is in the experiment lives here, so a host app
can't get it subtly different: the opt-out and bot rules, the sticky cookie,
the exposure row, and server-side attribution of later events. The host only
asks "which arm is this visitor in?" and renders accordingly.

    exp = Experiment("exp001_layout", salt=..., events=("detail_click",))
    exp.init_app(server)          # a Flask app, or a Dash app's .server
    variant = exp.expose()        # in a page handler: "A", "B", or None

None means the visit is outside the experiment (opted out, a bot, or no
request in flight): serve the control and log nothing.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from flask import Blueprint, Flask, g, has_request_context, jsonify, request

from ablab import store
from ablab.assignment import assign

COOKIE = "ab_vid"
COOKIE_MAX_AGE = 60 * 60 * 24 * 90

# Substring match on the UA, applied identically to both arms and declared in
# the pre-registration. Crude, but a filter tuned after seeing the results is
# not a filter, it is a knob.
BOT_MARKERS = ("bot", "crawler", "spider", "headless", "lighthouse", "curl", "wget")


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


class Experiment:
    def __init__(self, name: str, salt: str, split: int = 50,
                 events: tuple[str, ...] = ("detail_click", "interaction"),
                 conversion: str = "detail_click", cookie_secure: bool = True):
        if conversion not in events:
            raise ValueError("the conversion event must be one the collector accepts")
        self.name, self.salt, self.split = name, salt, split
        self.events, self.conversion = events, conversion
        self.cookie_secure = cookie_secure

    def tracked(self) -> bool:
        return has_request_context() and not (opted_out() or is_bot())

    def variant(self) -> str | None:
        """The arm of the current visitor, without logging anything."""
        if not self.tracked():
            return None
        vid = request.cookies.get(COOKIE) or g.get("ablab_new_vid")
        return assign(vid, self.salt, self.split) if vid else None

    def expose(self) -> str | None:
        """Bucket the current visitor and log the exposure and a pageview.

        Call once per page load, from the handler that renders the variant.
        The exposure is the denominator: one per visitor, enforced by the
        schema. The pageview is every load, so returning visits are countable
        without touching that denominator.
        """
        if not self.tracked():
            return None
        vid = request.cookies.get(COOKIE) or g.get("ablab_new_vid")
        if vid is None:
            vid = g.ablab_new_vid = str(uuid.uuid4())  # cookie set in after_request
        variant = assign(vid, self.salt, self.split)
        ts, device = now(), device_class()
        store.record(ts, vid, self.name, variant, "exposure", device=device)
        store.record(ts, vid, self.name, variant, "pageview", device=device)
        return variant

    def track(self, event: str, target: str | None = None) -> bool:
        """Log an event from server code (e.g. a Dash callback) for the current
        visitor. Same rules as the beacon: untracked or never-exposed visitors
        log nothing, and the arm comes from the cookie, not the caller."""
        if event not in self.events:
            raise ValueError(f"{event!r} is not an event this experiment collects")
        vid = request.cookies.get(COOKIE) if self.tracked() else None
        if not vid:
            return False
        return store.record(now(), vid, self.name, assign(vid, self.salt, self.split),
                            event, (target or "")[:64] or None)

    def init_app(self, app: Flask) -> None:
        store.init()
        bp = Blueprint("ablab", __name__)
        bp.add_url_rule("/api/events", "collect", self._collect, methods=["POST"])
        bp.add_url_rule("/api/stats", "stats", self._stats)
        app.register_blueprint(bp)
        app.after_request(self._set_cookie)

    def _set_cookie(self, resp):
        vid = g.get("ablab_new_vid")
        if vid and COOKIE not in request.cookies:
            resp.set_cookie(COOKIE, vid, max_age=COOKIE_MAX_AGE, httponly=True,
                            samesite="Lax", secure=self.cookie_secure)
        return resp

    def _collect(self):
        """Beacon endpoint. The variant is NOT taken from the request body --
        it is recomputed server-side from the visitor id, so a client cannot
        report itself into the other arm."""
        if not self.tracked():
            return "", 204
        vid = request.cookies.get(COOKIE)
        if not vid:
            return "", 204  # never exposed, so nothing to attribute

        payload = request.get_json(silent=True) or {}
        event = payload.get("event")
        if event not in self.events:
            return jsonify(error="unknown event"), 400

        target = (payload.get("target") or "")[:64] or None
        store.record(now(), vid, self.name, assign(vid, self.salt, self.split),
                     event, target)
        return "", 204

    def _stats(self):
        """Live counts. Deliberately NOT a p-value.

        Exposing significance here would make peeking one click away, and the
        pre-registration commits to a single analysis at a fixed horizon. Run
        analysis/report.py when the experiment ends.
        """
        return jsonify(experiment=self.name,
                       counts=store.counts(self.name, conversion=self.conversion))
