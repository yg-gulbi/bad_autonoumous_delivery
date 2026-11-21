"""Single-writer SQLite journal for fleet ticks. UI readers can open the file read-only."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

from .config import PublishCfg


def stop_item(kind, seq, stop):
    if kind == "PATIENT_TRANSPORT":
        return "픽업" if seq == 1 else "목적지"
    return stop.get("item")


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
        elif kind in {"RESOURCE_HOLD", "ZONE_OCCUPIED_HOLD"} and key not in self.holds:
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
            if started is not None and detail.get("closed_by", detail["reason"]) == "resolved":
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
                id INTEGER PRIMARY KEY, name TEXT, chart_no TEXT NOT NULL, created_at REAL);
            CREATE TABLE IF NOT EXISTS missions (
                id TEXT PRIMARY KEY, robot TEXT, type TEXT NOT NULL, level INTEGER CHECK(level BETWEEN 1 AND 3), stops_json TEXT,
                patient_id INTEGER REFERENCES patients(id), status TEXT NOT NULL, created_at REAL,
                started_at REAL, finished_at REAL);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, ts REAL, robot TEXT, mission_id TEXT, type TEXT, detail_json TEXT);
            CREATE TABLE IF NOT EXISTS cards (
                id TEXT PRIMARY KEY, kind TEXT, robots TEXT, payload_json TEXT,
                opened_at REAL, closed_at REAL, choice TEXT, "by" TEXT, close_reason TEXT);
        """)
        self._migrate()
        self.sequence = self.db.execute(
            "SELECT COALESCE(MAX(CAST(substr(id,3) AS INTEGER)),0) FROM missions WHERE id LIKE 'M-%'"
        ).fetchone()[0]

        self.stats = Metrics()
        for row in self.db.execute("SELECT id FROM missions"):
            self.stats.trip(row["id"])
        for row in self.db.execute("SELECT * FROM events ORDER BY id"):
            self.stats.observe(row["type"], row["ts"], row["robot"], row["mission_id"], json.loads(row["detail_json"]))

    def _migrate(self):
        """Add the history schema and backfill it as a single, restartable transaction."""
        if (self.db.execute("PRAGMA user_version").fetchone()[0] >= 1 and
                self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mission_stops'").fetchone()):
            return
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            columns = {"patients": {"tag_id": "TEXT", "updated_at": "REAL"},
                       "missions": {"requested_by": "TEXT", "ui_json": "TEXT", "req_id": "TEXT",
                                    "assigned_at": "REAL", "cancel_reason": "TEXT"},
                       "events": {"by": "TEXT"}, "cards": {"title": "TEXT", "reason": "TEXT"}}
            for table, additions in columns.items():
                old = {r["name"] for r in self.db.execute(f"PRAGMA table_info({table})")}
                for name, kind in additions.items():
                    if name not in old:
                        self.db.execute(f'ALTER TABLE {table} ADD COLUMN "{name}" {kind}')
            self.db.execute("""CREATE TABLE IF NOT EXISTS mission_stops(
                mission_id TEXT NOT NULL REFERENCES missions(id), seq INTEGER NOT NULL CHECK(seq >= 1),
                zone TEXT NOT NULL, item TEXT, status TEXT, arrived_at REAL,
                confirmed_at REAL, confirmed_by TEXT, PRIMARY KEY(mission_id, seq))""")
            self._migrate_patients()
            for unique, table, fields in [
                    (True, "patients", "chart_no"), (True, "missions", "req_id"),
                    (False, "events", "ts"), (False, "events", "mission_id"), (False, "events", "type"),
                    (False, "missions", "status,created_at"), (False, "cards", "opened_at")]:
                name = table + "_" + fields.replace(",", "_")
                self.db.execute(f"CREATE {'UNIQUE' if unique else ''} INDEX IF NOT EXISTS {name} ON {table}({fields})")
            self.db.execute("""UPDATE events SET "by"=json_extract(detail_json,'$.by')
                               WHERE json_valid(detail_json)""")
            self.db.execute("""UPDATE cards SET title=json_extract(payload_json,'$.title'),
                               reason=json_extract(payload_json,'$.reason') WHERE json_valid(payload_json)""")
            for row in self.db.execute("SELECT * FROM missions ORDER BY created_at, rowid"):
                self._migrate_stops(row)
            self.db.execute("PRAGMA user_version=1")

    def _migrate_patients(self):
        self.db.execute("UPDATE patients SET updated_at=created_at WHERE updated_at IS NULL")
        duplicates = list(self.db.execute("""SELECT chart_no FROM patients WHERE chart_no IS NOT NULL
                                            GROUP BY chart_no HAVING COUNT(*)>1"""))
        if duplicates:
            # Keep every old patient row and its original chart number for audit.
            self.db.execute("ALTER TABLE patients ADD COLUMN legacy_chart_no TEXT")
        for (chart,) in duplicates:
            rows = list(self.db.execute("SELECT * FROM patients WHERE chart_no=? ORDER BY created_at,id", (chart,)))
            canonical = rows[0]["id"]
            for old in rows[1:]:
                self.db.execute("UPDATE missions SET patient_id=? WHERE patient_id=?", (canonical, old["id"]))
                self.db.execute("UPDATE patients SET legacy_chart_no=chart_no, chart_no=NULL WHERE id=?", (old["id"],))
            name = next((r["name"] for r in reversed(rows) if r["name"]), rows[0]["name"])
            self.db.execute("UPDATE patients SET name=?, updated_at=? WHERE id=?", (name, rows[-1]["updated_at"], canonical))

    def _migrate_stops(self, mission):
        stops = json.loads(mission["stops_json"] or "[]")
        meta = json.loads(mission["ui_json"] or "{}")
        if mission["patient_id"] and (meta.get("patient") or {}).get("tag_id"):
            self.db.execute("UPDATE patients SET tag_id=? WHERE id=?",
                            (meta["patient"]["tag_id"], mission["patient_id"]))
        for seq, stop in enumerate(stops, 1):
            self.db.execute("""INSERT INTO mission_stops(mission_id,seq,zone,item,status) VALUES(?,?,?,?,?)""",
                            (mission["id"], seq, stop["zone"], stop_item(mission["type"], seq, stop), "PENDING"))
        # Old stop_index alone cannot distinguish a confirmation from a skip.
        # Restore arrival/confirmation only when the original event identifies the stop.
        seq = 1
        for ev in self.db.execute("""SELECT e.*, c.choice AS mismatch_choice FROM events e
                LEFT JOIN cards c ON e.type='CARD_OPEN' AND c.id=json_extract(e.detail_json,'$.card.card_id')
                                  AND c.kind='STOP_MISMATCH'
                WHERE e.mission_id=? ORDER BY e.id""", (mission["id"],)):
            kind, ts = ev["type"], ev["ts"]
            d = json.loads(ev["detail_json"])
            if kind == "MISSION_ASSIGNED":
                self.db.execute("UPDATE missions SET assigned_at=? WHERE id=?", (ts, mission["id"]))
            if kind == "MISSION_CANCELED":
                self.db.execute("UPDATE missions SET cancel_reason=? WHERE id=?", (d.get("reason"), mission["id"]))
            if ev["mismatch_choice"] == "skip" and seq <= len(stops):
                self.db.execute("UPDATE mission_stops SET status='SKIPPED' WHERE mission_id=? AND seq=?",
                                (mission["id"], seq))
                seq += 1
                continue
            if seq > len(stops) or d.get("destination") != stops[seq-1]["zone"] or d.get("return_home"):
                continue
            key = (mission["id"], seq)
            if kind == "ARRIVE":
                self.db.execute("UPDATE mission_stops SET status='ARRIVED',arrived_at=? WHERE mission_id=? AND seq=?", (ts, *key))
            elif kind == "CONFIRM_STEP":
                if d.get("result", d.get("reason")) == "OK":
                    self.db.execute("""UPDATE mission_stops SET status='CONFIRMED',confirmed_at=?,confirmed_by=?
                                       WHERE mission_id=? AND seq=?""", (ts, ev["by"], *key))
                    seq += 1
                elif d.get("result", d.get("reason")) == "MISMATCH":
                    self.db.execute("UPDATE mission_stops SET status='MISMATCH' WHERE mission_id=? AND seq=?", key)
        # Completed legacy stops with no event evidence have no known outcome.
        completed = len(stops) if mission["status"] == "DONE" else meta.get("stop_index", 0)
        self.db.execute("""UPDATE mission_stops SET status=NULL WHERE mission_id=? AND seq<=? AND status='PENDING'""",
                        (mission["id"], completed))
        if mission["status"] == "CANCELED":
            self.db.execute("""UPDATE mission_stops SET status='SKIPPED' WHERE mission_id=? AND seq>=?""",
                            (mission["id"], meta.get("stop_index", seq - 1) + 1))
        elif mission["status"] == "ACTIVE":
            self.db.execute("""UPDATE mission_stops SET status='ACTIVE' WHERE mission_id=? AND seq=? AND status='PENDING'""",
                            (mission["id"], meta.get("stop_index", seq - 1) + 1))

    def mission_for_request(self, req_id):
        row = self.db.execute("SELECT id FROM missions WHERE req_id=?", (req_id,)).fetchone()
        return row["id"] if row else None

    def recent_finished(self, limit=PublishCfg.finished_mission_limit):
        rows = self.db.execute("""
            SELECT m.*, p.chart_no, p.name FROM missions m LEFT JOIN patients p ON m.patient_id=p.id
            WHERE m.status IN ('DONE','CANCELED') AND m.type IN ('PATIENT_TRANSPORT','CALL','MED_ROUND')
            ORDER BY finished_at DESC, m.rowid DESC LIMIT ?""", (limit,))
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
                    """UPDATE missions SET robot=?,level=?,status=?,assigned_at=?,started_at=?,finished_at=?,cancel_reason=?,
                       ui_json=? WHERE id=?""",
                    (job.robot, job.level, job.status, job.assigned_at, job.started_at, job.finished_at, job.cancel_reason,
                     meta, job.id))
                if not updated.rowcount:
                    patient_id = None
                    if job.patient:
                        patient_id = self.db.execute("""INSERT INTO patients(name,chart_no,tag_id,created_at,updated_at)
                            VALUES(?,?,?,?,?) ON CONFLICT(chart_no) DO UPDATE SET
                            name=COALESCE(NULLIF(excluded.name,''),patients.name),
                            tag_id=COALESCE(NULLIF(excluded.tag_id,''),patients.tag_id),
                            updated_at=excluded.updated_at RETURNING id""",
                            (job.patient.get("name", ""), job.patient["chart_no"], job.patient.get("tag_id"),
                             job.created_at, job.created_at)).fetchone()[0]
                    self.db.execute("""INSERT INTO missions (id,req_id,robot,type,level,stops_json,patient_id,status,
                        created_at,assigned_at,started_at,finished_at,requested_by,ui_json,cancel_reason)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (job.id, job.req_id, job.robot, job.kind, job.level,
                        json.dumps(job.stops, ensure_ascii=False), patient_id, job.status, job.created_at, job.assigned_at,
                        job.started_at, job.finished_at, job.requested_by, meta, job.cancel_reason))
                for seq, (stop, log) in enumerate(zip(job.stops, job.stop_log, strict=True), 1):
                    self.db.execute("""INSERT INTO mission_stops VALUES(?,?,?,?,?,?,?,?)
                        ON CONFLICT(mission_id,seq) DO UPDATE SET status=excluded.status,arrived_at=excluded.arrived_at,
                        confirmed_at=excluded.confirmed_at,confirmed_by=excluded.confirmed_by""",
                        (job.id, seq, stop["zone"], stop_item(job.kind, seq, stop), log["status"], log["arrived_at"],
                         log["confirmed_at"], log["confirmed_by"]))
            for event in events:
                d = event["detail"]
                self.db.execute('INSERT INTO events(ts,robot,mission_id,type,"by",detail_json) VALUES(?,?,?,?,?,?)',
                                (event["ts"], event["robot"], event["mission_id"], event["type"], d.get("by"),
                                 json.dumps(d, ensure_ascii=False)))
                if event["type"] == "CARD_OPEN":
                    card = d["card"]
                    self.db.execute("INSERT INTO cards(id,kind,robots,payload_json,opened_at,title,reason) VALUES(?,?,?,?,?,?,?)",
                                    (card["card_id"], card["kind"], json.dumps(card["robots"]),
                                     json.dumps(card, ensure_ascii=False), card["created_at"], card["title"], card["reason"]))
                elif event["type"] == "CARD_CLOSED":
                    self.db.execute('UPDATE cards SET closed_at=?, choice=?, "by"=?, close_reason=? WHERE id=?',
                                    (event["ts"], d.get("choice"), d.get("by"), d.get("closed_by", d["reason"]), d["card_id"]))

        for event in events:
            self.stats.observe(event["type"], event["ts"], event["robot"], event["mission_id"], event["detail"])

    def metrics(self):
        return {**self.stats.kpi(), "missions": deepcopy(self.stats.trips),
                "deadlock_resolve_s": list(self.stats.resolved)}

    def close(self):
        self.db.close()
