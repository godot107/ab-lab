"""The dashboard's content: a frozen Hiring Lab snapshot, loaded once.

Loaded at import and never refreshed, so every visitor in both arms sees the
same numbers for the whole window. Rebuild with data/build_snapshot.py, and
only before traffic starts.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

PATH = Path(__file__).resolve().parent / "snapshot.json"


def load(path: Path = PATH) -> dict:
    snap = json.loads(path.read_text())
    snap["as_of_label"] = date.fromisoformat(snap["as_of"]).strftime("%b %-d, %Y")
    return snap
