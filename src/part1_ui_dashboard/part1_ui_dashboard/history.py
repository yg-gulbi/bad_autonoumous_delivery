"""Read-only SQLite views of the fleet journal, one snapshot per HTTP request."""
import csv
from datetime import date, datetime, timedelta
from io import StringIO
import json
from math import isfinite
from pathlib import Path
import sqlite3

from flask import Blueprint, Response, abort, g, jsonify, request

from part3_fleet_manager.core.store import Metrics
from .ui_api import present_event, legacy_names, DEFAULT_ROBOT_NAMES


def open_history(path):
    if not path:
        raise sqlite3.OperationalError("DB path unavailable")
    return sqlite3.connect(Path(path).expanduser().resolve().as_uri() + "?mode=ro", uri=True, timeout=1)


def period():
    bounds = []
    for key in ("from", "to"):
        raw = request.args.get(key)
        if not raw:
            continue
        operator = ">=" if key == "from" else "<="
        try:
            try:
                stamp = float(raw)
            except ValueError:
                day = date.fromisoformat(raw)
                if key == "to":
                    day += timedelta(days=1)
                    operator = "<"
                stamp = datetime.combine(day, datetime.min.time()).timestamp()
            if not isfinite(stamp):
                raise ValueError
        except (ValueError, OverflowError, OSError):
            abort(400, "날짜 또는 epoch 초를 입력하세요.")
        bounds.append((operator, stamp))
    return bounds


def filters(time_column, exact=()):
    clauses, args = [], []
    for column, key in exact:
        value = request.args.get(key)
        if value:
            clauses.append(column + "=?")
            args.append(value)
    for op, stamp in period():
        clauses.append(time_column + op + "?")
        args.append(stamp)
    return clauses, args


def paged(db, sql, params, convert):
    try:
        page = int(request.args.get("page", 1))
        if not 1 <= page <= (2**63 - 1) // 50:
            raise ValueError
    except ValueError:
        abort(400, "쪽 번호는 1 이상의 정수입니다.")
    rows = db.execute(sql + " LIMIT 51 OFFSET ?", [*params, (page - 1) * 50]).fetchall()
    return {"items": [convert(row) for row in rows[:50]], "page": page, "has_more": len(rows) > 50}


MISSIONS = """SELECT m.*,p.chart_no,p.name,p.tag_id FROM missions m LEFT JOIN patients p ON p.id=m.patient_id"""
CLINICAL = "m.type IN ('PATIENT_TRANSPORT','MED_ROUND','CALL')"


def mission_row(row):
    m = dict(row)
    m["mission_id"] = m["id"]
    stops = json.loads(m.pop("stops_json") or "[]")
    meta = json.loads(m.pop("ui_json") or "{}")
    m["stops"], m["items"] = [s["zone"] for s in stops], [s.get("item") for s in stops]
    m["stop_index"] = meta.get("stop_index", 0)
    m["requesting_department"] = meta.get("department")
    m["patient"] = ({k: m[k] for k in ("chart_no", "name", "tag_id")} if m["patient_id"] else None)
    return m


