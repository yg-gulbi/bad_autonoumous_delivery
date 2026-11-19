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
            "patients", "missions", "mission_stops", "cards", "events"}
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


def test_37_migration_keeps_old_rows_and_can_reopen(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as old:
        old.executescript("""
            CREATE TABLE patients(id INTEGER PRIMARY KEY,name TEXT,chart_no TEXT,created_at REAL);
            CREATE TABLE missions(id TEXT PRIMARY KEY,robot TEXT,type TEXT,level INTEGER,stops_json TEXT,
                patient_id INTEGER REFERENCES patients(id),status TEXT,created_at REAL,started_at REAL,finished_at REAL);
            CREATE TABLE events(id INTEGER PRIMARY KEY,ts REAL,robot TEXT,mission_id TEXT,type TEXT,detail_json TEXT);
            CREATE TABLE cards(id TEXT PRIMARY KEY,kind TEXT,robots TEXT,payload_json TEXT,opened_at REAL,closed_at REAL,
                choice TEXT,[by] TEXT,close_reason TEXT);
            INSERT INTO patients VALUES (7,'가','C1',0),(8,'나','C1',5);
            INSERT INTO missions VALUES ('M-0010','robot_a','PATIENT_TRANSPORT',1,'[{"zone":"A"},{"zone":"E"}]',8,'CANCELED',5,6,8);
            INSERT INTO events VALUES (51,7,'robot_a','M-0010','ARRIVE','{"destination":"A"}'),
              (52,7.1,'robot_a','M-0010','CONFIRM_STEP','{"destination":"A","reason":"OK","by":"기록자"}'),
              (53,8,'robot_a','M-0010','MISSION_CANCELED','{"reason":"대기 임무 취소"}');
            INSERT INTO cards VALUES ('K-old','BLOCKED','[]','{"title":"장애물","reason":"30초 정지"}',0,1,'retry','가','selected');
        """)
    for _ in range(2):
        store = Store(path)
        try:
            db = store.db
            assert [tuple(r) for r in db.execute("SELECT id,chart_no,legacy_chart_no FROM patients ORDER BY id")] == [(7,"C1",None),(8,None,"C1")]
            assert tuple(db.execute("SELECT id,patient_id,cancel_reason FROM missions").fetchone()) == ("M-0010",7,"대기 임무 취소")
            assert [tuple(r) for r in db.execute("SELECT status,confirmed_by FROM mission_stops ORDER BY seq")] == [("CONFIRMED","기록자"),("SKIPPED",None)]
            assert tuple(db.execute("SELECT title,reason FROM cards").fetchone()) == ("장애물","30초 정지")
            assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 3
            assert list(db.execute("PRAGMA foreign_key_check")) == []
            with pytest.raises(sqlite3.IntegrityError):
                db.execute("INSERT INTO patients(chart_no) VALUES('C1')")
            db.rollback()
        finally:
            store.close()
