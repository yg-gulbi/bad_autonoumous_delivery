"""Stage 4O PART 01 tests: RESOURCE_PREEMPT history filter, segment labels, clear commands, static checks."""
from pathlib import Path

from part1_ui_dashboard import ui_api
from part1_ui_dashboard.web import Feed, create_app
from part3_fleet_manager.core.config import load_config
from part3_fleet_manager.core.segments import SegmentMap
from part3_fleet_manager.core.store import Store

PACKAGE = Path(__file__).resolve().parents[1]
PART3_CFG = Path(__file__).resolve().parents[2] / "part3_fleet_manager/config/fleet.yaml"


def test_resource_preempt_in_decision_filter_and_present_event(tmp_path):
    cfg = load_config(PART3_CFG)
    seg_map = SegmentMap(cfg)
    seg_labels = {k: v.label for k, v in seg_map.segments.items()}
    robot_labels = {k: v.label for k, v in cfg.robots.items()}

    ev_preempt = {
        "ts": 100.0,
        "type": "RESOURCE_PREEMPT",
        "robot": "robot_b",
        "mission_id": "M-0002",
        "detail": {
            "reason": "응급도 우선: 약품 운송 로봇(Level 3)가 교차로 먼저 → 침상형 이송 로봇 정지선 대기",
            "resources": ["CROSSROAD", "CORRIDOR_TOP", "D1"],
            "segment": "CROSSROAD",
            "preempted": "robot_a",
            "rule": "PREEMPT",
            "distance_m": 1.15,
            "contenders": [{"robot": "robot_b", "level": 3, "requested_at": 99.5, "type": "MED_ROUND"}],
        },
    }
    presented = ui_api.present_event(ev_preempt, cfg.zone_names, robot_labels, segment_labels=seg_labels)
    assert presented["title"] == "약품 운송 로봇 교차로 선점"
    assert "교차로" in presented["detail"]["reason"]

    # Legacy resource labels remain unchanged.
    assert ui_api.resource_label("CORRIDOR", cfg.zone_names, seg_labels) == "복도"
    assert ui_api.resource_label("ROOM_B", cfg.zone_names, seg_labels) == f"{cfg.zone_names['B']} 출입"
    assert ui_api.resource_label("DOOR_B", cfg.zone_names, seg_labels) == f"{cfg.zone_names['B']} 문"
    assert ui_api.resource_label("CORRIDOR_TOP", cfg.zone_names, seg_labels) == "복도 위쪽"
    assert ui_api.resource_label("A2", cfg.zone_names, seg_labels) == f"{cfg.zone_names['A']} 2번 자리"

    db_path = tmp_path / "history.db"
    store = Store(db_path)
    try:
        store.save([], [
            ev_preempt,
            {"ts": 101.0, "type": "RESOURCE_ENTER", "robot": "robot_b", "mission_id": "M-0002",
             "detail": {"reason": "약품 운송 로봇 · 교차로 진입 확인", "resources": ["CROSSROAD"]}},
        ])
        client = create_app(
            Feed(), {"names": cfg.zone_names, "robot_names": robot_labels, "segment_labels": seg_labels},
            None, None, db_path=db_path,
        ).test_client()
        resp = client.get("/api/events", query_string={"group": "decision"})
        assert resp.status_code == 200
        items = resp.get_json()["items"]
        assert [i["type"] for i in items] == ["RESOURCE_PREEMPT"]
        assert items[0]["title"] == "약품 운송 로봇 교차로 선점"
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


def test_static_assets_include_4o_segment_and_preempt_hooks():
    static = PACKAGE / "part1_ui_dashboard/static"
    assert "RESOURCE_PREEMPT" in (static / "pages/log.js").read_text(encoding="utf-8")
    assert "취소됨 · 복도를 빠져나온 뒤 정지" in (static / "app.js").read_text(encoding="utf-8")
    assert "CORRIDOR_TOP" in (static / "map.js").read_text(encoding="utf-8")
    assert "stop_line_waits" in (static / "pages/dashboard.js").read_text(encoding="utf-8")