def history_api(path, names, robots=None):
    robots = robots or DEFAULT_ROBOT_NAMES
    api = Blueprint("history", __name__, url_prefix="/api")

    @api.before_request
    def connect():
        g.history_db = open_history(path)
        g.history_db.row_factory = sqlite3.Row
        g.history_db.execute("BEGIN")
        g.history_db.create_function("event_text", 3, lambda kind, robot, detail:
            searchable({"type": kind, "robot": robot, "detail": json.loads(detail)}))

    @api.teardown_request
    def disconnect(error):
        db = g.pop("history_db", None)
        if db is not None:
            db.close()

    @api.errorhandler(400)
    def invalid(error):
        return jsonify(message=error.description), 400

    @api.errorhandler(sqlite3.Error)
    def unavailable(error):
        return jsonify(message="기록 DB에 연결할 수 없습니다"), 503

    def present(data):
        d = data["detail"]
        card = None
        if data["type"] == "CARD_CLOSED" and d.get("reason") == "selected" and d.get("card_id"):
            row = g.history_db.execute("SELECT payload_json FROM cards WHERE id=?", (d["card_id"],)).fetchone()
            if row:
                card = json.loads(row[0])
        return present_event(data, names, robots, card)

    def searchable(data):
        event = present(data)
        return event["title"] + " " + event["detail"]["reason"]

    def event(row):
        data = dict(row)
        data["detail"] = json.loads(data.pop("detail_json"))
        return present(data)

    @api.get("/missions")
    def missions():
        clauses, args = filters("m.created_at", [(f"m.{k}", k) for k in ("status", "type", "robot", "level")])
        sql = MISSIONS + " WHERE " + " AND ".join([CLINICAL, *clauses]) + " ORDER BY m.created_at DESC,m.rowid DESC"
        return jsonify(paged(g.history_db, sql, args, mission_row))

    @api.get("/missions/<mission_id>")
    def detail(mission_id):
        row = g.history_db.execute(MISSIONS + " WHERE m.id=? AND " + CLINICAL, (mission_id,)).fetchone()
        if row is None:
            return jsonify(message="기록에서 임무를 찾을 수 없습니다."), 404
        stops = g.history_db.execute("SELECT * FROM mission_stops WHERE mission_id=? ORDER BY seq", (mission_id,))
        events = g.history_db.execute("SELECT * FROM events WHERE mission_id=? ORDER BY ts,id", (mission_id,))
        return jsonify(mission=mission_row(row), stops=[dict(s) for s in stops], events=[event(e) for e in events])

    @api.get("/patients/recent")
    def patients():
        rows = g.history_db.execute(MISSIONS + """ WHERE m.type='PATIENT_TRANSPORT' AND p.id IS NOT NULL
                            ORDER BY m.created_at DESC,m.rowid DESC LIMIT 10""")
        return jsonify(items=[mission_row(row) for row in rows])

    def event_query():
        clauses, args = filters("ts", [("type", "type"), ("robot", "robot"), ("mission_id", "mission")])
        if request.args.get("group") == "decision":
            clauses.append("type IN ('ZONE_OCCUPIED_HOLD','PROXIMITY_STOP','PLAN_UPDATE','PLAN_WAIT','PLAN_YIELD_MOVE')")
        q = request.args.get("q", "").strip()
        if q:
            clauses.append("""instr(lower(type || ' ' || coalesce(robot,'') || ' ' || coalesce(mission_id,'')
                            || ' ' || coalesce("by",'') || ' ' || event_text(type,robot,detail_json)),lower(?))>0""")
            args.append(q)
        return "SELECT * FROM events" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY id DESC", args

    @api.get("/events")
    def events():
        sql, args = event_query()
        # Consecutive BED_MONITOR_WARN rows with the same text are one line with their count (D-51). The CSV
        # below keeps every row.
        grouped = f"""WITH f AS ({sql.removesuffix(" ORDER BY id DESC")}),
            k AS (SELECT f.*, CASE WHEN type='BED_MONITOR_WARN' THEN json_extract(detail_json,'$.reason') END AS wkey FROM f),
            p AS (SELECT k.*, LAG(wkey) OVER (ORDER BY id) AS pkey FROM k),
            q AS (SELECT p.*, SUM(CASE WHEN wkey IS NOT NULL AND wkey = pkey THEN 0 ELSE 1 END) OVER (ORDER BY id) AS grp FROM p),
            r AS (SELECT q.*, COUNT(*) OVER (PARTITION BY grp) AS repeat, MIN(ts) OVER (PARTITION BY grp) AS first_ts,
                  ROW_NUMBER() OVER (PARTITION BY grp ORDER BY id DESC) AS rn FROM q)
            SELECT * FROM r WHERE rn=1 ORDER BY id DESC"""

        def row(r):
            data = dict(r)
            repeat, first = data.pop("repeat"), data.pop("first_ts")
            for key in ("wkey", "pkey", "grp", "rn"):
                data.pop(key, None)
            item = event(data)
            if repeat > 1:
                item["detail"] = {**item["detail"], "repeat": repeat, "first_ts": first}
            return item
        return jsonify(paged(g.history_db, grouped, args, row))

    @api.get("/events.csv")
    def export():
        sql, args = event_query()
        output = StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["시각", "종류", "로봇", "임무", "누가", "문장"])
        for row in g.history_db.execute(sql + " LIMIT 10000", args):
            e = event(row)
            values = [datetime.fromtimestamp(e["ts"]).isoformat(sep=" "), e["type"], e["robot"],
                      e["mission_id"], e["by"], e["detail"].get("reason")]
            writer.writerow(["'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v for v in values])
        return Response("\ufeff" + output.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="fleet-events.csv"'})

    @api.get("/cards")
    def cards():
        clauses, args = filters("opened_at")
        sql = "SELECT * FROM cards" + (" WHERE " + " AND ".join(clauses) if clauses else "")
        def card_row(row):
            card = dict(row)
            payload = json.loads(card.pop("payload_json") or "{}")
            card["choice_label"] = next((o["label"] for o in payload.get("options", []) if o["id"] == card["choice"]), None)
            for key in ("choice_label", "title", "reason"):
                if card.get(key):
                    card[key] = legacy_names(card[key], robots, names)
            return card
        return jsonify(paged(g.history_db, sql + " ORDER BY opened_at DESC,rowid DESC", args, card_row))

    @api.get("/kpi")
    def kpi():
        bounds = period()
        where = "".join(" AND ts" + op + "?" for op, _ in bounds)
        args = [stamp for _, stamp in bounds]
        # The fleet's own counting (core.store.Metrics): a wait at a stop line or before a start counts once per
        # leg, every PAUSE counts.
        history = Metrics()
        rows = g.history_db.execute("""SELECT * FROM events WHERE type IN ('RESOURCE_REQUEST','LEG_START',
            'MISSION_CANCELED','MISSION_DONE','PLAN_WAIT','ZONE_OCCUPIED_HOLD','PAUSE')""" + where + " ORDER BY id", args)
        for row in rows:
            history.observe(row["type"], row["ts"], row["robot"], row["mission_id"], json.loads(row["detail_json"]))
        return jsonify(**history.kpi())

    return api
