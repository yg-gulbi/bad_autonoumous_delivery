"""Single-writer SQLite journal for fleet ticks. UI readers can open the file read-only."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3


class Metrics:
    def __init__(self):
        self.trips, self.waiting, self.holds = {}, {}, {}
        self.cards, self.yields = {}, {}
        self.hold_count = self.pause_count = self.deadlock_count = 0
        self.resolved = []

    def trip(self, mid):
        return self.trips.setdefault(mid, {"departure": None, "arrival": None, "hold_s": [], "level3_wait_s": []})

    def observe(self, kind, ts, robot, mid, detail):
        key = robot, mid
        trip = self.trip(mid) if mid else None
        if kind in {"RESOURCE_REQUEST", "RESOURCE_GRANT", "MISSION_CANCELED", "MISSION_DONE"}:
            since = self.holds.pop(key, None)
            if since is not None and trip:
                trip["hold_s"].append(ts - since)
            req = self.waiting.pop(key, None)
            if kind == "RESOURCE_REQUEST":
                self.waiting[key] = ts, detail.get("level")
            elif kind == "RESOURCE_GRANT" and trip and req and req[1] == 3:
                trip["level3_wait_s"].append(ts - req[0])
        elif kind == "RESOURCE_HOLD" and key not in self.holds:
            self.holds[key] = ts
            self.hold_count += 1
        elif kind == "LEG_START" and trip and trip["departure"] is None:
            trip["departure"] = ts
        elif kind == "ARRIVE" and trip:
            trip["arrival"] = ts
        elif kind == "PAUSE" or kind == "CORRIDOR_YIELD_START":
            self.pause_count += 1
        elif kind == "DEADLOCK_DETECTED":
            self.deadlock_count += 1
            self.cards[detail["card_id"]] = ts
        elif kind == "CARD_CLOSED":
            started = self.cards.pop(detail["card_id"], None)
            if started is not None and detail["reason"] == "resolved":
                self.resolved.append(ts - started)
        elif kind == "YIELD_START":
            self.yields[robot, detail["source_room"]] = detail["detected_at"]
        elif kind == "RESOURCE_RELEASE":
            for resource in detail["resources"]:
                started = self.yields.pop((robot, resource), None)
                if started is not None:
                    self.resolved.append(ts - started)

    def kpi(self):
        return {"hold_count": self.hold_count, "deadlock_count": self.deadlock_count,
                "deadlock_resolve_s_avg": sum(self.resolved) / len(self.resolved) if self.resolved else None,
                "pause_count": self.pause_count}


class Store:
    def __init__(self, path):
        if str(path) != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(Path(path).expanduser()), timeout=0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS patients (
                id INTEGER PRIMARY KEY, name TEXT, chart_no TEXT, created_at REAL);
            CREATE TABLE IF NOT EXISTS missions (
                id TEXT PRIMARY KEY, robot TEXT, type TEXT, level INTEGER, stops_json TEXT,
                patient_id INTEGER REFERENCES patients(id), status TEXT, created_at REAL,
                started_at REAL, finished_at REAL);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, ts REAL, robot TEXT, mission_id TEXT, type TEXT, detail_json TEXT);
            CREATE TABLE IF NOT EXISTS cards (
                id TEXT PRIMARY KEY, kind TEXT, robots TEXT, payload_json TEXT,
                opened_at REAL, closed_at REAL, choice TEXT, "by" TEXT, close_reason TEXT);
        """)
        if "requested_by" not in {r["name"] for r in self.db.execute("PRAGMA table_info(missions)")}:
            self.db.execute("ALTER TABLE missions ADD COLUMN requested_by TEXT")
        if "ui_json" not in {r["name"] for r in self.db.execute("PRAGMA table_info(missions)")}:
            self.db.execute("ALTER TABLE missions ADD COLUMN ui_json TEXT")
        self.sequence = self.db.execute(
            "SELECT COALESCE(MAX(CAST(substr(id,3) AS INTEGER)),0) FROM missions WHERE id LIKE 'M-%'"
        ).fetchone()[0]

        self.stats = Metrics()
        for row in self.db.execute("SELECT id FROM missions"):
            self.stats.trip(row["id"])
        for row in self.db.execute("SELECT * FROM events ORDER BY id"):
            self.stats.observe(row["type"], row["ts"], row["robot"], row["mission_id"], json.loads(row["detail_json"]))

    def recent_finished(self):
        rows = self.db.execute("""
            SELECT m.*, p.chart_no, p.name FROM missions m LEFT JOIN patients p ON m.patient_id=p.id
            WHERE m.status IN ('DONE','CANCELED') AND m.type IN ('PATIENT_TRANSPORT','CALL','MED_ROUND')
            ORDER BY finished_at DESC, m.rowid DESC LIMIT 20""")
        result = {}
        for r in reversed(list(rows)):
            stops = json.loads(r["stops_json"])
            meta = json.loads(r["ui_json"] or "{}")
            result[r["id"]] = {"mission_id": r["id"], "type": r["type"], "robot": r["robot"], "level": r["level"],
                "status": r["status"], "created_at": r["created_at"], "finished_at": r["finished_at"],
                "stops": [s["zone"] for s in stops], "items": [s.get("item") for s in stops],
                "stop_index": meta.get("stop_index", len(stops) if r["status"] == "DONE" else 0),
                "requested_by": r["requested_by"], "requesting_department": meta.get("department"),
                "patient": meta.get("patient", {"name": r["name"], "chart_no": r["chart_no"]} if r["patient_id"] else None), "wait_reason": None}
        return result

    def next_id(self):
        self.sequence += 1
        return f"M-{self.sequence:04d}"

    def save(self, jobs, events):
        with self.db:
            for job in jobs:
                meta = json.dumps({"stop_index": job.index, "patient": job.patient, "department": job.department}, ensure_ascii=False)
                updated = self.db.execute(
                    "UPDATE missions SET robot=?, status=?, started_at=?, finished_at=?, ui_json=? WHERE id=?",
                    (job.robot, job.status, job.started_at, job.finished_at, meta, job.id))
                if updated.rowcount:
                    continue
                patient_id = None
                if job.patient:
                    patient_id = self.db.execute(
                        "INSERT INTO patients(name,chart_no,created_at) VALUES(?,?,?)",
                        (job.patient.get("name", ""), job.patient["chart_no"], job.created_at)).lastrowid
                self.db.execute("""INSERT INTO missions (id,robot,type,level,stops_json,patient_id,status,created_at,
                    started_at,finished_at,requested_by,ui_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    job.id, job.robot, job.kind, job.level, json.dumps(job.stops, ensure_ascii=False),
                    patient_id, job.status, job.created_at, job.started_at, job.finished_at, job.requested_by, meta))
            for event in events:
                d = event["detail"]
                self.db.execute("INSERT INTO events(ts,robot,mission_id,type,detail_json) VALUES(?,?,?,?,?)",
                                (event["ts"], event["robot"], event["mission_id"], event["type"],
                                 json.dumps(d, ensure_ascii=False)))
                if event["type"] == "CARD_OPEN":
                    card = d["card"]
                    self.db.execute("INSERT INTO cards(id,kind,robots,payload_json,opened_at) VALUES(?,?,?,?,?)",
                                    (card["card_id"], card["kind"], json.dumps(card["robots"]),
                                     json.dumps(card, ensure_ascii=False), card["created_at"]))
                elif event["type"] == "CARD_CLOSED":
                    self.db.execute('UPDATE cards SET closed_at=?, choice=?, "by"=?, close_reason=? WHERE id=?',
                                    (event["ts"], d.get("choice"), d.get("by"), d["reason"], d["card_id"]))

        for event in events:
            self.stats.observe(event["type"], event["ts"], event["robot"], event["mission_id"], event["detail"])

    def metrics(self):
        return {**self.stats.kpi(), "missions": deepcopy(self.stats.trips),
                "deadlock_resolve_s": list(self.stats.resolved)}

    def close(self):
        self.db.close()
