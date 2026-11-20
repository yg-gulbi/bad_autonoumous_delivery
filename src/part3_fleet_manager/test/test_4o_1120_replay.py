"""Stage 4O · 7B: 11/20 mock log replay (5 scenes + 3s variant) and 4N 15:02:26/15:03:38 in SEGMENTS."""
from dataclasses import replace

import pytest

from fleet_simulation import Simulation
from kinematic_mock import KinematicSim

A, B = "robot_a", "robot_b"


def _first_move_time(sim: KinematicSim, robot: str, after: float) -> float | None:
    for ts, r, cmd in sim.all_commands:
        if r == robot and ts >= after - 1e-6 and cmd.startswith("MOVE "):
            return ts
    return None


def _count_events(sim, kind: str) -> int:
    return sum(1 for e in sim.all_events if e["type"] == kind)


def _wire(out):
    return {c.robot: c.command for c in out.commands}


def _at(sim, robot, point):
    sim.frames[robot]["pose"] = sim.pose(point)


def _run_160550(cfg, mode: str) -> dict:
    """16:05:50 A(L2) D->C while B(L3) F->E is driving after grant."""
    sim = KinematicSim(cfg, mode=mode, speed_mps=0.2)
    try:
        # B(L3) F->E->B->D starts at t=0.2 (simulating 16:05:46)
        sim.submit_med(("E", "B", "D"), level=3, ref="m2")
        sim.run_until(4.0, auto_confirm=False)
        b_start = _first_move_time(sim, B, 0.0)
        assert b_start is not None
        # 4 s later (16:05:50): A(L2) D->C requested
        req_t = sim.now
        sim.submit_patient("C", "A", level=2, ref="m1")
        # Run until A departs (do NOT confirm B at E until A departs, just like 11/20!)
        sim.run_until(req_t + 40.0, auto_confirm=False)
        a_start = _first_move_time(sim, A, req_t)
        assert a_start is not None
        b_arr = next((e["ts"] for e in sim.all_events if e["type"] == "ARRIVE" and e["robot"] == B), None)
        return {
            "wait_s": round(a_start - req_t, 2),
            "a_start": a_start,
            "b_start": b_start,
            "b_arr": b_arr,
            "prox_stops": _count_events(sim, "PROXIMITY_STOP"),
        }
    finally:
        sim.close()


def test_160550_a_d_to_c_starts_once_b_enters_door_e(cfg):
    leg = _run_160550(cfg, "LEGACY")
    seg = _run_160550(cfg, "SEGMENTS")
    # In SEGMENTS, A starts as soon as B clears CROSSROAD into E door/room (well under 27 s and before B reaches E1).
    assert seg["wait_s"] < 27.0
    assert seg["a_start"] < seg["b_arr"]
    assert seg["prox_stops"] == 0
    assert leg["wait_s"] > 0.0


def _run_160632(cfg, mode: str) -> dict:
    """16:06:32 B(L3) E->B while A(L2) D->C is going down the corridor."""
    sim = KinematicSim(cfg, mode=mode, speed_mps=0.2, poses={A: "D", B: "E"})
    try:
        # A(L2) D->C starts driving down corridor
        sim.submit_patient("C", "A", level=2, ref="m1")
        # Wait until A is inside CORRIDOR_TOP near CROSSROAD (cannot be preempted: < 0.8 m from CROSSROAD)
        while sim.now < 20.0:
            sim.step(0.2)
            if sim.robots[A].pose[1] <= -1.35:
                break
        req_t = sim.now
        sim.submit_med(("B", "D"), level=3, ref="m2")
        # Advance until B departs
        sim.run_until(req_t + 30.0, auto_confirm=False)
        b_start = _first_move_time(sim, B, req_t)
        assert b_start is not None
        # Also record when A actually arrived at C
        a_arr = next((e["ts"] for e in sim.all_events if e["type"] == "ARRIVE" and e["robot"] == A), None)
        return {
            "wait_s": round(b_start - req_t, 2),
            "b_start": b_start,
            "a_arr": a_arr,
            "prox_stops": _count_events(sim, "PROXIMITY_STOP"),
        }
    finally:
        sim.close()


