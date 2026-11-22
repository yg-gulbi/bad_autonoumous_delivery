"""4J obstacle report mode: PART 04 photo on a Nav2 abort, PART 06 verdict (BRIEF 5.5, D-31). Fake clock."""
from dataclasses import replace
import json

import pytest

from fleet_simulation import Simulation

A, B = "robot_a", "robot_b"
IN_CORRIDOR = (-1.55, -1.7)


@pytest.fixture
def sim(cfg, tmp_path):
    s = Simulation(replace(cfg, db_path=str(tmp_path / "bad.db")), slots=True)
    yield s
    s.core.store.close()


def commands(out):
    return {c.robot: c.command for c in out.commands}


def types(out):
    return [e["type"] for e in out.events]


def verdict(label=None, *, status="ok"):
    if status != "ok":
        return json.dumps({"status": status, "obstacle_present": None, "detections": []})
    return json.dumps({"status": "ok", "obstacle_present": label is not None,
                       "header": {"stamp": {"sec": 1, "nanosec": 0}, "frame_id": "oakd_rgb_camera_optical_frame"},
                       "detections": [] if label is None else [
                           {"label": label, "confidence": 0.93, "bbox": [120, 80, 300, 260]}]})


def drive_into_corridor(sim):
    """A goes D -> C (patient pickup) and is MOVING inside the corridor at t=2."""
    sim.patient(pickup="C", dest="A")
    assert commands(sim.tick(0))[A].startswith("MOVE C2 2")   # the plan: C2, in the first tick
    sim.tick(1)
    sim.event(A, "MISSION_ACCEPTED")
    sim.frames[A]["pose"] = sim.pose(IN_CORRIDOR)
    sim.tick(2)
    assert sim.state(A) == "MOVING"


def abort(sim, t, *, obstacle=True, lidar=0.42, mission_id=None):
    sim.event(A, "MISSION_FAILED", reason="Nav2: ABORTED")
    if obstacle:
        report = dict(destination="C", robot_pose={"x": -1.55, "y": -1.7, "yaw_deg": -92.0},
                      captured=["depth", "rgb", "scan"], image_topic="/robot3/hospital_nav/obstacle_image",
                      depth_m=0.45, lidar_m=lidar, bearing_deg=2.0, map_x=-1.56, map_y=-2.12)
        if mission_id:
            report["mission_id"] = mission_id
        sim.event(A, "OBSTACLE", **report)
    return sim.tick(t)


def test_57_medical_box_blocks_corridor_with_box_card_until_retry(sim):
    drive_into_corridor(sim)
    out = abort(sim, 3)
    assert sim.state(A) == "FAULT" and not out.cards  # NAV_FAILED card waits for the verdict
    assert {"NAV_FAILED", "FAULT", "OBSTACLE_REPORT"} <= set(types(out))
    report, = [e for e in out.events if e["type"] == "OBSTACLE_REPORT"]
    assert report["detail"]["paired"] and report["detail"]["lidar_m"] == 0.42
    assert report["detail"]["reason"] == "장애물 앞에서 주행 포기 · 사진 수신, 로봇 앞 약 0.4 m"
    assert out.state is None or out.state["robots"][A]["obstacle_pending"]
    assert sim.core.snapshot(3)["robots"][A]["obstacle_pending"]
    sim.core.enqueue("obstacle_result", verdict("medical_box"), 3.8, robot=A)
    out = sim.tick(4)
    card, = out.cards
    assert card["kind"] == "BLOCKED" and card["reason"] == "의료용 상자가 길을 막고 있음 (로봇 앞 약 0.4 m)"
    assert [o["label"] for o in card["options"]] == ["치웠음, 다시 출발", "되돌아가기", "보류"]
    assert card["obstacle"]["label_ko"] == "의료용 상자" and card["obstacle"]["confidence"] == 0.93
    assert card["obstacle"]["photo"] and card["obstacle"]["reported_at"] == 3
    result, = [e for e in out.events if e["type"] == "OBSTACLE_RESULT"]
    assert result["detail"]["applied"] and result["detail"]["kind"] == "OBJECT"
    corridor = sim.core.resources.snapshot()["CORRIDOR"]
    assert corridor["blocked"] and corridor["blocked_robots"] == [A] and "BLOCKED" in types(out)
    robot = sim.core.snapshot(4)["robots"][A]
    assert robot["obstacle"]["kind"] == "OBJECT" and robot["obstacle"]["text"] == card["reason"]
    assert not robot["obstacle_pending"]
    # Another robot's new corridor grant is held while the box is reported.
    sim.call("call", "A")
    for t in (5, 6, 7):
        assert B not in commands(sim.tick(t))
    assert B in sim.core.resources.held          # held still by the plan while the corridor is blocked
    sim.choose(card, "retry")
    out = sim.tick(8)
    assert not sim.core.resources.snapshot()["CORRIDOR"]["blocked"] and "UNBLOCKED" in types(out)
    sent = commands(out)
    sent.update(commands(sim.tick(8.5)))
    assert sent[A].startswith("MOVE C2 2")


