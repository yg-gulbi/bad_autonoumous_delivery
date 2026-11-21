"""Stage 4O PART 01 tests: PLAN events in the decision filter, segment labels, clear commands, static checks."""
from pathlib import Path

from part1_ui_dashboard import ui_api
from part1_ui_dashboard.web import Feed, create_app
from part3_fleet_manager.core.config import load_config
from part3_fleet_manager.core.segments import SegmentMap
from part3_fleet_manager.core.store import Store

PACKAGE = Path(__file__).resolve().parents[1]
PART3_CFG = Path(__file__).resolve().parents[2] / "part3_fleet_manager/config/fleet.yaml"


def test_plan_events_in_decision_filter_and_present_event(tmp_path):
    cfg = load_config(PART3_CFG)
    seg_map = SegmentMap(cfg)
    seg_labels = {k: v.label for k, v in seg_map.segments.items()}
    robot_labels = {k: v.label for k, v in cfg.robots.items()}

    wait = {"ts": 100.0, "type": "PLAN_WAIT", "robot": "robot_a", "mission_id": "M-0002",
            "detail": {"reason": "교차로: 약품 운송 로봇이 지나간 뒤", "peer": "robot_b", "point": "CROSSROAD"}}
    aside = {"ts": 101.0, "type": "PLAN_YIELD_MOVE", "robot": "robot_a", "mission_id": "M-0002",
             "detail": {"reason": "침상형 이송 로봇 → 102호 병실(B) 2번 자리 · 길 비켜 주는 중", "target": "B2"}}
    update = {"ts": 99.0, "type": "PLAN_UPDATE", "robot": None, "mission_id": None,
              "detail": {"reason": "다시 계획 · 새 구간 요청 · 12 ms", "plan_ms": 12.0, "cost": 30.0,
                         "method": "JOINT", "fallback": False}}
    assert ui_api.present_event(wait, cfg.zone_names, robot_labels, segment_labels=seg_labels)["title"] == \
        "침상형 이송 로봇 계획 순서 대기"
    assert ui_api.present_event(aside, cfg.zone_names, robot_labels, segment_labels=seg_labels)["title"] == \
        "침상형 이송 로봇 길 비켜 주는 중"
    assert ui_api.present_event(update, cfg.zone_names, robot_labels, segment_labels=seg_labels)["title"] == \
        "두 로봇 다시 계획"

    # Segment labels stay as before.
    assert ui_api.resource_label("CORRIDOR", cfg.zone_names, seg_labels) == "복도"
    assert ui_api.resource_label("ROOM_B", cfg.zone_names, seg_labels) == f"{cfg.zone_names['B']} 출입"
    assert ui_api.resource_label("DOOR_B", cfg.zone_names, seg_labels) == f"{cfg.zone_names['B']} 문"
    assert ui_api.resource_label("CORRIDOR_TOP", cfg.zone_names, seg_labels) == "복도 위쪽"
    assert ui_api.resource_label("A2", cfg.zone_names, seg_labels) == f"{cfg.zone_names['A']} 2번 자리"

    db_path = tmp_path / "history.db"
    store = Store(db_path)
    try:
        store.save([], [update, wait, aside,
                        {"ts": 102.0, "type": "RESOURCE_ENTER", "robot": "robot_a", "mission_id": "M-0002",
                         "detail": {"reason": "침상형 이송 로봇 · 교차로 진입 확인", "resources": ["CROSSROAD"]}}])
        client = create_app(
            Feed(), {"names": cfg.zone_names, "robot_names": robot_labels, "segment_labels": seg_labels},
            None, None, db_path=db_path,
        ).test_client()
        resp = client.get("/api/events", query_string={"group": "decision"})
        assert resp.status_code == 200
        assert sorted(i["type"] for i in resp.get_json()["items"]) == ["PLAN_UPDATE", "PLAN_WAIT", "PLAN_YIELD_MOVE"]
    finally:
        store.close()


def test_clear_command_accepts_segment_names_and_rejects_invalid():
    cfg = load_config(PART3_CFG)
    seg_labels = {k: v.label for k, v in SegmentMap(cfg).segments.items()}
    robot_labels = {k: v.label for k, v in cfg.robots.items()}
    sent = []
    client = create_app(
        Feed(),
        {"names": cfg.zone_names, "robot_names": robot_labels, "segment_labels": seg_labels},
        lambda topic, payload: sent.append((topic, payload)),
        None,
    ).test_client()

    ok = client.post("/api/command", json={"cmd": "RESOURCE_CLEAR_CONFIRM", "resource": "CORRIDOR_TOP", "operator": "간호사"})
    assert ok.status_code == 202
    assert len(sent) == 1

    bad = client.post("/api/command", json={"cmd": "RESOURCE_CLEAR_CONFIRM", "resource": "TOP", "operator": "간호사"})
    assert bad.status_code == 400