def test_160632_b_e_to_b_starts_as_soon_as_a_clears_crossroad(cfg):
    leg = _run_160632(cfg, "LEGACY")
    seg = _run_160632(cfg, "SEGMENTS")
    # In SEGMENTS, B starts right after A clears CROSSROAD (while A is still in CORRIDOR_BOTTOM), strictly shorter than LEGACY!
    assert seg["wait_s"] < leg["wait_s"]
    assert seg["wait_s"] < 14.0
    assert seg["b_start"] < seg["a_arr"]
    assert seg["prox_stops"] == 0


def _run_160709(cfg, mode: str) -> dict:
    """16:07:09 B(L3) B->D while A(L1, C->D return) is going up CORRIDOR_BOTTOM."""
    sim = KinematicSim(cfg, mode=mode, speed_mps=0.2, poses={A: "C", B: "B"})
    try:
        # Start A(L1) C->D (return_home / GO_ZONE at Level 1)
        sim.cmd("GO_ZONE", robot=A, zone="D")
        # Wait until A enters CORRIDOR_BOTTOM heading up, still >= 0.8 m from CROSSROAD (y ~ -3.9, dist to CROSSROAD ~ 1.12 m)
        while sim.now < 20.0:
            sim.step(0.2)
            if sim.robots[A].pose[1] >= -4.05 and sim.robots[A].pose[0] > -2.15:
                break
        req_t = sim.now
        sim.submit_med(("D", "F"), level=3, ref="m2")
        sim.run_until(req_t + 45.0, auto_confirm=False)
        b_start = _first_move_time(sim, B, req_t)
        assert b_start is not None
        preempts = [e for e in sim.all_events if e["type"] == "RESOURCE_PREEMPT"]
        a_pauses = [ts for ts, r, cmd in sim.all_commands if r == A and cmd == "PAUSE" and ts >= req_t]
        a_resumes = [ts for ts, r, cmd in sim.all_commands if r == A and cmd == "RESUME" and ts >= req_t]
        b_follow_stops = [e for e in sim.all_events if e["type"] == "CORRIDOR_FOLLOW_STOP" and e["robot"] == B]
        return {
            "wait_s": round(b_start - req_t, 2),
            "preempts": len(preempts),
            "a_paused": len(a_pauses),
            "a_resumed": len(a_resumes),
            "b_follow_stops": len(b_follow_stops),
            "a_zone": sim.core.mission.robots[A].zone,
            "b_zone": sim.core.mission.robots[B].zone,
            "prox_stops": _count_events(sim, "PROXIMITY_STOP"),
        }
    finally:
        sim.close()


def test_160709_b_b_to_d_preempts_crossroad_and_a_follows_behind(cfg):
    leg = _run_160709(cfg, "LEGACY")
    seg = _run_160709(cfg, "SEGMENTS")
    # In SEGMENTS: B preempts CROSSROAD immediately (no 17 s bogus follow-stop on B!),
    # A pauses at stop line and resumes behind B, both reach D!
    assert seg["preempts"] >= 1
    assert seg["wait_s"] <= 2.0
    assert seg["b_follow_stops"] == 0
    assert seg["a_paused"] >= 1 and seg["a_resumed"] >= 1
    assert seg["a_zone"] == "D" and seg["b_zone"] == "D"
    assert seg["prox_stops"] == 0
    assert leg["wait_s"] >= 0.0  # baseline measured cleanly


def _run_160959(cfg, mode: str, *, lead_s: float) -> dict:
    """16:09:59 B(L3) F->E while A(L2) D->C is going down CORRIDOR_TOP (lead_s controls A's progress)."""
    sim = KinematicSim(cfg, mode=mode, speed_mps=0.2, poses={A: "D", B: "F"})
    try:
        sim.submit_patient("C", "A", level=2, ref="m3")
        # Wait until A starts moving, then let A drive for lead_s seconds.
        while sim.now < 5.0 and _first_move_time(sim, A, 0.0) is None:
            sim.step(0.2)
        a_move = _first_move_time(sim, A, 0.0)
        assert a_move is not None
        sim.run_until(a_move + lead_s, auto_confirm=False)
        req_t = sim.now
        a_dist_to_cross = (
            sim.core.resources.segments["CROSSROAD"].spec.distance(sim.robots[A].pose)
            if mode == "SEGMENTS" else None
        )
        sim.submit_med(("E", "B"), level=3, ref="m4")
        sim.run_until(req_t + 60.0, auto_confirm=False)
        b_start = _first_move_time(sim, B, req_t)
        assert b_start is not None
        preempts = [e for e in sim.all_events if e["type"] == "RESOURCE_PREEMPT"]
        a_pauses = [ts for ts, r, cmd in sim.all_commands if r == A and cmd == "PAUSE" and ts >= req_t]
        a_resumes = [ts for ts, r, cmd in sim.all_commands if r == A and cmd == "RESUME" and ts >= req_t]
        return {
            "wait_s": round(b_start - req_t, 2),
            "a_dist": round(a_dist_to_cross, 3) if a_dist_to_cross is not None else None,
            "preempts": len(preempts),
            "a_paused": len(a_pauses),
            "a_resumed": len(a_resumes),
            "a_zone": sim.core.mission.robots[A].zone,
            "b_zone": sim.core.mission.robots[B].zone,
            "prox_stops": _count_events(sim, "PROXIMITY_STOP"),
        }
    finally:
        sim.close()


