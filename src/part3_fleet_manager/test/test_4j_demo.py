"""4J demo in five acts (BRIEF 9 #61) with the PLAN traffic judgement, on the kinematic mock and the default
fleet.yaml. Up to the 4O 정리 this was a scripted LEGACY run (act 2 was LEGACY's opposite-direction corridor wait).

1 three requests at once   2 A waits at its stop line for B   3 bed exit pauses only A
4 B meets a medical box    5 the queued call starts, both robots go home
"""
import pytest

from kinematic_mock import KinematicSim

A, B = "robot_a", "robot_b"


def _until(sim, check, limit, *, auto_confirm=False):
    end = sim.now + limit
    while not check():
        assert sim.now < end, f"{sim.now:.1f}초: 조건 시간 초과"
        if auto_confirm:
            sim.confirm_waiting()
        sim.step(0.2)


def _of(sim, kind, robot=None, since=0.0):
    return [e for e in sim.all_events if e["type"] == kind and e["ts"] >= since and (robot is None or e["robot"] == robot)]


@pytest.fixture
def sim(cfg):
    s = KinematicSim(cfg, bed_feed=True)
    yield s
    s.close()


def test_61_demo_five_acts(sim):
    core = sim.core
    # --- 1막: C→A 환자 이송, F→E→B 약품, A 구역 호출 동시 접수 ------------------------------------
    sim.submit_patient("C", "A", level=2, ref="p")
    sim.submit_med(("F", "E", "B"), level=3, ref="m")
    sim.submit_call("A", level=1, ref="call")
    sim.step(0.2)
    assert len(_of(sim, "MISSION_CREATED")) == 3
    rows = {m["mission_id"]: m for m in core.snapshot(sim.now)["missions"]}
    assert rows[sim.ids["p"]]["robot"] == A and rows[sim.ids["m"]]["robot"] == B
    assert rows[sim.ids["call"]]["status"] == "QUEUED" and rows[sim.ids["call"]]["wait_reason"] == "담당 로봇 대기"
    # B already stands in F: it arrives there without a MOVE.
    _until(sim, lambda: core.mission.robots[B].state == "WAIT_CONFIRM", 3)
    assert not [c for _, r, c in sim.all_commands if r == B and c.startswith("MOVE")]
    sim.confirm(B)

    # --- 2막: 두 로봇을 함께 계획: A(Level 2)는 교차로 정지선에서 B(Level 3)가 지나가길 기다림 ----------
    act2 = sim.now
    _until(sim, lambda: A in core.resources.held, 15)
    wait, = _of(sim, "PLAN_WAIT", A, act2)
    assert wait["detail"]["reason"] == "교차로: 약품 운송 로봇이 지나간 뒤" and wait["detail"]["peer"] == B
    assert sim.robots[A].state in ("STOPPING_PAUSE", "PAUSED")
    _until(sim, lambda: sim.robots[A].state == "MOVING" and sim.robots[A].pose[1] < -0.9, 30)
    assert _of(sim, "PLAN_UPDATE", since=act2)

    # --- 3막: 침상 이탈로 A만 정지 → 운영자 재개 --------------------------------------------------
    act3 = sim.now
    sim.bed_value = True
    _until(sim, lambda: "BED_EXIT" in core.safety.causes[A], 3)
    assert not core.safety.causes[B]
    assert _of(sim, "BED_EXIT", since=act3)
    assert [c for t, r, c in sim.all_commands if t >= act3 and c == "PAUSE"] and \
        all(r == A for t, r, c in sim.all_commands if t >= act3 and c == "PAUSE")
    sim.bed_value = False
    sim.run_until(sim.now + 3.0, auto_confirm=False)
    assert core.visible_state(A, core.mission.robots[A]) == "SAFE_PAUSE"      # never resumes by itself
    resumed = sim.now
    sim.resume(A)
    _until(sim, lambda: any(t >= resumed and r == A and c == "RESUME" for t, r, c in sim.all_commands), 2)

    # --- 4막: B가 복도에서 상자 → Nav2 포기 → OBSTACLE → medical_box → 막힘 카드 → 치움 ----------------
    _until(sim, lambda: core.mission.robots[A].zone == "C" and core.mission.robots[A].state == "WAIT_CONFIRM", 60)
    sim.confirm(A)
    _until(sim, lambda: core.mission.robots[B].zone == "E" and core.mission.robots[B].state == "WAIT_CONFIRM", 60)
    sim.confirm(B)                                         # B: E -> B, through the crossroad
    _until(sim, lambda: sim.robots[B].state == "MOVING" and core.config.corridor.region_of(sim.robots[B].pose)
           == "CORRIDOR", 30, auto_confirm=True)
    act4 = sim.now
    assert sim.obstacle(B)
    _until(sim, lambda: _of(sim, "OBSTACLE_REPORT", B, act4), 10, auto_confirm=True)
    sim.obstacle_verdict(B, "medical_box")
    _until(sim, lambda: [c for c in core.decisions.cards.values() if c["kind"] == "BLOCKED" and B in c["robots"]], 3,
           auto_confirm=True)
    box, = [c for c in core.decisions.cards.values() if c["kind"] == "BLOCKED" and B in c["robots"]]
    assert box["reason"].startswith("의료용 상자가 길을 막고 있음")
    assert [o["label"] for o in box["options"]] == ["치웠음, 다시 출발", "되돌아가기", "보류"]
    assert core.resources.snapshot()["CORRIDOR"]["blocked"]
    sim.robots[B].blocked_since = None                    # the box is taken away
    sim.choose(box["card_id"], "retry")
    _until(sim, lambda: core.mission.robots[B].zone == "B", 60, auto_confirm=True)
    assert _of(sim, "UNBLOCKED", B, act4)

    # --- 5막: 대기 호출 자동 시작 → 두 로봇 홈 복귀 --------------------------------------------------
    _until(sim, lambda: next(m for m in core.snapshot(sim.now)["missions"] if m["mission_id"] == sim.ids["call"])
           ["status"] == "ACTIVE", 60, auto_confirm=True)
    _until(sim, lambda: all(u.job is None and not u.queue for u in core.mission.robots.values()), 240, auto_confirm=True)
    assert core.mission.robots[A].zone == "D" and core.mission.robots[B].zone == "F"
    assert {e["detail"]["mission_type"] for e in _of(sim, "MISSION_CREATED")} == {"PATIENT_TRANSPORT", "MED_ROUND", "CALL"}
    assert len(_of(sim, "MISSION_DONE")) >= 3
    assert not _of(sim, "PROXIMITY_STOP")