def test_static_assets_include_4o_plan_hooks():
    static = PACKAGE / "part1_ui_dashboard/static"
    log = (static / "pages/log.js").read_text(encoding="utf-8")
    assert all(kind in log for kind in ("PLAN_UPDATE", "PLAN_WAIT", "PLAN_YIELD_MOVE"))
    app = (static / "app.js").read_text(encoding="utf-8")
    assert "길 비켜 주는 중" in app and "canceling_exit" not in app and "SEGMENTS" not in app
    map_js = (static / "map.js").read_text(encoding="utf-8")
    assert "CORRIDOR_TOP" in map_js and "traffic?.plans" in map_js
    dash = (static / "pages/dashboard.js").read_text(encoding="utf-8")
    assert "plan.wait" in dash and "SEGMENTS" not in dash


def test_quick_send_level_and_level_change_commands():
    """4O follow-up: GO_ZONE takes an optional Level (none = mission.manual_level); SET_LEVEL needs one."""
    go = ui_api.command({"cmd": "GO_ZONE", "robot": "robot_a", "zone": "C", "level": 3, "operator": "간호사"})
    assert go["level"] == 3 and go["zone"] == "C"
    plain = ui_api.command({"cmd": "GO_ZONE", "robot": "robot_a", "zone": "C", "operator": "간호사"})
    assert "level" not in plain
    change = ui_api.command({"cmd": "SET_LEVEL", "mission_id": "M-0007", "level": 2, "operator": "간호사"})
    assert change["mission_id"] == "M-0007" and change["level"] == 2
    for bad in ({"cmd": "SET_LEVEL", "mission_id": "M-0007", "operator": "간호사"},
                {"cmd": "GO_ZONE", "robot": "robot_a", "zone": "C", "level": 5, "operator": "간호사"}):
        try:
            ui_api.command(bad)
        except ValueError as exc:
            assert "응급도" in exc.user_message()
        else:
            raise AssertionError(bad)

    sent = []
    client = create_app(Feed(), {}, lambda topic, payload: sent.append((topic, payload)), None).test_client()
    reply = client.post("/api/command", json={"cmd": "SET_LEVEL", "mission_id": "M-0007", "level": 3, "operator": "간호사"})
    assert reply.status_code == 202
    (topic, text), = sent
    assert topic == "/ui/operator_cmd" and '"level":3' in text and '"cmd":"SET_LEVEL"' in text


def test_level_change_event_title():
    cfg = load_config(PART3_CFG)
    robot_labels = {k: v.label for k, v in cfg.robots.items()}
    event = {"ts": 10.0, "type": "LEVEL_CHANGE", "robot": "robot_b", "mission_id": "M-0007",
             "detail": {"reason": "Level 1 → 3", "level": 3, "previous": 1, "by": "간호사"}}
    shown = ui_api.present_event(event, cfg.zone_names, robot_labels)
    assert shown["title"] == "응급도 변경" and shown["detail"]["reason"] == "Level 1 → 3"


def test_static_assets_include_level_controls():
    static = PACKAGE / "part1_ui_dashboard/static"
    app = (static / "app.js").read_text(encoding="utf-8")
    assert "levelOptions" in app
    dash = (static / "pages/dashboard.js").read_text(encoding="utf-8")
    assert "data-go-level" in dash and "level: goLevels[id] || 1" in dash      # quick send, Level 1 by default
    map_js = (static / "map.js").read_text(encoding="utf-8")
    assert "map-go-level" in map_js
    missions = (static / "pages/missions.js").read_text(encoding="utf-8")
    # The Level change button for a waiting or running mission (the plan is made again with it).
    assert 'command("SET_LEVEL"' in missions and "traffic?.mode" not in missions


