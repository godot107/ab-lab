"""Event store: SQLite, one row per event.

SQLite because the whole experiment is a few thousand rows on a 512 MB
instance. The schema is deliberately narrow -- see docs/telemetry_options.md:
if a column can't be traced to a metric in the pre-registration, it isn't here.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.environ.get("AB_DB_PATH", "/data/events.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,   -- UTC ISO8601, second resolution
    visitor_id  TEXT    NOT NULL,   -- random UUID, no PII, no IP
    experiment  TEXT    NOT NULL,
    variant     TEXT    NOT NULL CHECK (variant IN ('A','B')),
    event       TEXT    NOT NULL,   -- exposure | pageview | detail_click | interaction
    target      TEXT,              -- which element, for secondary metrics
    device      TEXT               -- mobile | tablet | desktop | synthetic; exposure
                                   -- and pageview rows only. A class, never the UA.
);
CREATE INDEX IF NOT EXISTS idx_events_exp ON events (experiment, variant, event);
-- One exposure per visitor per experiment. Enforced in the schema rather than
-- in application code: a double-counted exposure silently corrupts the
-- denominator of every rate metric, and SRM would not necessarily catch it.
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_exposure
    ON events (experiment, visitor_id) WHERE event = 'exposure';
"""


@contextmanager
def connect(path: Path | None = None):
    db = Path(path or DB_PATH)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init(path: Path | None = None) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        # Databases created before `device` existed. Additive only: a column
        # is added, nothing is rewritten.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
        if "device" not in cols:
            conn.execute("ALTER TABLE events ADD COLUMN device TEXT")


def record(ts, visitor_id, experiment, variant, event, target=None, device=None,
           path=None) -> bool:
    """Insert one event. Returns False if it was a duplicate exposure."""
    with connect(path) as conn:
        try:
            conn.execute(
                "INSERT INTO events (ts, visitor_id, experiment, variant, event, target, device)"
                " VALUES (?,?,?,?,?,?,?)",
                (ts, visitor_id, experiment, variant, event, target, device),
            )
            return True
        except sqlite3.IntegrityError:
            return False  # duplicate exposure, by design


def counts(experiment: str, path=None) -> dict[str, dict[str, int]]:
    """Per-arm exposed visitors and converters -- the two numbers the z-test needs."""
    with connect(path) as conn:
        rows = conn.execute(
            """
            SELECT variant,
                   COUNT(DISTINCT CASE WHEN event='exposure' THEN visitor_id END) AS exposed,
                   COUNT(DISTINCT CASE WHEN event='detail_click' THEN visitor_id END) AS converted
            FROM events WHERE experiment = ? GROUP BY variant
            """,
            (experiment,),
        ).fetchall()
    return {r["variant"]: {"exposed": r["exposed"], "converted": r["converted"]} for r in rows}