def test_160959_both_preempt_and_within_stop_margin_variants(cfg):
    # Variant 1 (3 s earlier): A is entering CORRIDOR_TOP, >= 0.8 m from CROSSROAD even after decision_window_s -> B preempts!
    # Variant 2 (+3 s later): A has moved 0.6 m closer (< 0.8 m from CROSSROAD) -> B does NOT preempt and waits cleanly!
    early_seg = _run_160959(cfg, "SEGMENTS", lead_s=6.8)
    early_leg = _run_160959(cfg, "LEGACY", lead_s=6.8)
    assert early_seg["a_dist"] >= 0.8
    assert early_seg["preempts"] >= 1
    assert early_seg["wait_s"] <= 2.0
    assert early_seg["wait_s"] < early_leg["wait_s"]
    assert early_seg["a_paused"] >= 1 and early_seg["a_resumed"] >= 1
    assert early_seg["a_zone"] == "C" and early_seg["b_zone"] == "E"
    assert early_seg["prox_stops"] == 0

    # Variant 2 (3 s later): A is < 0.8 m from CROSSROAD -> B does NOT preempt and waits cleanly for A!
    late_seg = _run_160959(cfg, "SEGMENTS", lead_s=9.8)
    assert 0.0 < late_seg["a_dist"] < 0.8
    assert late_seg["preempts"] == 0
    assert late_seg["wait_s"] > 2.0
    assert late_seg["a_zone"] == "C" and late_seg["b_zone"] == "E"
    assert late_seg["prox_stops"] == 0


def _run_161049(cfg, mode: str) -> dict:
    """16:10:49 B(L3) E->B while A(L2) C->A departed 1 s earlier."""
    sim = KinematicSim(cfg, mode=mode, speed_mps=0.2, poses={A: "C", B: "E"})
    try:
        sim.submit_patient("A", "D", level=2, ref="m3")
        while sim.now < 5.0 and _first_move_time(sim, A, 0.0) is None:
            sim.step(0.2)
        a_move = _first_move_time(sim, A, 0.0)
        assert a_move is not None
        # 1 s after A departed: B(L3) E->B requested
        sim.run_until(a_move + 1.0, auto_confirm=False)
        req_t = sim.now
        sim.submit_med(("B", "F"), level=3, ref="m4")
        sim.run_until(req_t + 65.0, auto_confirm=False)
        b_start = _first_move_time(sim, B, req_t)
        assert b_start is not None
        preempts = [e for e in sim.all_events if e["type"] == "RESOURCE_PREEMPT"]
        a_pauses = [ts for ts, r, cmd in sim.all_commands if r == A and cmd == "PAUSE" and ts >= req_t]
        a_resumes = [ts for ts, r, cmd in sim.all_commands if r == A and cmd == "RESUME" and ts >= req_t]
        return {
            "wait_s": round(b_start - req_t, 2),
            "preempts": len(preempts),
            "a_paused": len(a_pauses),
            "a_resumed": len(a_resumes),
            "a_zone": sim.core.mission.robots[A].zone,
            "b_zone": sim.core.mission.robots[B].zone,
            "prox_stops": _count_events(sim, "PROXIMITY_STOP"),
        }
    finally:
        sim.close()