def test_57_box_card_return_unblocks_when_the_robot_moves_again(sim):
    drive_into_corridor(sim)
    abort(sim, 3)
    sim.core.enqueue("obstacle_result", verdict("medical_box"), 3.5, robot=A)
    card, = sim.tick(4).cards
    sim.choose(card, "return")
    t, sent = 4.5, {}
    while A not in sent and t < 40:
        sent = commands(sim.tick(t))
        assert sim.core.resources.snapshot()["CORRIDOR"]["blocked"]
        t += .5
    assert sent[A].startswith("MOVE D")  # back to the previous zone
    sim.event(A, "MISSION_ACCEPTED")
    sim.tick(t)
    sim.frames[A]["pose"] = sim.pose((-1.5, -1.2))
    out = sim.tick(t + .5)
    assert "UNBLOCKED" in types(out) and not sim.core.resources.snapshot()["CORRIDOR"]["blocked"]


def test_58_none_retries_once_after_3s_then_card_only(sim):
    # 4K turned the automatic retry off in fleet.yaml (D-31). This keeps the retry_if_clear: true rule tested.
    sim.core.reports.policy = replace(sim.core.reports.policy, retry_if_clear=True)
    drive_into_corridor(sim)
    abort(sim, 3)
    sim.core.enqueue("obstacle_result", verdict(None), 3.5, robot=A)
    out = sim.tick(4)
    assert not out.cards and not out.commands
    result, = [e for e in out.events if e["type"] == "OBSTACLE_RESULT"]
    assert result["detail"]["kind"] == "NONE" and result["detail"]["reason"] == "의료용 상자는 보이지 않음"
    assert sim.core.snapshot(4)["robots"][A]["obstacle"]["text"] == "의료용 상자는 보이지 않음 · 3초 뒤 자동 재출발"
    assert not sim.core.resources.snapshot()["CORRIDOR"]["blocked"]
    assert not sim.tick(6.9).commands
    out = sim.tick(7)
    retry, = [e for e in out.events if e["type"] == "OBSTACLE_RETRY"]
    assert retry["detail"]["reason"] == "의료용 상자는 보이지 않음 → 같은 구간 자동 재출발 (1회)"
    sent = commands(out)
    sent.update(commands(sim.tick(7.5)))
    assert sent[A].startswith("MOVE C2 2") and sim.state(A) == "SENDING"
    # The second failure on the same leg gets a card, no further automatic retry.
    sim.event(A, "MISSION_ACCEPTED")
    sim.tick(8)
    abort(sim, 9)
    sim.core.enqueue("obstacle_result", verdict(None), 9.5, robot=A)
    card, = sim.tick(10).cards
    assert card["kind"] == "BLOCKED" and card["reason"].startswith("의료용 상자는 보이지 않음 · 자동 재출발 뒤 다시 멈춤")
    assert card["options"][0]["label"] == "확인함, 다시 출발"
    for t in (13, 20):
        out = sim.tick(t)
        assert not out.commands and "OBSTACLE_RETRY" not in types(out)


def test_58_no_result_or_unknown_is_a_judgement_failure_card_without_retry(sim):
    drive_into_corridor(sim)
    abort(sim, 3)
    assert not sim.tick(8.9).cards  # 6 s after OBSTACLE (t=3)
    out = sim.tick(9.1)
    card, = out.cards
    assert card["kind"] == "BLOCKED" and card["reason"] == "장애물 판별 불가 — 로봇이 찍은 사진 확인"
    assert card["obstacle"]["kind"] == "UNKNOWN" and card["obstacle"]["label"] is None
    assert not sim.core.resources.snapshot()["CORRIDOR"]["blocked"]
    for t in (12, 15):
        assert not sim.tick(t).commands


