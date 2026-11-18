import json
import sqlite3

import pytest

from part3_fleet_manager.core.mission import Mission
from part3_fleet_manager.core.store import Store


def test_wal_readonly_visibility_transaction_and_reopen(tmp_path):
    file = tmp_path / "nested" / "bad.db"
    store = Store(file)
    reader = sqlite3.connect(f"{file.as_uri()}?mode=ro", uri=True)
    try:
        assert reader.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert {r[0] for r in reader.execute("SELECT name FROM sqlite_master WHERE type='table'")} == {
            "patients", "missions", "cards", "events"}
        job = Mission(store.next_id(), "robot_a", "PATIENT_TRANSPORT", 2, [{"zone": "A"}],
                      {"name": "테스트", "chart_no": "T"}, 0)
        with pytest.raises(KeyError):
            store.save([job], [{"detail": {}}])
        assert reader.execute("SELECT count(*) FROM patients").fetchone()[0] == 0
        store.save([job], [])
        job.status = "DONE"
        job.finished_at = 4
        store.save([job], [])
        assert reader.execute("SELECT count(*) FROM patients").fetchone()[0] == 1
        status, stops = reader.execute("SELECT status,stops_json FROM missions").fetchone()
        assert status == "DONE" and json.loads(stops) == job.stops
    finally:
        store.close()
        reader.close()
    reopened = Store(file)
    try:
        assert reopened.next_id() == "M-0002"
    finally:
        reopened.close()


def test_patient_without_name_keeps_chart_number_separate(tmp_path):
    store = Store(tmp_path / "patient.db")
    try:
        job = Mission(store.next_id(), "robot_a", "PATIENT_TRANSPORT", 1, [{"zone": "A"}], {"chart_no": "T-1"}, 0)
        job.status, job.finished_at = "DONE", 1
        store.save([job], [])
        assert tuple(store.db.execute("SELECT name,chart_no FROM patients").fetchone()) == ("", "T-1")
        assert store.recent_finished()[job.id]["patient"] == {"chart_no": "T-1"}
    finally:
        store.close()