def test_161049_b_e_to_b_starts_immediately_and_a_waits_at_bottom_stop_line(cfg):
    leg = _run_161049(cfg, "LEGACY")
    seg = _run_161049(cfg, "SEGMENTS")
    # B starts immediately via preemption (<= 2 s vs ~27 s LEGACY); A drives to stop line, pauses, then resumes!
    assert seg["preempts"] >= 1
    assert seg["wait_s"] <= 2.0
    assert seg["wait_s"] < leg["wait_s"]
    assert seg["a_paused"] >= 1 and seg["a_resumed"] >= 1
    assert seg["a_zone"] == "A" and seg["b_zone"] == "B"
    assert seg["prox_stops"] == 0


# --- 4N field timelines 15:02:26 & 15:03:38 in SEGMENTS mode (0 proximity stops) -----------------

def test_150226_segments_zero_proximity_stops(cfg, tmp_path):
    sc = replace(cfg, db_path=str(tmp_path / "f1.db"), traffic=replace(cfg.traffic, mode="SEGMENTS"))
    sim = Simulation(sc, slots=True)
    try:
        _at(sim, B, sim.cfg.slot_point("F", 2))
        sim.cmd("GO_ZONE", robot=A, zone="C")
        sim.tick(0)
        assert _wire(sim.tick(1))[A].startswith("MOVE C ")
        sim.event(A, "MISSION_ACCEPTED")
        _at(sim, A, (-1.65, -3.7))
        sim.cmd("GO_ZONE", robot=B, zone="D")
        sim.tick(1.1)
        _at(sim, A, (-1.81, -4.70))
        sim.tick(2)
        for i in range(1, 12):
            _at(sim, A, (-1.81 - 0.039 * i / 11, -4.7))
            assert not sim.tick(2 + 0.1 * i).commands
        assert A not in sim.core.resources.snapshot()["CORRIDOR"]["holders"]
        # Even after the 1.0 s corridor decision window expires at t=4.0, A is still moving within 0.9 m -> NearStartGuard holds B!
        assert not sim.tick(4.0).commands
        assert sim.core.wait_reason(B) == "가까이 움직이는 로봇 대기"
        _at(sim, A, (-2.26, -4.70))  # 0.91 m away -> B departs immediately with 0 proximity stops!
        assert _wire(sim.tick(4.1))[B].startswith("MOVE D ")
        prox = sim.core.store.db.execute("SELECT count(*) FROM events WHERE type='PROXIMITY_STOP'").fetchone()[0]
        assert prox == 0
    finally:
        sim.core.store.close()


def test_150338_segments_zero_proximity_stops(cfg, tmp_path):
    sc = replace(cfg, db_path=str(tmp_path / "f2.db"), traffic=replace(cfg.traffic, mode="SEGMENTS"))
    sim = Simulation(sc, slots=True)
    try:
        _at(sim, B, "C")
        sim.patient("bed", "C", "F")
        sim.tick(0)
        assert _wire(sim.tick(1))[A].startswith("MOVE C2 ")
        sim.event(A, "MISSION_ACCEPTED")
        _at(sim, A, (-1.65, -3.55))
        sim.cmd("GO_ZONE", robot=B, zone="D")
        assert not sim.tick(2).commands
        _at(sim, A, (-1.85, -4.52))
        assert not sim.tick(3).commands
        assert not sim.tick(4.1).commands  # A still holds C2 even after clearing corridor!
        _at(sim, A, (-2.758, -4.539))
        assert not sim.tick(4.15).commands
        sim.event(A, "ARRIVED")
        assert not sim.tick(4.2).commands  # A now parked in C2 -> B still waits on C2!
        sim.confirm(A, "bed")
        assert _wire(sim.tick(5.0))[A].startswith("MOVE F ")
        sim.event(A, "MISSION_ACCEPTED")
        _at(sim, A, (-2.15, -4.64))  # still within 0.9 m of B
        assert not sim.tick(6.0).commands
        _at(sim, A, (-1.70, -4.62))  # > 0.9 m away and outside C2 + clear_margin_m
        sim.tick(7.0)
        assert _wire(sim.tick(8.1))[B].startswith("MOVE D ")  # C2 released after clear_hold_s -> B departs immediately!
        prox = sim.core.store.db.execute("SELECT count(*) FROM events WHERE type='PROXIMITY_STOP'").fetchone()[0]
        assert prox == 0
    finally:
        sim.core.store.close()
