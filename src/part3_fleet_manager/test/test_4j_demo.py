"""4J demo in five acts (BRIEF 9 #61), one fake-clock run with the default fleet.yaml.

1 three requests at once   2 opposite corridor wait   3 bed exit pauses only A
4 B meets a medical box in the corridor   5 queued call starts, both robots go home
"""
from dataclasses import replace
import json

import pytest

from fleet_simulation import Simulation

A, B = "robot_a", "robot_b"
CROSSROAD = (-1.5, -2.18)


class Demo:
    def __init__(self, sim):
        self.sim, self.t, self.bed = sim, 0.0, None  # None: webcam not started yet
        self.events, self.cards, self.sent = [], [], []

    def tick(self, dt=0.5):
        self.t = round(self.t + dt, 3)
        if self.bed is not None:
            for _ in range(6):  # PART 05 at about 12 Hz once the webcam is on (act 3)
                self.sim.pending.append(("/patient/bed_exit", bool(self.bed), None))
        out = self.sim.tick(self.t)
        self.events += out.events
        self.cards += out.cards
        self.sent += [(self.t, c.robot, c.command) for c in out.commands]
        return out

    def until(self, check, limit=15, dt=0.5):
        end = self.t + limit
        while not check():
            assert self.t < end, f"{self.t}초: 조건 시간 초과"
            self.tick(dt)

    def moved(self, robot):
        return [c for _, r, c in self.sent if r == robot and c.startswith("MOVE")]

    def wait_move(self, robot):
        count = len(self.moved(robot))
        self.until(lambda: len(self.moved(robot)) > count)
        return self.moved(robot)[-1]

    def status(self, robot, name, **fields):
        frame = self.sim.frames[robot]
        self.sim.pending.append(("status", json.dumps({"type": "STATUS", "event": name, "robot_id": frame["robot_id"],
                                                       "mission_id": frame["mission_id"], **fields}), robot))

    def at(self, robot, xy):
        self.sim.frames[robot]["pose"] = self.sim.pose(xy)

    def drive(self, robot, *points, via=False):
        """Accept the last MOVE, pass the given points (crossroad -> VIA_PASSED) and arrive."""
        self.sim.event(robot, "MISSION_ACCEPTED")
        frame = self.sim.frames[robot]
        self.status(robot, "NAVIGATING", destination=frame["destination"], via="교차로" if via else None)
        self.tick()
        for point in points:
            self.at(robot, point)
            if point == CROSSROAD:
                self.status(robot, "VIA_PASSED", destination=frame["destination"])
            self.tick()
        self.sim.event(robot, "ARRIVED")
        self.tick()

    def of(self, kind, robot=None, since=0.0):
        return [e for e in self.events if e["type"] == kind and e["ts"] >= since and (robot is None or e["robot"] == robot)]

    def state(self, robot):
        return self.sim.state(robot)

    def snapshot(self):
        return self.sim.core.snapshot(self.t)


@pytest.fixture(params=["LEGACY", "SEGMENTS"])
def demo(cfg, tmp_path, request):
    sc = replace(cfg, db_path=str(tmp_path / f"demo-{request.param}.db"),
                 traffic=replace(cfg.traffic, mode=request.param))
    sim = Simulation(sc, slots=True)
    yield Demo(sim)
    sim.core.store.close()


