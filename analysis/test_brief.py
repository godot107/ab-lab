"""The brief's one hard rule: no A-vs-B comparison before the stopping rule."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

import store  # noqa: E402
from analysis import brief  # noqa: E402


def make_db(path: Path, per_arm: int, rate_a: float, rate_b: float, device="desktop"):
    store.init(path)
    for arm, rate in (("A", rate_a), ("B", rate_b)):
        for i in range(per_arm):
            vid = f"{arm}-{i}"
            ts = f"2026-10-01T12:{i // 60 % 60:02d}:{i % 60:02d}+00:00"
            store.record(ts, vid, "exp", arm, "exposure", device=device, path=path)
            store.record(ts, vid, "exp", arm, "pageview", device=device, path=path)
            if i < rate * per_arm:
                store.record(ts, vid, "exp", arm, "detail_click", "trend", path=path)


def test_interim_brief_hides_the_comparison(tmp_path):
    db = tmp_path / "e.db"
    make_db(db, 40, 0.20, 0.60)          # a huge gap, deliberately
    text = brief.render(brief.load(db, "exp"), "exp", want_final=False)
    assert "status update" in text and "No decision yet" in text
    assert "Layout A (tiles first)" not in text   # no per-arm table
    assert "20%" not in text and "60%" not in text  # no per-arm rates


def test_final_is_refused_before_the_stopping_rule(tmp_path):
    db = tmp_path / "e.db"
    make_db(db, 40, 0.2, 0.6)
    with pytest.raises(SystemExit, match="Stopping rule not met"):
        brief.render(brief.load(db, "exp"), "exp", want_final=True)


def test_final_brief_decides_by_the_preregistered_rule(tmp_path):
    db = tmp_path / "e.db"
    make_db(db, 260, 0.30, 0.45)
    text = brief.render(brief.load(db, "exp"), "exp", want_final=True)
    assert "decision brief" in text and "**Adopt layout B (chart first).**" in text


def test_null_result_is_not_called_equivalence(tmp_path):
    db = tmp_path / "e.db"
    make_db(db, 260, 0.30, 0.31)
    text = brief.render(brief.load(db, "exp"), "exp", want_final=True)
    assert "No change" in text and "*not* evidence that the layouts are equivalent" in text


def test_synthetic_data_is_labelled(tmp_path):
    db = tmp_path / "e.db"
    make_db(db, 30, 0.3, 0.3, device="synthetic")
    assert "Synthetic demo data" in brief.render(brief.load(db, "exp"), "exp", False)
