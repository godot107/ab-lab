"""The Dash app starts on synthetic data and serves the page and /healthz."""
import importlib
import sys

import dq_checks as q
import hiringlab_dashboard as hd


def test_app_serves_page_and_health(sqlite_db, tmp_path, monkeypatch):
    monkeypatch.setattr(hd, "DB", sqlite_db)
    monkeypatch.setattr(q, "HISTORY_DB", tmp_path / "dq_history.db")
    sys.modules.pop("hiringlab_app", None)
    app = importlib.import_module("hiringlab_app")

    client = app.server.test_client()
    assert client.get("/").status_code == 200
    health = client.get("/healthz").get_json()
    assert health["status"] == "ok" and health["data_through"] == "2026-09-18"
    assert health["blocking_passed"] == "8/8"

    m = app.compute("total postings", 52)
    assert m["n"] == 4
    assert app.verdict(m["chg"], m["lim"]).split()[0] in {"within", "beyond"}
    assert set(m["sec"]["beyond"]) <= {True, False}
    assert (tmp_path / "dq_history.db").exists()
