"""Stage 4O: 11/20 mock log replay (5 scenes, 16:09:59 in two variants) and 4N 15:02:26/15:03:38 in PLAN.

The Level 3 robot is always robot_b. Each scene is measured in LEGACY too; the report table
(docs/part3/ui/stage4o-report.md) lists LEGACY, ac58e60 SEGMENTS and PLAN side by side. The mock robots
drive through the middle of the B·E doors (plan_graph.door_route) in both modes, so the arrival bounds
include that walk.
"""
from dataclasses import replace

import pytest

from kinematic_mock import KinematicSim

A, B = "robot_a", "robot_b"


def _first_move_time(sim: KinematicSim, robot: str, after: float) -> float | None:
    for ts, r, cmd in sim.all_commands:
        if r == robot and ts >= after - 1e-6 and cmd.startswith("MOVE "):
            return ts
    return None


def _paused_s(sim, robot, after):
    total, since = 0.0, None
    for ts, r, cmd in sim.all_commands:
        if r != robot or ts < after - 1e-6:
            continue
        if cmd == "PAUSE" and since is None:
            since = ts
        elif cmd == "RESUME" and since is not None:
            total, since = total + ts - since, None
    return round(total + (sim.now - since if since is not None else 0.0), 1)


def _measure(sim, req_t):
    """Level 3 robot (B): wait until its first MOVE, time to its first arrival, time spent paused."""
    first = _first_move_time(sim, B, req_t)
    arrive = next((e["ts"] for e in sim.all_events if e["type"] == "ARRIVE" and e["robot"] == B and e["ts"] >= req_t), None)
    return {
        "wait_s": round(first - req_t, 1) if first is not None else None,
        "arrive_s": round(arrive - req_t, 1) if arrive is not None else None,
        "b_paused_s": _paused_s(sim, B, req_t),
        "a_paused_s": _paused_s(sim, A, req_t),
        "a_zone": sim.core.mission.robots[A].zone,
        "b_zone": sim.core.mission.robots[B].zone,
        "prox_stops": sum(1 for e in sim.all_events if e["type"] == "PROXIMITY_STOP"),
        "a_waits": [e["detail"]["reason"] for e in sim.all_events if e["type"] == "PLAN_WAIT" and e["robot"] == A],
        "a_aside": sum(1 for e in sim.all_events if e["type"] == "PLAN_YIELD_MOVE" and e["robot"] == A),
        "fallbacks": [e["detail"]["reason"] for e in sim.all_events
                      if e["type"] == "PLAN_UPDATE" and e["detail"].get("fallback")],
    }


def _run_160550(cfg, mode: str) -> dict:
    """16:05:50 A(L2) D->C is requested 4 s after B(L3) F->E->B->D started."""
    sim = KinematicSim(cfg, mode=mode, speed_mps=0.2)
    try:
        sim.submit_med(("E", "B", "D"), level=3, ref="m2")
        sim.run_until(4.0, auto_confirm=False)
        a_req = sim.now
        sim.submit_patient("C", "A", level=2, ref="m1")
        sim.run_until(a_req + 60.0, auto_confirm=False)
        out = _measure(sim, 0.0)
        a_start = _first_move_time(sim, A, a_req)
        out["a_wait_s"] = round(a_start - a_req, 1) if a_start is not None else None
        return out
    finally:
        sim.close()


def _run_160632(cfg, mode: str) -> dict:
    """16:06:32 B(L3) E->B while A(L2) D->C is in CORRIDOR_TOP, too close to CROSSROAD to stop before it."""
    sim = KinematicSim(cfg, mode=mode, speed_mps=0.2, poses={A: "D", B: "E"})
    try:
        sim.submit_patient("C", "A", level=2, ref="m1")
        while sim.now < 20.0:
            sim.step(0.2)
            if sim.robots[A].pose[1] <= -1.35:
                break
        req_t = sim.now
        sim.submit_med(("B", "D"), level=3, ref="m2")
        sim.run_until(req_t + 45.0, auto_confirm=False)
        return _measure(sim, req_t)
    finally:
        sim.close()


def _run_160709(cfg, mode: str) -> dict:
    """16:07:09 B(L3) B->D while A(L1, C->D return) is going up CORRIDOR_BOTTOM, >= 0.8 m from CROSSROAD."""
    sim = KinematicSim(cfg, mode=mode, speed_mps=0.2, poses={A: "C", B: "B"})
    try:
        sim.cmd("GO_ZONE", robot=A, zone="D")
        while sim.now < 20.0:
            sim.step(0.2)
            if sim.robots[A].pose[1] >= -4.05 and sim.robots[A].pose[0] > -2.15:
                break
        req_t = sim.now
        sim.submit_med(("D", "F"), level=3, ref="m2")
        sim.run_until(req_t + 45.0, auto_confirm=False)
        return _measure(sim, req_t)
    finally:
        sim.close()


