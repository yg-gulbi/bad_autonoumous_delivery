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
    assert "CORRIDOR_TOP" in map_js and "traffic.plans" in map_js
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
    # The Level change button only in PLAN (LEGACY refuses SET_LEVEL).
    assert 'command("SET_LEVEL"' in missions and 'S?.traffic?.mode === "PLAN"' in missions