def test_58_processing_error_is_unknown(sim):
    drive_into_corridor(sim)
    abort(sim, 3)
    sim.core.enqueue("obstacle_result", verdict(status="processing_error"), 3.4, robot=A)
    card, = sim.tick(3.5).cards
    assert card["reason"] == "장애물 판별 불가 — 로봇이 찍은 사진 확인"


def test_59_late_result_is_only_recorded(sim):
    drive_into_corridor(sim)
    abort(sim, 3)
    card, = sim.tick(9.1).cards
    sim.core.enqueue("obstacle_result", verdict("medical_box"), 9.3, robot=A)
    out = sim.tick(9.5)
    late, = [e for e in out.events if e["type"] == "OBSTACLE_RESULT"]
    assert not late["detail"]["applied"] and late["detail"]["reason"] == "현재 판단에서 제외한 장애물 판별 결과"
    assert not out.cards and not sim.core.resources.snapshot()["CORRIDOR"]["blocked"]
    assert card["card_id"] in sim.core.decisions.cards


def test_59_result_before_the_status_message_is_kept_for_the_report(sim):
    drive_into_corridor(sim)
    sim.event(A, "MISSION_FAILED", reason="Nav2: ABORTED")
    sim.core.enqueue("obstacle_result", verdict("medical_box"), 3, robot=A)  # photo beat the status
    out = sim.tick(3)
    assert not out.cards and not types(out).count("OBSTACLE_RESULT")
    sim.event(A, "OBSTACLE", lidar_m=0.4, image_topic="/robot3/hospital_nav/obstacle_image")
    card, = sim.tick(3.2).cards
    assert card["reason"].startswith("의료용 상자가 길을 막고 있음")


def test_60_no_obstacle_report_falls_back_to_nav_failed(sim):
    drive_into_corridor(sim)
    out = abort(sim, 3, obstacle=False)
    assert sim.state(A) == "FAULT" and not out.cards
    assert not sim.tick(6.9).cards
    card, = sim.tick(7).cards  # report_wait_s = 4
    assert card["kind"] == "NAV_FAILED" and [o["id"] for o in card["options"]] == ["retry", "return", "cancel"]


def test_60_other_failures_open_nav_failed_at_once(sim):
    drive_into_corridor(sim)
    sim.event(A, "MISSION_FAILED", reason="Nav2: FAILED")
    card, = sim.tick(3).cards
    assert card["kind"] == "NAV_FAILED"


def test_60_obstacle_for_another_mission_is_only_recorded(sim):
    drive_into_corridor(sim)
    out = abort(sim, 3, mission_id="0123456789ab")
    report, = [e for e in out.events if e["type"] == "OBSTACLE_REPORT"]
    assert not report["detail"]["paired"] and report["detail"]["part4_mission_id"] == "0123456789ab"
    sim.core.enqueue("obstacle_result", verdict("medical_box"), 3.5, robot=A)
    out = sim.tick(4)
    assert not out.cards and not sim.core.resources.snapshot()["CORRIDOR"]["blocked"]
    assert sim.tick(7).cards[0]["kind"] == "NAV_FAILED"


def test_60_operator_retry_before_the_verdict_drops_it(sim):
    drive_into_corridor(sim)
    abort(sim, 3)
    sim.cmd("RETRY", robot=A)
    out = sim.tick(3.5)
    assert commands(out)[A].startswith("MOVE C2 2")
    sim.core.enqueue("obstacle_result", verdict("medical_box"), 3.8, robot=A)
    out = sim.tick(4)
    assert not out.cards and not sim.core.resources.snapshot()["CORRIDOR"]["blocked"]
    assert not next(e for e in out.events if e["type"] == "OBSTACLE_RESULT")["detail"]["applied"]


def test_60_report_mode_never_requests_inspection(sim):
    drive_into_corridor(sim)
    for t in range(3, 40):
        out = sim.tick(t)
        assert not out.inspections and "OBSTACLE_INSPECT" not in types(out)
    assert sim.core.resources.snapshot()["CORRIDOR"]["blocked"]  # the 30 s stall rule still applies