def _run_160959(cfg, mode: str, *, lead_s: float) -> dict:
    """16:09:59 B(L3) F->E while A(L2) D->C is going down CORRIDOR_TOP (lead_s = how long A has driven)."""
    sim = KinematicSim(cfg, mode=mode, speed_mps=0.2, poses={A: "D", B: "F"})
    try:
        sim.submit_patient("C", "A", level=2, ref="m3")
        while sim.now < 5.0 and _first_move_time(sim, A, 0.0) is None:
            sim.step(0.2)
        a_move = _first_move_time(sim, A, 0.0)
        assert a_move is not None
        sim.run_until(a_move + lead_s, auto_confirm=False)
        req_t = sim.now
        stop_line = sim.cfg.traffic.stop_margin_m
        crossroad = sim.core.resources.segments["CROSSROAD"].spec if mode == "PLAN" else None
        a_dist = crossroad.distance(sim.robots[A].pose) if crossroad else None
        sim.submit_med(("E", "B"), level=3, ref="m4")
        sim.run_until(req_t + 60.0, auto_confirm=False)
        out = _measure(sim, req_t)
        out["a_dist"] = round(a_dist, 3) if a_dist is not None else None
        out["stop_line"] = stop_line
        return out
    finally:
        sim.close()


def _run_161049(cfg, mode: str) -> dict:
    """16:10:49 B(L3) E->B one second after A(L2) C->A departed."""
    sim = KinematicSim(cfg, mode=mode, speed_mps=0.2, poses={A: "C", B: "E"})
    try:
        sim.submit_patient("A", "D", level=2, ref="m3")
        while sim.now < 5.0 and _first_move_time(sim, A, 0.0) is None:
            sim.step(0.2)
        a_move = _first_move_time(sim, A, 0.0)
        assert a_move is not None
        sim.run_until(a_move + 1.0, auto_confirm=False)
        req_t = sim.now
        sim.submit_med(("B", "F"), level=3, ref="m4")
        sim.run_until(req_t + 65.0, auto_confirm=False)
        return _measure(sim, req_t)
    finally:
        sim.close()


def test_160550_l2_robot_starts_at_once_and_waits_at_the_corridor_stop_line(cfg):
    leg = _run_160550(cfg, "LEGACY")
    plan = _run_160550(cfg, "PLAN")
    assert plan["wait_s"] <= 1.0 and plan["arrive_s"] <= leg["arrive_s"]
    assert plan["b_paused_s"] == 0.0
    # LEGACY kept A in D for ~19 s; PLAN sends it at once and pauses it before B's way, naming B.
    assert leg["a_wait_s"] > 10.0
    assert plan["a_wait_s"] <= 1.0
    assert plan["a_paused_s"] > 0.0
    assert any("약품 운송 로봇이 지나간 뒤" in r for r in plan["a_waits"])
    assert plan["prox_stops"] == 0


def test_160632_l3_waits_only_while_a_crosses_and_arrives_before_legacy(cfg):
    leg = _run_160632(cfg, "LEGACY")
    plan = _run_160632(cfg, "PLAN")
    # A is inside its stop line before CROSSROAD: it cannot stop, so B waits for it to pass. B leaves E1 once
    # A is past the room E way and may stop once at the CROSSROAD stop line while A clears it (clear_hold_s).
    assert plan["wait_s"] < leg["wait_s"]
    assert plan["arrive_s"] is not None and plan["arrive_s"] <= 29.0 and plan["arrive_s"] < leg["arrive_s"]
    assert plan["a_paused_s"] == 0.0 and plan["b_paused_s"] <= cfg.resources.clear_hold_s
    assert plan["a_zone"] == "C" and plan["b_zone"] == "B"
    assert plan["prox_stops"] == 0


def test_160709_l3_goes_first_and_l1_robot_waits_below_crossroad(cfg):
    leg = _run_160709(cfg, "LEGACY")
    plan = _run_160709(cfg, "PLAN")
    assert plan["wait_s"] <= 1.0
    assert plan["arrive_s"] < leg["arrive_s"] and plan["arrive_s"] <= 23.0
    assert plan["b_paused_s"] == 0.0 and leg["b_paused_s"] > 10.0
    assert plan["a_paused_s"] > 0.0
    assert plan["a_zone"] == "D" and plan["b_zone"] == "D"
    assert plan["prox_stops"] == 0