def test_61_demo_five_acts(demo):
    sim = demo.sim
    # --- 1막: C→A 환자 이송, F→E→B 약품, A 구역 호출 동시 접수 ------------------------------------
    sim.patient(pickup="C", dest="A", level=2)
    sim.med(stops=("F", "E", "B"), level=3)
    sim.call("call", "A", level=1)
    demo.tick(0)
    demo.bed = False  # PART 05 webcam on: normal
    assert len(demo.of("MISSION_CREATED")) == 3
    rows = {m["mission_id"]: m for m in demo.snapshot()["missions"]}
    assert rows[sim.ids["p"]]["robot"] == A and rows[sim.ids["m"]]["robot"] == B
    assert rows[sim.ids["call"]]["status"] == "QUEUED" and rows[sim.ids["call"]]["wait_reason"] == "담당 로봇 대기"
    assert demo.moved(B)[-1].startswith("MOVE F 3")  # B is already in F
    demo.drive(B)
    sim.confirm(B, "m")  # B: F → E next
    act2 = demo.t

    # --- 2막·3막: 반대 방향 교행·선점 + 침상 이탈로 A만 정지 → 운영자 재개 ------------------------
    assert demo.moved(A)[-1].startswith("MOVE C 2")
    if sim.cfg.traffic.mode == "LEGACY":
        demo.tick()
        hold = demo.of("RESOURCE_HOLD", B, act2)[-1]
        assert hold["detail"]["rule"] == "DIRECTION"
        assert hold["detail"]["reason"] == "침상형 이송 로봇 복도 반대 방향 통행 중 → 약품 운송 로봇 복도 밖 대기"
        assert demo.state(B) == "HOLD" and len(demo.moved(B)) == 1
        sim.event(A, "MISSION_ACCEPTED")
        demo.at(A, (-1.55, -1.7))
        demo.tick()

        # --- 3막: 침상 이탈로 A만 정지 → 운영자 재개 ----------------------------------------------
        act3 = demo.t + 0.5
        demo.bed = True
        out = demo.tick()
        assert demo.of("BED_EXIT", since=act3) and {c.robot: c.command for c in out.commands} == {A: "PAUSE"}
        assert demo.state(A) == "SAFE_PAUSE" and demo.state(B) == "HOLD"
        sim.event(A, "PAUSED")
        demo.tick()
        demo.bed = False
        demo.tick()
        demo.tick()
        assert demo.state(A) == "SAFE_PAUSE"  # never resumes by itself
        sim.cmd("RESUME_ROBOT", robot=A)
        out = demo.tick()
        assert {c.robot: c.command for c in out.commands} == {A: "RESUME"}
        sim.event(A, "RESUMED")
        demo.tick()
        demo.at(A, (-1.7, -3.9))
        demo.tick()
        sim.event(A, "ARRIVED")
        demo.tick()
        assert demo.state(A) == "WAIT_CONFIRM"
        assert demo.wait_move(B).startswith("MOVE E 3")  # A left the corridor: B goes up into E
        demo.drive(B, (-1.6, -3.3), CROSSROAD, (-1.0, -2.2), via=True)
        assert demo.state(B) == "WAIT_CONFIRM" and demo.snapshot()["robots"][B]["zone"] == "E"
    else:
        # SEGMENTS: A(L2)가 교차로에 들어가기 전에 B(L3)가 F→E 요청 → B가 교차로·아래쪽을 선점해 먼저 출발
        sim.event(A, "MISSION_ACCEPTED")
        demo.at(A, (-1.50, -0.70))  # CORRIDOR_TOP 안, 교차로 경계에서 >= 0.8 m
        assert demo.wait_move(B).startswith("MOVE E 3")
        assert demo.of("RESOURCE_PREEMPT", B, act2)
        # A는 정지선에서 정지, B는 E로 주행 시작; 침상 이탈 시 A만 SAFE_PAUSE
        demo.at(A, (-1.50, -0.90))
        demo.tick()
        sim.event(A, "PAUSED")
        act3 = demo.t + 0.5
        demo.bed = True
        demo.tick()
        assert demo.of("BED_EXIT", since=act3)
        assert demo.state(A) == "SAFE_PAUSE" and demo.state(B) != "SAFE_PAUSE"
        demo.drive(B, (-1.6, -3.3), CROSSROAD, (-1.0, -2.2), via=True)
        assert demo.state(B) == "WAIT_CONFIRM" and demo.snapshot()["robots"][B]["zone"] == "E"
        demo.bed = False
        demo.tick()
        demo.tick()
        assert demo.state(A) == "SAFE_PAUSE"
        sim.cmd("RESUME_ROBOT", robot=A)
        demo.until(lambda: any(r == A and c == "RESUME" for _, r, c in demo.sent))
        sim.event(A, "RESUMED")
        demo.at(A, (-1.7, -3.9))
        demo.tick()
        sim.event(A, "ARRIVED")
        demo.tick()
        assert demo.state(A) == "WAIT_CONFIRM"

    # --- 4막: B가 복도에서 상자 → 5초 뒤 포기 → OBSTACLE → medical_box → 복도 막힘 ------------------
    sim.confirm(B, "m")  # B: E → B (crossing the corridor, single file)
    sim.confirm(A, "p")  # A: C → A (up the corridor)
    act4 = demo.t
    assert demo.wait_move(B).startswith("MOVE B 3")
    a_moves = len(demo.moved(A))
    sim.event(B, "MISSION_ACCEPTED")
    demo.status(B, "NAVIGATING", destination="B", via="교차로")
    demo.at(B, (-1.2, -2.25))  # out of the E door, the box ahead
    for _ in range(10):        # Nav2 recovers, then gives up after about 5 s
        demo.tick()
    sim.event(B, "MISSION_FAILED", reason="Nav2: ABORTED")
    demo.status(B, "OBSTACLE", destination="B", lidar_m=0.42, depth_m=0.45, captured=["depth", "rgb", "scan"],
                image_topic="/robot2/hospital_nav/obstacle_image", robot_pose={"x": -1.2, "y": -2.25, "yaw_deg": 178.0})
    failed_at = demo.t + 0.5
    demo.tick()
    assert demo.state(B) == "FAULT" and demo.of("OBSTACLE_REPORT", B)
    assert not [c for c in demo.cards if c["created_at"] >= failed_at]  # NAV_FAILED waits for the verdict
    sim.core.enqueue("obstacle_result", json.dumps({"status": "ok", "obstacle_present": True, "detections": [
        {"label": "medical_box", "confidence": 0.93, "bbox": [100, 80, 300, 280]}]}), demo.t, robot=B)
    demo.tick()
    box, = [c for c in demo.cards if c["created_at"] >= failed_at]
    assert box["kind"] == "BLOCKED" and box["reason"] == "의료용 상자가 길을 막고 있음 (로봇 앞 약 0.4 m)"
    assert [o["label"] for o in box["options"]] == ["치웠음, 다시 출발", "되돌아가기", "보류"]
    corridor = demo.snapshot()["resources"]["CORRIDOR"]
    assert corridor["blocked"] and corridor["blocked_robots"] == [B]
    assert {e["detail"]["kind"] for e in demo.of("OBSTACLE_RESULT", B)} == {"OBJECT"}
    for _ in range(4):
        demo.tick()
        assert demo.state(A) == "HOLD" and len(demo.moved(A)) == a_moves  # A stays out of the corridor
    sim.choose(box, "retry")
    demo.tick()
    assert demo.of("UNBLOCKED", B, act4) and not demo.snapshot()["resources"]["CORRIDOR"]["blocked"]
    assert demo.wait_move(B).startswith("MOVE B 3")
    demo.drive(B, CROSSROAD, (-2.2, -2.0), via=True)
    assert demo.state(B) == "WAIT_CONFIRM" and demo.snapshot()["robots"][B]["zone"] == "B"
    assert demo.wait_move(A).startswith("MOVE A 2")  # A goes up once B has left the corridor
    demo.drive(A, (-1.7, -3.8), (-1.5, -1.0))
    act5 = demo.t

    # --- 5막: 대기 호출 자동 시작 → 두 로봇 홈 복귀 --------------------------------------------------
    sim.confirm(B, "m")
    demo.tick()
    assert demo.of("MISSION_DONE", B, act5)
    def call_row():
        return next(m for m in demo.snapshot()["missions"] if m["mission_id"] == sim.ids["call"])
    demo.until(lambda: call_row()["status"] == "ACTIVE", limit=2)  # the queued call replaces the home leg
    assert call_row()["robot"] == B
    assert demo.wait_move(B).startswith("MOVE A2 1")  # A waits for confirmation in A1
    sim.confirm(A, "p")
    demo.tick()
    assert not demo.moved(A)[-1].startswith("MOVE D")  # B is arriving within the A2 approach radius
    demo.drive(B, CROSSROAD, (-1.5, -0.8), via=True)
    arrived = demo.of("ARRIVE", B)[-1]
    assert arrived["detail"]["slot"] == 2 and arrived["detail"]["reason"] == "응급실(A) 2번 자리 도착"
    sim.confirm(B, "call")
    assert demo.wait_move(B).startswith("MOVE F 1")
    # B leaves the blocking second slot first. Drive the two home legs as their MOVEs are issued.
    sim.event(B, "MISSION_ACCEPTED")
    demo.tick()
    demo.at(B, (-1.5, -1.0))
    demo.until(lambda: demo.moved(A)[-1].startswith("MOVE D 1"))
    demo.drive(A, (-1.0, 0.5))
    demo.at(B, (-1.7, -4.3))
    demo.tick()
    sim.event(B, "ARRIVED")
    demo.tick()
    for robot, zone in ((A, "D"), (B, "F")):
        assert demo.state(robot) == "IDLE" and demo.snapshot()["robots"][robot]["zone"] == zone
    done = {m["mission_id"] for m in demo.snapshot()["missions"] if m["status"] == "DONE"}
    assert done == {sim.ids["p"], sim.ids["m"], sim.ids["call"]}
    kinds = {e["type"] for e in demo.events}
    assert {"BED_EXIT", "OBSTACLE_REPORT", "OBSTACLE_RESULT", "BLOCKED", "UNBLOCKED", "CARD_OPEN", "CARD_CLOSED",
            "RESOURCE_HOLD", "MISSION_DONE"} <= kinds
    assert not {"DEADLOCK_DETECTED", "CORRIDOR_YIELD_START", "STALL_LONG", "PROXIMITY_STOP"} & kinds