def test_bed_monitor_switch_command():
    """D-51: the screen switch sends BED_MONITOR_SET {enabled}; enabled must be true or false."""
    import pytest
    from part3_fleet_manager.core import schemas
    for enabled in (True, False):
        cmd = ui_api.command({"cmd": "BED_MONITOR_SET", "enabled": enabled, "operator": "간호사"})
        assert cmd["cmd"] == "BED_MONITOR_SET" and cmd["enabled"] is enabled and cmd["by"] == "간호사"
    for bad in ({}, {"enabled": "false"}, {"enabled": 0}):
        with pytest.raises(schemas.SchemaError, match="enabled"):
            ui_api.command({"cmd": "BED_MONITOR_SET", "operator": "간호사", **bad})
    titles = {kind: ui_api.present_event({"ts": 1.0, "type": kind, "robot": None, "mission_id": None,
                                          "detail": {"reason": "", "by": "간호사"}}, {})["title"]
              for kind in ("BED_MONITOR_ENABLED", "BED_MONITOR_DISABLED")}
    assert titles == {"BED_MONITOR_ENABLED": "침상 감시 켬", "BED_MONITOR_DISABLED": "침상 감시 끔"}


def test_static_assets_include_the_bed_monitor_switch():
    index = (PACKAGE / "part1_ui_dashboard/static/index.html").read_text(encoding="utf-8")
    app = (PACKAGE / "part1_ui_dashboard/static/app.js").read_text(encoding="utf-8")
    assert 'id="bed-switch"' in index and 'role="switch"' in index
    assert 'command("BED_MONITOR_SET", { enabled: false }' in app and 'command("BED_MONITOR_SET", { enabled: true }' in app
    assert "침상 감시 꺼짐" in app and "BED_MONITOR_DISABLED" in app


def _warn(ts, reason="침상 감시 불안정"):
    return {"v": 1, "ts": ts, "type": "BED_MONITOR_WARN", "robot": None, "mission_id": None,
            "detail": {"reason": reason, "candidates": []}}


def test_live_record_keeps_one_line_per_webcam_warning():
    """11/21: the same warning 1,328 times. The live record (SSE snapshot) keeps one line with its count."""
    feed = Feed(limit=5)
    other = {"v": 1, "ts": 1.5, "type": "PLAN_UPDATE", "robot": None, "mission_id": None, "detail": {"reason": "계획"}}
    feed.receive("event", _warn(1.0))
    feed.receive("event", other)
    for ts in range(2, 40):
        feed.receive("event", _warn(float(ts)))
    feed.receive("event", _warn(40.0, "침상 감시 신호가 지연되고 있습니다."))
    kinds = [(e["type"], e["detail"].get("repeat", 1)) for e in feed.events]
    assert kinds == [("PLAN_UPDATE", 1), ("BED_MONITOR_WARN", 39), ("BED_MONITOR_WARN", 1)]
    assert feed.events[1]["detail"]["first_ts"] == 1.0 and feed.events[1]["ts"] == 39.0
    assert sum(1 for _, kind, _ in feed.recent if kind == "event") == 41      # the stream still has every event


def test_log_groups_consecutive_webcam_warnings(tmp_path):
    cfg = load_config(PART3_CFG)
    db_path = tmp_path / "history.db"
    store = Store(db_path)
    plan = {"v": 1, "ts": 10.5, "type": "PLAN_UPDATE", "robot": None, "mission_id": None,
            "detail": {"reason": "다시 계획 · 시작 · 0 ms", "plan_ms": 0.0, "cost": 0.0, "method": "JOINT", "fallback": False}}
    try:
        store.save([], [*(_warn(float(t)) for t in range(1, 11)), plan, *(_warn(float(t)) for t in range(11, 14)),
                        _warn(14.0, "침상 감시 신호가 지연되고 있습니다.")])
        client = create_app(Feed(), {"names": cfg.zone_names}, None, None, db_path=db_path).test_client()
        items = client.get("/api/events").get_json()["items"]
        assert [(i["type"], i["detail"].get("repeat", 1), i["ts"]) for i in items] == [
            ("BED_MONITOR_WARN", 1, 14.0), ("BED_MONITOR_WARN", 3, 13.0), ("PLAN_UPDATE", 1, 10.5),
            ("BED_MONITOR_WARN", 10, 10.0)]
        assert items[1]["detail"]["first_ts"] == 11.0 and items[3]["detail"]["first_ts"] == 1.0
        # Filters still apply first; the CSV keeps every row.
        only = client.get("/api/events", query_string={"type": "BED_MONITOR_WARN"}).get_json()["items"]
        assert [i["detail"].get("repeat", 1) for i in only] == [1, 13]
        csv = client.get("/api/events.csv").get_data(as_text=True)
        assert csv.count("BED_MONITOR_WARN") == 14
    finally:
        store.close()