@pytest.mark.parametrize("lead_s", [6.8, 9.8])
def test_160959_l3_starts_at_once_whether_or_not_a_can_stop_before_crossroad(cfg, lead_s):
    leg = _run_160959(cfg, "LEGACY", lead_s=lead_s)
    plan = _run_160959(cfg, "PLAN", lead_s=lead_s)
    if lead_s == 6.8:
        assert plan["a_dist"] >= plan["stop_line"]    # A can still stop before CROSSROAD: it pauses there
        assert plan["a_aside"] == 0 and plan["a_paused_s"] > 0.0
    else:
        assert plan["a_dist"] < plan["stop_line"]     # A cannot stop before CROSSROAD: it gets out of B's way
        assert plan["a_aside"] >= 1
    assert plan["wait_s"] <= 1.0
    assert plan["arrive_s"] <= 27.0 and plan["arrive_s"] < leg["arrive_s"]
    assert plan["b_paused_s"] == 0.0
    assert plan["a_zone"] == "C" and plan["b_zone"] == "E"
    assert plan["prox_stops"] == 0


def test_161049_l3_starts_at_once_and_l2_robot_waits_in_corridor_bottom(cfg):
    leg = _run_161049(cfg, "LEGACY")
    plan = _run_161049(cfg, "PLAN")
    assert plan["wait_s"] <= 1.0 and leg["wait_s"] > 20.0
    assert plan["arrive_s"] <= 19.5 and plan["arrive_s"] < leg["arrive_s"]
    assert plan["b_paused_s"] == 0.0 and plan["a_paused_s"] > 0.0
    assert plan["a_zone"] == "A" and plan["b_zone"] == "B"
    assert plan["prox_stops"] == 0


def test_160959_level_order_plan_when_every_joint_search_runs_out_of_time(cfg):
    """plan_limit_ms past (here: every joint search): the Level-order plan takes over and PLAN_UPDATE says so."""
    tiny = replace(cfg, traffic=replace(cfg.traffic, graph=replace(cfg.traffic.graph, plan_limit_ms=0.01)))
    plan = _run_160959(tiny, "PLAN", lead_s=9.8)
    assert plan["fallbacks"] and all("Level 순 계획으로 대신 (계획 시간 초과)" in r for r in plan["fallbacks"])
    assert plan["wait_s"] <= 1.0 and plan["b_paused_s"] == 0.0
    assert plan["a_zone"] == "C" and plan["b_zone"] == "E"
    assert plan["prox_stops"] == 0


# --- 4N field timelines 15:02:26 & 15:03:38 in PLAN (0 proximity stops) -----------------------------

def test_150226_plan_zero_proximity_stops(cfg):
    """15:02:26: B stands in F2; A D->C comes down toward the bottom hall when B is sent F->D."""
    sim = KinematicSim(cfg, mode="PLAN", speed_mps=0.2, poses={B: cfg.slot_point("F", 2)})
    try:
        sim.cmd("GO_ZONE", robot=A, zone="C")
        while sim.now < 40.0 and sim.robots[A].pose[1] > -3.7:
            sim.step(0.2)
        assert sim.robots[A].pose[1] <= -3.7
        sim.cmd("GO_ZONE", robot=B, zone="D")
        sim.run_until(sim.now + 60.0)
        assert sim.core.mission.robots[A].zone == "C" and sim.core.mission.robots[B].zone == "D"
        assert sum(1 for e in sim.all_events if e["type"] == "PROXIMITY_STOP") == 0
    finally:
        sim.close()


def test_150338_plan_zero_proximity_stops(cfg):
    """15:03:38: B stands in C1; A picks a patient up in C (C2) and takes them to F while B is sent C->D."""
    sim = KinematicSim(cfg, mode="PLAN", speed_mps=0.2, poses={B: "C"})
    try:
        sim.submit_patient("C", "F", level=2, ref="bed")
        while sim.now < 40.0 and sim.robots[A].pose[1] > -3.55:
            sim.step(0.2)
        assert sim.robots[A].pose[1] <= -3.55
        sim.cmd("GO_ZONE", robot=B, zone="D")
        sim.run_until(sim.now + 90.0)
        done = [e for e in sim.all_events if e["type"] == "MISSION_DONE" and e["robot"] == A]
        assert done and any(e["type"] == "ARRIVE" and e["robot"] == A and e["ts"] <= done[0]["ts"]
                            and e["detail"].get("destination") == "F" for e in sim.all_events)
        assert sim.core.mission.robots[B].zone == "D"
        assert sum(1 for e in sim.all_events if e["type"] == "PROXIMITY_STOP") == 0
    finally:
        sim.close()
