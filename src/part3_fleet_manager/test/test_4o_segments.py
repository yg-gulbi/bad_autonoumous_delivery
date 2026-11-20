"""Stage 4O · 7A unit tests for segment map, unified comparator, preemption, cancel, and routes."""
from dataclasses import replace

import pytest

from fleet_simulation import Simulation
from kinematic_mock import KinematicSim
from part3_fleet_manager.core import schemas
from part3_fleet_manager.core.config import config_from_dict, read_config_yaml
from part3_fleet_manager.core.resource_manager import RobotReport
from part3_fleet_manager.core.route_planner import waypoints
from part3_fleet_manager.core.segments import SegmentManager, SegmentMap
from part3_fleet_manager.preflight_checks import traffic_mode_line

A, B = "robot_a", "robot_b"


def seg_cfg(cfg, **traffic_kw):
    return replace(cfg, db_path=":memory:", traffic=replace(cfg.traffic, mode="SEGMENTS", **traffic_kw))


def wire(out):
    return {c.robot: c.command for c in out.commands}


# --- 1. Segment kind sharing ---------------------------------------------------------------------

def test_segment_kind_sharing_shared_oneway_single(cfg):
    sc = seg_cfg(cfg)
    sm = SegmentManager(sc)
    # 1a. shared (HALL_TOP): A D->A1 and B A->D1 both only need shared HALL_TOP + distinct slots.
    # Wait: A1 is slot2_blocks_exit so leaving A1 needs A2, while leaving D1 -> A2 needs A2.
    # Use D1->A2 and A1->D1? Better: D1->A1 (if B at D2 -> A2) — use A at D1->A1 (no B at A1) and B at C1->F1 (HALL_BOTTOM).
    # For same shared HALL_TOP: A at (-1.5, 0.3) -> A1 and B at (-1.5, 0.3) -> D1 share HALL_TOP simultaneously.
    reports = {A: RobotReport((-1.5, 0.3), True), B: RobotReport((-1.5, 0.3), True)}
    sm.request(A, "A", 1, 0.0)
    sm.request(B, "D", 1, 0.0)
    res = sm.tick(1.0, reports)
    assert {leg.robot for leg in res.grants} == {A, B}

    # 1b. one_way (CORRIDOR_TOP): same direction shares, opposite direction blocks.
    sm2 = SegmentManager(sc)
    reports2 = {A: RobotReport(sc.zones["D"], True), B: RobotReport(sc.zones["A"], True)}
    sm2.request(A, "C", 2, 0.0)
    sm2.tick(1.0, reports2)
    # Advance A past CROSSROAD into CORRIDOR_BOTTOM so CROSSROAD is free, wait — test CORRIDOR_TOP only!
    # A holds CORRIDOR_TOP (dir=+1) + CROSSROAD + CORRIDOR_BOTTOM + C1; let B go A->E only if CROSSROAD free.
    # Better: A already in CORRIDOR_TOP heading +1 (only holding CORRIDOR_TOP + HALL_BOTTOM? No, let A go D->F1 and release nothing yet,
    # test direct one_way sharing by putting A on CORRIDOR_TOP dir +1 via D->C1 after releasing CROSSROAD? No, if A is in CORRIDOR_TOP heading +1
    # and we release A's CROSSROAD? No, check _evaluate: two robots both heading +1 on CORRIDOR_TOP if they don't collide on single segments!)
    # Even simpler: verify CORRIDOR_TOP directly by placing A with holding direction +1 on CORRIDOR_TOP and requesting B D->B? B needs CROSSROAD.
    # Since all routes through CORRIDOR_TOP also use CROSSROAD (which is single), verify one_way sharing directly on SegmentState + SegmentManager:
    sm2 = SegmentManager(sc)
    # A occupies CORRIDOR_TOP (+1) only (e.g. after entering CORRIDOR_TOP while going D->C, or before entering):
    # Actually: A heading +1 from D->C has entered CORRIDOR_TOP and released nothing yet, vs opposite B from C->D:
    # B (L3) from C->D needs CORRIDOR_BOTTOM (-1), CROSSROAD, CORRIDOR_TOP (-1). Since A entered CORRIDOR_TOP (+1), B MUST wait!
    sm2.request(A, "C", 1, 0.0)
    sm2.tick(1.0, {A: RobotReport(sc.zones["D"], True), B: RobotReport(sc.zones["C"], True)})
    assert A in sm2.segments["CORRIDOR_TOP"].holders
    sm2.tick(2.0, {A: RobotReport((-1.5, -1.0), True), B: RobotReport(sc.zones["C"], True)})
    assert sm2.segments["CORRIDOR_TOP"].holders[A].state == "OCCUPIED"
    sm2.request(B, "D", 3, 2.0)
    res_opp = sm2.tick(3.0, {A: RobotReport((-1.5, -1.0), True), B: RobotReport(sc.zones["C"], True)})
    assert not res_opp.grants  # opposite direction blocked on one_way CORRIDOR_TOP
    assert "사용 중" in sm2.wait_reasons[B]

    # Same direction on one_way CORRIDOR_TOP: if A holds CORRIDOR_TOP (+1) and has released nothing else (e.g. dock/exit path),
    # let's test B requesting CORRIDOR_TOP (+1) when CROSSROAD is free (e.g., A exited CROSSROAD -> nope, A in CORRIDOR_TOP hasn't entered CROSSROAD,
    # so B (L3) heading +1 from A->B can preempt CROSSROAD? Even cleaner: A already left CORRIDOR_TOP? No, A enters CORRIDOR_BOTTOM (+1) and releases
    # CORRIDOR_TOP and CROSSROAD; then B enters CORRIDOR_BOTTOM (+1) in same direction!).
    # Test same-direction sharing on CORRIDOR_BOTTOM (+1):
    # A went D->C (heading +1), cleared CORRIDOR_TOP and CROSSROAD, now physically in CORRIDOR_BOTTOM (+1) heading to C1.
    # B from D->F1 (heading +1) needs CORRIDOR_TOP (+1), CROSSROAD, CORRIDOR_BOTTOM (+1), F1 -> SHARES CORRIDOR_BOTTOM (+1) with A!
    sm3 = SegmentManager(sc)
    sm3.request(A, "C", 2, 0.0)
    sm3.tick(1.0, {A: RobotReport(sc.zones["D"], True), B: RobotReport(sc.zones["A"], True)})
    # Move A through CORRIDOR_TOP -> CROSSROAD -> CORRIDOR_BOTTOM and hold outside clear_margin for clear_hold_s.
    for t, pos in [(2.0, (-1.5, -1.0)), (3.0, (-1.5, -2.18)), (4.0, (-1.65, -3.5)), (5.2, (-1.65, -3.6))]:
        sm3.tick(t, {A: RobotReport(pos, True), B: RobotReport(sc.zones["A"], True)})
    assert A not in sm3.segments["CORRIDOR_TOP"].holders
    assert A not in sm3.segments["CROSSROAD"].holders
    assert sm3.segments["CORRIDOR_BOTTOM"].holders[A].state == "OCCUPIED"
    assert sm3.segments["CORRIDOR_BOTTOM"].holders[A].direction == 1
    sm3.request(B, "F", 1, 5.2)
    res_same = sm3.tick(6.3, {A: RobotReport((-1.65, -3.7), True), B: RobotReport(sc.zones["A"], True)})
    assert [leg.robot for leg in res_same.grants] == [B]
    assert set(sm3.segments["CORRIDOR_BOTTOM"].holders) == {A, B}  # shared same-direction one_way!

    # 1c. single (CROSSROAD): only 1 robot at a time.
    sm4 = SegmentManager(sc)
    sm4.request(A, "B", 2, 0.0)
    sm4.tick(1.0, {A: RobotReport(sc.zones["E"], True), B: RobotReport(sc.zones["D"], True)})
    assert A in sm4.segments["CROSSROAD"].holders
    sm4.tick(1.5, {A: RobotReport(sc.corridor.crossroad, True), B: RobotReport(sc.zones["D"], True)})
    sm4.request(B, "C", 3, 1.5)
    res_single = sm4.tick(2.5, {A: RobotReport(sc.corridor.crossroad, True), B: RobotReport(sc.zones["D"], True)})
    assert not res_single.grants
    assert A in sm4.segments["CROSSROAD"].holders and B not in sm4.segments["CROSSROAD"].holders


# --- 2. All-or-nothing acquisition ---------------------------------------------------------------

def test_all_or_nothing_no_partial_grant(cfg):
    sc = seg_cfg(cfg)
    sm = SegmentManager(sc)
    # B is parked in B1 (occupies single segment B1) while CORRIDOR_TOP, CROSSROAD, DOOR_B are completely FREE.
    # A (L3) requests D->B -> picks B2 if slot2 exists; occupy BOTH B1 and B2? Actually B has only 1 slot? Check!
    # Check slot_count: every zone has 2 slots; disable slot_support for A so A wants B1 only, OR put B in B1 and request B1.
    sm.slot_support[A] = False
    reports = {A: RobotReport(sc.zones["D"], True), B: RobotReport(sc.zones["B"], True)}
    sm.tick(0.0, reports)
    assert B in sm.segments["B1"].holders
    sm.request(A, "B", 3, 0.0)
    res = sm.tick(1.0, reports)
    assert not res.grants
    # Must NOT hold CORRIDOR_TOP, CROSSROAD, or DOOR_B partially!
    for seg_name in ("CORRIDOR_TOP", "CROSSROAD", "DOOR_B", "B1"):
        assert A not in sm.segments[seg_name].holders


# --- 3. Passed-segment release with clear_margin_m + clear_hold_s --------------------------------

def test_passed_segment_released_after_clear_margin_and_hold(cfg):
    sc = seg_cfg(cfg)
    sm = SegmentManager(sc)
    sm.request(A, "C", 2, 0.0)
    sm.tick(1.0, {A: RobotReport(sc.zones["D"], True), B: RobotReport(sc.zones["F"], True)})
    # Enter CORRIDOR_TOP at t=2.0
    sm.tick(2.0, {A: RobotReport((-1.5, -1.0), True), B: RobotReport(sc.zones["F"], True)})
    assert sm.segments["CORRIDOR_TOP"].holders[A].state == "OCCUPIED"
    # Just outside CORRIDOR_TOP by 0.10 m (< clear_margin_m = 0.20 m): NOT clearing yet
    sm.tick(2.5, {A: RobotReport((-1.5, -1.66), True), B: RobotReport(sc.zones["F"], True)})
    assert sm.segments["CORRIDOR_TOP"].holders[A].outside_since is None
    # Outside by > 0.20 m at CROSSROAD center (-1.50, -2.18) at t=3.0: starts clear_hold_s (1.0 s) timer
    sm.tick(3.0, {A: RobotReport((-1.5, -2.18), True), B: RobotReport(sc.zones["F"], True)})
    assert sm.segments["CORRIDOR_TOP"].holders[A].outside_since == 3.0
    # At t=3.9 (0.9 s < 1.0 s): still held
    sm.tick(3.9, {A: RobotReport((-1.5, -2.18), True), B: RobotReport(sc.zones["F"], True)})
    assert A in sm.segments["CORRIDOR_TOP"].holders
    # At t=4.0 (1.0 s >= clear_hold_s): CORRIDOR_TOP released, CROSSROAD + CORRIDOR_BOTTOM + C1 still held!
    sm.tick(4.0, {A: RobotReport((-1.5, -2.18), True), B: RobotReport(sc.zones["F"], True)})
    assert A not in sm.segments["CORRIDOR_TOP"].holders
    assert A in sm.segments["CROSSROAD"].holders
    assert A in sm.segments["CORRIDOR_BOTTOM"].holders
    assert A in sm.segments["C1"].holders


# --- 4. Unified comparator order (INSIDE > LEVEL > REQUEST_TIME > ORDER) -------------------------

def test_comparator_priority_inside_and_exit_beats_level(cfg):
    sc = seg_cfg(cfg)
    # 4a. Within stop_margin_m (0.8 m) of CORRIDOR_TOP beats Level 3 farther away!
    sm = SegmentManager(sc)
    near_top = (-1.50, -0.20)  # ~0.35 m from CORRIDOR_TOP (< 0.8 m)
    far_bottom = sc.zones["F"]  # > 1.0 m from CORRIDOR_BOTTOM & CROSSROAD
    sm.request(A, "C", 1, 0.0)
    sm.request(B, "D", 3, 0.0)
    res = sm.tick(1.0, {A: RobotReport(near_top, True), B: RobotReport(far_bottom, True)})
    assert [leg.robot for leg in res.grants] == [A]
    grant = next(e for e in sm.events if e["type"] == "RESOURCE_GRANT")
    assert grant["detail"]["rule"] == "INSIDE"

    # 4b. Leaving zone slot C1 gets Priority 1 on exit segment C2 (slot2_blocks_exit) over inbound Level 3!
    sm2 = SegmentManager(sc)
    reports2 = {A: RobotReport(sc.zones["D"], True), B: RobotReport(sc.zones["C"], True)}
    sm2.tick(0.0, reports2)  # observe B parked in C1
    sm2.request(A, "C", 3, 0.0)  # inbound A wants C2 (since B is in C1)
    sm2.request(B, "D", 1, 0.0)  # outbound B leaves C1 through exit segment C2
    res2 = sm2.tick(1.0, reports2)
    assert [leg.robot for leg in res2.grants] == [B]
    grant2 = next(e for e in sm2.events if e["type"] == "RESOURCE_GRANT" and e["robot"] == B)
    assert grant2["detail"]["rule"] == "INSIDE"


def test_comparator_level_aging_request_time_and_order(cfg):
    sc = seg_cfg(cfg)
    far_reports = {A: RobotReport(sc.zones["D"], True), B: RobotReport(sc.zones["F"], True)}

    # 4c. Higher Level beats earlier request_time within decision_window_s.
    sm = SegmentManager(sc)
    sm.request(A, "C", 1, 0.0)
    sm.request(B, "A", 3, 0.2)
    res = sm.tick(1.0, far_reports)
    assert [leg.robot for leg in res.grants] == [B]
    grant = next(e for e in sm.events if e["type"] == "RESOURCE_GRANT")
    assert grant["detail"]["rule"] == "LEVEL"

    # 4d. Aging boosts waiting Level 1 over new Level 2 after aging_s.
    sm_age = SegmentManager(replace(sc, resources=replace(sc.resources, aging_s=10.0)))
    sm_age.request(A, "C", 1, 0.0)
    sm_age.request(B, "A", 2, 20.0)  # at t=25: A eff=1+2=3, B eff=2+0=2
    res_age = sm_age.tick(25.0, far_reports)
    assert [leg.robot for leg in res_age.grants] == [A]
    assert next(e for e in sm_age.events if e["type"] == "RESOURCE_GRANT")["detail"]["rule"] == "LEVEL"

    # 4e. Equal Level -> earlier request timestamp wins (REQUEST_TIME).
    sm_t = SegmentManager(sc)
    sm_t.request(B, "A", 2, 0.0)
    sm_t.request(A, "C", 2, 0.3)
    res_t = sm_t.tick(1.0, far_reports)
    assert [leg.robot for leg in res_t.grants] == [B]
    assert next(e for e in sm_t.events if e["type"] == "RESOURCE_GRANT")["detail"]["rule"] == "REQUEST_TIME"

    # 4f. Exact tie on priority, level, request_time -> config robot order (robot_a before robot_b, ORDER).
    sm_o = SegmentManager(sc)
    sm_o.request(B, "A", 2, 0.0)
    sm_o.request(A, "C", 2, 0.0)
    res_o = sm_o.tick(1.0, far_reports)
    assert [leg.robot for leg in res_o.grants] == [A]
    assert next(e for e in sm_o.events if e["type"] == "RESOURCE_GRANT")["detail"]["rule"] == "ORDER"


# --- 5. All 5 preemption conditions + Rule 1 never steals held segment ---------------------------

def test_preemption_equal_level_does_not_preempt(cfg):
    sc = seg_cfg(cfg)
    sm = SegmentManager(sc)
    reports = {A: RobotReport(sc.zones["D"], True, departed=True), B: RobotReport(sc.zones["E"], True)}
    sm.request(A, "C", 2, 0.0)
    sm.tick(1.0, reports)
    sm.request(B, "B", 2, 1.5)  # equal Level 2
    res = sm.tick(2.5, reports)
    assert not res.grants
    assert not any(e["type"] == "RESOURCE_PREEMPT" for e in sm.events)
    assert A in sm.segments["CROSSROAD"].holders


def test_preemption_entered_segment_is_not_preempted(cfg):
    sc = seg_cfg(cfg)
    sm = SegmentManager(sc)
    sm.request(A, "C", 1, 0.0)
    sm.tick(1.0, {A: RobotReport(sc.zones["D"], True, departed=True), B: RobotReport(sc.zones["E"], True)})
    # A physically enters CROSSROAD
    sm.tick(2.0, {A: RobotReport(sc.corridor.crossroad, True, departed=True), B: RobotReport(sc.zones["E"], True)})
    assert sm.segments["CROSSROAD"].holders[A].state == "OCCUPIED"
    sm.request(B, "B", 3, 2.0)
    res = sm.tick(3.0, {A: RobotReport(sc.corridor.crossroad, True, departed=True), B: RobotReport(sc.zones["E"], True)})
    assert not res.grants
    assert not any(e["type"] == "RESOURCE_PREEMPT" for e in sm.events)


def test_preemption_holder_within_stop_margin_is_not_preempted(cfg):
    sc = seg_cfg(cfg)
    sm = SegmentManager(sc)
    sm.request(A, "C", 1, 0.0)
    sm.tick(1.0, {A: RobotReport(sc.zones["D"], True, departed=True), B: RobotReport(sc.zones["E"], True)})
    # A enters CORRIDOR_TOP and approaches CROSSROAD within 0.5 m (< stop_margin_m = 0.8 m), not yet inside CROSSROAD.
    # Crossroad center (-1.5, -2.18), r=0.6 -> boundary at y=-1.58; y=-1.20 is 0.38 m from boundary (< 0.8 m).
    near_cross = (-1.50, -1.20)
    assert 0.0 < sm.segments["CROSSROAD"].spec.distance(near_cross) < sc.traffic.stop_margin_m
    sm.tick(2.0, {A: RobotReport(near_cross, True, departed=True), B: RobotReport(sc.zones["E"], True)})
    sm.request(B, "B", 3, 2.0)
    res = sm.tick(3.0, {A: RobotReport(near_cross, True, departed=True), B: RobotReport(sc.zones["E"], True)})
    assert not res.grants
    assert not any(e["type"] == "RESOURCE_PREEMPT" for e in sm.events)


def test_preemption_aborted_if_preemptor_cannot_get_all_segments(cfg):
    sc = seg_cfg(cfg)
    sm = SegmentManager(sc)
    # A (L1) goes D->C (has entered CORRIDOR_TOP, far from CROSSROAD -> CROSSROAD would be preemptable,
    # BUT CORRIDOR_TOP is OCCUPIED by A heading +1!).
    # B (L3) wants F->D (needs CORRIDOR_BOTTOM, CROSSROAD, CORRIDOR_TOP (-1), D1).
    # Even though B could preempt CROSSROAD, B CANNOT get CORRIDOR_TOP (already OCCUPIED by A in opposite dir).
    # Therefore B MUST NOT preempt ANY segment from A!
    sm.request(A, "C", 1, 0.0)
    sm.tick(1.0, {A: RobotReport(sc.zones["D"], True, departed=True), B: RobotReport(sc.zones["F"], True)})
    top_entry = (-1.50, -0.60)  # inside CORRIDOR_TOP, ~0.98 m from CROSSROAD (> 0.8 m)
    assert sm.segments["CORRIDOR_TOP"].spec.contains(top_entry)
    assert sm.segments["CROSSROAD"].spec.distance(top_entry) >= sc.traffic.stop_margin_m
    sm.tick(2.0, {A: RobotReport(top_entry, True, departed=True), B: RobotReport(sc.zones["F"], True)})
    sm.request(B, "D", 3, 2.0)
    res = sm.tick(3.0, {A: RobotReport(top_entry, True, departed=True), B: RobotReport(sc.zones["F"], True)})
    assert not res.grants
    assert not any(e["type"] == "RESOURCE_PREEMPT" for e in sm.events)
    assert A in sm.segments["CROSSROAD"].holders
    assert A in sm.segments["CORRIDOR_BOTTOM"].holders
    assert A in sm.segments["C1"].holders


def test_preemption_pauses_at_stop_line_and_resumes_when_reacquired(cfg):
    sc = seg_cfg(cfg)
    sm = SegmentManager(sc)
    # A (L2) starts C->A and has departed; still in HALL_BOTTOM far from CORRIDOR_BOTTOM (>= 0.8 m) or in CORRIDOR_BOTTOM far from CROSSROAD.
    # Place A at (-2.7, -4.55) (> 0.8 m from CORRIDOR_BOTTOM) with departed=True; B (L3) at E wants E->B.
    a_start = sc.zones["C"]
    b_start = sc.zones["E"]
    assert sm.segments["CROSSROAD"].spec.distance(a_start) >= sc.traffic.stop_margin_m
    sm.request(A, "A", 2, 0.0, mission_id="M-A")
    sm.tick(1.0, {A: RobotReport(a_start, True, departed=True), B: RobotReport(b_start, True)})
    assert A in sm.segments["CORRIDOR_BOTTOM"].holders
    assert A in sm.segments["CROSSROAD"].holders
    assert A in sm.segments["CORRIDOR_TOP"].holders
    assert A in sm.segments["A1"].holders

    # B (L3) requests E->B (needs DOOR_E, CROSSROAD, DOOR_B, B1).
    # First preempted segment on A's path is CROSSROAD -> A keeps CORRIDOR_BOTTOM, drops CROSSROAD, CORRIDOR_TOP, A1!
    sm.request(B, "B", 3, 1.5, mission_id="M-B")
    res = sm.tick(2.5, {A: RobotReport(a_start, True, departed=True), B: RobotReport(b_start, True)})
    assert [leg.robot for leg in res.grants] == [B]
    assert A in sm.segments["CORRIDOR_BOTTOM"].holders  # prefix kept!
    assert A not in sm.segments["CROSSROAD"].holders
    assert A not in sm.segments["CORRIDOR_TOP"].holders
    assert A not in sm.segments["A1"].holders
    assert A in sm.stop_lines and sm.stop_lines[A].segment == "CROSSROAD"
    assert not sm.stop_lines[A].paused  # still > stop_margin_m from CROSSROAD -> keeps driving to stop line!
    assert sm.wait_reasons[A] == "응급도 우선: 약품 운송 로봇(Level 3)가 교차로 먼저 → 침상형 이송 로봇 정지선 대기"

    pre = next(e for e in sm.events if e["type"] == "RESOURCE_PREEMPT")
    assert pre["detail"]["rule"] == "PREEMPT"
    assert pre["detail"]["distance_m"] >= sc.traffic.stop_margin_m
    assert len(pre["detail"]["contenders"]) == 2

    # A drives into CORRIDOR_BOTTOM up to stop line (<= 0.8 m from CROSSROAD): emits PAUSE!
    stop_pos = (-1.65, -3.50)  # ~0.72 m from CROSSROAD boundary
    assert 0.0 < sm.segments["CROSSROAD"].spec.distance(stop_pos) <= sc.traffic.stop_margin_m
    res_pause = sm.tick(4.0, {A: RobotReport(stop_pos, True, departed=True), B: RobotReport(sc.corridor.crossroad, True, departed=True)})
    assert any(c.robot == A and c.command == "PAUSE" for c in res_pause.commands)
    assert sm.stop_lines[A].paused is True
    assert sm.robot_state(A, "MOVING") == "HOLD"

    # B clears CROSSROAD into B1 -> A re-acquires CROSSROAD, CORRIDOR_TOP, A1 and receives RESUME!
    sm.tick(6.0, {A: RobotReport(stop_pos, True, departed=True, nav_paused=True), B: RobotReport(sc.zones["B"], True, departed=True)})
    sm.tick(7.1, {A: RobotReport(stop_pos, True, departed=True, nav_paused=True), B: RobotReport(sc.zones["B"], True, departed=True)})
    res_resume = sm.tick(8.2, {A: RobotReport(stop_pos, True, departed=True, nav_paused=True), B: RobotReport(sc.zones["B"], True, departed=True)})
    assert A not in sm.stop_lines
    assert A in sm.segments["CROSSROAD"].holders
    assert A in sm.segments["CORRIDOR_TOP"].holders
    assert A in sm.segments["A1"].holders
    assert any(c.robot == A and c.command == "RESUME" for c in res_resume.commands)


def test_preempted_before_move_sent_holds_without_sending_move(cfg):
    sc = seg_cfg(cfg)
    sm = SegmentManager(sc)
    # A granted but departed=False (MOVE not sent yet)
    sm.request(A, "A", 1, 0.0)
    sm.tick(1.0, {A: RobotReport(sc.zones["C"], True, departed=False), B: RobotReport(sc.zones["E"], True)})
    sm.request(B, "B", 3, 1.5)
    res = sm.tick(2.5, {A: RobotReport(sc.zones["C"], True, departed=False), B: RobotReport(sc.zones["E"], True)})
    assert [leg.robot for leg in res.grants] == [B]
    assert sm.stop_lines[A].paused is True  # held before departure
    assert not any(c.robot == A and c.command == "PAUSE" for c in res.commands)  # no PAUSE for unsent MOVE!
    # When B clears CROSSROAD, A re-acquires without sending RESUME (since MOVE was never sent).
    sm.tick(4.0, {A: RobotReport(sc.zones["C"], True, departed=False), B: RobotReport(sc.corridor.crossroad, True, departed=True)})
    sm.tick(5.0, {A: RobotReport(sc.zones["C"], True, departed=False), B: RobotReport(sc.zones["B"], True, departed=True)})
    sm.tick(6.1, {A: RobotReport(sc.zones["C"], True, departed=False), B: RobotReport(sc.zones["B"], True, departed=True)})
    res2 = sm.tick(7.2, {A: RobotReport(sc.zones["C"], True, departed=False), B: RobotReport(sc.zones["B"], True, departed=True)})
    assert A not in sm.stop_lines
    assert not any(c.robot == A and c.command == "RESUME" for c in res2.commands)


def test_held_segment_is_not_taken_by_priority_one_rule(cfg):
    sc = seg_cfg(cfg)
    sm = SegmentManager(sc)
    # A (L2) holds CROSSROAD (not yet entered, far away at D).
    # B (L2, SAME level!) is right next to CROSSROAD inside ROOM_E (just outside DOOR_E, < stop_margin_m = 0.8 m -> Priority 1!).
    # Priority 1 applies ONLY to FREE segments — MUST NOT steal a held segment from A!
    near_cross_b = (-0.70, -2.18)  # in ROOM_E, ~0.20 m from CROSSROAD boundary (< 0.8 m), outside CORRIDOR_BOTTOM!
    assert not sm.segments["CORRIDOR_BOTTOM"].spec.contains(near_cross_b)
    assert not sm.segments["CROSSROAD"].spec.contains(near_cross_b)
    assert 0.0 < sm.segments["CROSSROAD"].spec.distance(near_cross_b) < sc.traffic.stop_margin_m
    sm.request(A, "C", 2, 0.0)
    sm.tick(1.0, {A: RobotReport(sc.zones["D"], True, departed=True), B: RobotReport(near_cross_b, True)})
    assert A in sm.segments["CROSSROAD"].holders
    sm.request(B, "B", 2, 1.5)  # same Level 2, inside stop_margin_m!
    res = sm.tick(2.5, {A: RobotReport(sc.zones["D"], True, departed=True), B: RobotReport(near_cross_b, True)})
    assert not res.grants
    assert A in sm.segments["CROSSROAD"].holders
    assert B not in sm.segments["CROSSROAD"].holders


# --- 6. Fixed 6x6 x (slot 1, slot 2) route segment table matching route_planner.waypoints --------

def test_fixed_6x6_slot1_slot2_segment_routes_match_waypoints(cfg):
    sc = seg_cfg(cfg)
    smap = SegmentMap(sc)
    zones = ("A", "B", "C", "D", "E", "F")
    hall_of = {"A": "HALL_TOP", "D": "HALL_TOP", "C": "HALL_BOTTOM", "F": "HALL_BOTTOM"}
    door_of = {"B": "DOOR_B", "E": "DOOR_E"}
    room_of = {"B": "ROOM_B", "E": "ROOM_E"}

    checked = 0
    for src in zones:
        for dst in zones:
            for dst_slot in (1, 2):
                checked += 1
                start_pose = sc.zones[src]
                path = smap.path(start_pose, dst, dst_slot)
                assert path[0] == f"{src}1"
                assert path[-1] == f"{dst}{dst_slot}"

                # Build exact expected segment list from the known hospital topology.
                if src == dst:
                    if dst_slot == 1:
                        expected = (f"{src}1",)
                    elif src in sc.exit_blocked_zones:
                        expected = (f"{src}1", f"{src}2")
                    else:
                        hub = hall_of.get(src, room_of.get(src))
                        expected = (f"{src}1", hub, f"{src}2")
                else:
                    out_prefix = [f"{src}1"]
                    if src in sc.exit_blocked_zones:
                        out_prefix.append(f"{src}2")
                    in_suffix = [f"{dst}{dst_slot}"]
                    if dst in sc.exit_blocked_zones and dst_slot == 1:
                        in_suffix.insert(0, f"{dst}2")

                    src_hub = hall_of.get(src, room_of.get(src))
                    dst_hub = hall_of.get(dst, room_of.get(dst))
                    if src_hub == dst_hub:
                        middle = [src_hub]
                    else:
                        # Map hubs through corridor halves / crossroad / doors.
                        to_cross = {
                            "HALL_TOP": ["HALL_TOP", "CORRIDOR_TOP"],
                            "HALL_BOTTOM": ["HALL_BOTTOM", "CORRIDOR_BOTTOM"],
                            "ROOM_B": ["ROOM_B", "DOOR_B"],
                            "ROOM_E": ["ROOM_E", "DOOR_E"],
                        }
                        from_cross = {
                            "HALL_TOP": ["CORRIDOR_TOP", "HALL_TOP"],
                            "HALL_BOTTOM": ["CORRIDOR_BOTTOM", "HALL_BOTTOM"],
                            "ROOM_B": ["DOOR_B", "ROOM_B"],
                            "ROOM_E": ["DOOR_E", "ROOM_E"],
                        }
                        middle = [*to_cross[src_hub], "CROSSROAD", *from_cross[dst_hub]]
                    expected = tuple(out_prefix + middle + in_suffix)
                assert path == expected, (src, dst, dst_slot, path, expected)

                # Cross-check against actual PART 04 waypoints: every narrow corridor/door/crossroad segment
                # touched by waypoints MUST match the non-slot narrow segments in path!
                pts = [start_pose, *waypoints(sc, start_pose, dst, dst_slot)]
                touched_narrow = set()
                for p0, p1 in zip(pts, pts[1:]):
                    steps = max(2, int(max(abs(p1[0] - p0[0]), abs(p1[1] - p0[1])) / 0.02))
                    for i in range(steps + 1):
                        t = i / steps
                        xy = (p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t)
                        for seg_name in ("CORRIDOR_TOP", "CORRIDOR_BOTTOM", "CROSSROAD", "DOOR_B", "DOOR_E"):
                            if smap.segments[seg_name].contains(xy):
                                touched_narrow.add(seg_name)
                path_narrow = {n for n in path if n in {"CORRIDOR_TOP", "CORRIDOR_BOTTOM", "CROSSROAD", "DOOR_B", "DOOR_E"}}
                assert touched_narrow == path_narrow, (src, dst, dst_slot, touched_narrow, path_narrow)
                if src in ("B", "E") or dst in ("B", "E"):
                    if src != dst:
                        assert "CROSSROAD" in path
                        if src in door_of:
                            assert door_of[src] in path
                        if dst in door_of:
                            assert door_of[dst] in path
    assert checked == 72

    # Dock path stops at hall (no destination slot segment).
    dock_path = smap.path(sc.zones["C"], "D", 1, dock=True)
    assert dock_path[-1] == "HALL_TOP"
    assert "D1" not in dock_path


# --- 7. Follow-gap actual corridor-axis position (negative gap flips immediately) ----------------

def test_follow_gap_uses_actual_corridor_position_not_grant_order(cfg):
    sc = seg_cfg(cfg)
    sm = SegmentManager(sc)
    # A granted first (D->C, dir +1), then B granted second (D->F, dir +1) while A already moved past CROSSROAD.
    # Then swap their physical corridor positions so B is actually AHEAD of A (gap < 0 if A were assumed front)!
    sm.request(A, "C", 2, 0.0)
    sm.tick(1.0, {A: RobotReport(sc.zones["D"], True, departed=True), B: RobotReport(sc.zones["A"], True)})
    for t, pos in [(2.0, (-1.5, -1.0)), (3.0, (-1.5, -2.18)), (4.0, (-1.65, -3.5)), (5.2, (-1.65, -3.6))]:
        sm.tick(t, {A: RobotReport(pos, True, departed=True), B: RobotReport(sc.zones["A"], True)})
    sm.request(B, "F", 2, 5.2)
    sm.tick(6.0, {A: RobotReport((-1.65, -3.6), True, departed=True), B: RobotReport(sc.zones["A"], True)})
    # Now physically put B ahead of A along TOP->BOTTOM (+1) within follow_gap_m (0.7 m apart)!
    # B at y=-3.9 (more positive along TOP->BOTTOM), A at y=-3.2 (behind B).
    res = sm.tick(7.0, {A: RobotReport((-1.65, -3.2), True, departed=True), B: RobotReport((-1.65, -3.9), True, departed=True)})
    assert sm.follow is not None
    assert sm.follow.front == B and sm.follow.rear.robot == A  # actual position: B is front, A is rear!
    assert any(c.robot == A and c.command == "PAUSE" for c in res.commands)
    assert not any(c.robot == B and c.command == "PAUSE" for c in res.commands)

    # If A physically overtakes B (gap becomes negative), front/rear flips immediately:
    # A is released (RESUME), and since B is now 0.5 m behind A (<= follow_gap_m), B becomes rear and pauses!
    res2 = sm.tick(7.2, {A: RobotReport((-1.65, -4.0), True, departed=True, nav_paused=True), B: RobotReport((-1.65, -3.5), True, departed=True)})
    assert any(c.robot == A and c.command == "RESUME" for c in res2.commands)
    assert sm.follow is not None and sm.follow.front == A and sm.follow.rear.robot == B
    assert any(c.robot == B and c.command == "PAUSE" for c in res2.commands)


# --- 8. Mid-corridor cancel, return_after_cancel, UNKNOWN clear ----------------------------------

def test_mid_corridor_cancel_exits_narrow_segments_then_cancels_and_returns_home(cfg):
    sim = KinematicSim(cfg, mode="SEGMENTS")
    try:
        ref = sim.submit_patient("C", "A", level=2, ref="m1")
        # Run until A is physically inside CORRIDOR_TOP (heading D->C).
        while sim.now < 30.0:
            sim.step(0.2)
            if A in sim.core.resources.segments["CORRIDOR_TOP"].holders and sim.core.resources.segments["CORRIDOR_TOP"].holders[A].state == "OCCUPIED":
                break
        assert A in sim.core.resources.segments["CORRIDOR_TOP"].holders
        mid = sim.ids[ref]

        # Cancel mid-corridor: MUST succeed (no E-423), mission immediately CANCELED, screen text shown!
        sim.cancel(mid)
        out = sim.step(0.2, validate_wire=True)
        ack = next(a for a in out.acks if a["ok"])
        assert ack["ok"] is True
        assert sim.core.history[mid]["status"] == "CANCELED"
        assert sim.core.mission.robots[A].exiting_corridor is True
        assert sim.core.wait_reason(A) == "취소됨 · 복도를 빠져나온 뒤 정지"
        snap = sim.core.snapshot(sim.now)
        assert snap["robots"][A]["canceling_exit"] is True
        # Destination slots beyond the narrow exit chain (C2, C1) must already be released immediately,
        # while the narrow exit chain stays reserved at Level 1 until exited!
        assert A not in sim.core.resources.segments["C2"].holders
        assert A not in sim.core.resources.segments["C1"].holders

        # Run until A finishes exiting narrow segments, sends CANCEL, and auto-returns to home D (Level 1)!
        sim.run_until(sim.now + 80.0, auto_confirm=False)
        cancel_cmds = [cmd for _, r, cmd in sim.all_commands if r == A and cmd == "CANCEL"]
        assert len(cancel_cmds) >= 1
        assert sim.core.mission.robots[A].exiting_corridor is False
        assert sim.core.mission.robots[A].zone == "D"
        assert sim.core.mission.robots[A].state == "IDLE"
    finally:
        sim.close()


def test_cancel_before_entering_corridor_releases_all_unentered_immediately(cfg):
    sim = KinematicSim(cfg, mode="SEGMENTS")
    try:
        ref = sim.submit_patient("C", "A", level=2, ref="m0")
        # Step until granted & MOVING in HALL_TOP, before entering CORRIDOR_TOP.
        sim.run_until(2.0, auto_confirm=False)
        assert A in sim.core.resources.segments["CORRIDOR_TOP"].holders
        assert sim.core.resources.segments["CORRIDOR_TOP"].holders[A].state == "GRANTED"
        sim.cancel(sim.ids[ref])
        sim.step(0.2)
        # All unentered narrow segments released immediately on cancel!
        for seg_name in ("CORRIDOR_TOP", "CROSSROAD", "CORRIDOR_BOTTOM", "C2", "C1"):
            assert A not in sim.core.resources.segments[seg_name].holders
    finally:
        sim.close()


def test_cancel_queued_mission_replaces_return_after_cancel(cfg):
    sim = KinematicSim(cfg, mode="SEGMENTS")
    try:
        # Move A to C first via PATIENT_TRANSPORT, arrive and wait for confirm, queue second patient mission, then cancel first!
        r1 = sim.submit_patient("C", "B", level=2, ref="p1")
        sim.run_until(45.0, auto_confirm=False)
        assert sim.core.mission.robots[A].state == "WAIT_CONFIRM"
        r2 = sim.submit_patient("A", "C", level=2, ref="p2")
        sim.step(0.2)
        sim.cancel(sim.ids[r1])
        sim.step(0.2)
        sim.step(0.2)
        # Robot A should take queued mission p2 directly instead of RETURN_HOME!
        assert sim.core.mission.robots[A].job is not None
        assert sim.core.mission.robots[A].job.id == sim.ids[r2]
    finally:
        sim.close()


def test_unknown_on_link_loss_and_operator_clear_in_segments(cfg):
    sim = Simulation(seg_cfg(cfg), slots=True)
    try:
        sim.cmd("GO_ZONE", robot=A, zone="C")
        sim.tick(0.0)
        assert wire(sim.tick(1.0))[A].startswith("MOVE C ")
        sim.event(A, "MISSION_ACCEPTED")
        sim.frames[A]["pose"] = sim.pose((-1.5, -1.0))  # inside CORRIDOR_TOP
        sim.tick(2.0)
        assert sim.core.resources.segments["CORRIDOR_TOP"].holders[A].state == "OCCUPIED"
        # Drop A's STATE updates beyond link_timeout_s (3.0 s)
        sim.frames.pop(A)
        out = sim.tick(6.0)
        assert sim.core.resources.segments["CORRIDOR_TOP"].state == "UNKNOWN"
        assert out.state["traffic"]["segments"]["CORRIDOR_TOP"]["state"] == "UNKNOWN"
        assert out.state["resources"]["CORRIDOR"]["state"] == "UNKNOWN"
        # Operator clears CORRIDOR_TOP
        sim.cmd("RESOURCE_CLEAR_CONFIRM", resource="CORRIDOR_TOP")
        out2 = sim.tick(6.2)
        assert any(a["ok"] for a in out2.acks)
        assert sim.core.resources.segments["CORRIDOR_TOP"].state == "FREE"
    finally:
        sim.core.store.close()


# --- 9. Config defaults, schemas, preflight, and fleet state snapshot ----------------------------

def test_config_default_is_legacy_and_preflight_reports_mode(cfg):
    assert cfg.traffic.mode == "LEGACY"
    assert cfg.traffic.stop_margin_m == pytest.approx(0.8)
    assert cfg.effective_return_after_cancel is False
    sc = seg_cfg(cfg)
    assert sc.effective_return_after_cancel is True
    assert "LEGACY" in traffic_mode_line(cfg)
    assert "SEGMENTS" in traffic_mode_line(sc)
    assert "0.8 m" in traffic_mode_line(sc)

    # FleetCore snapshot validates against schemas in SEGMENTS mode and fills legacy resources.
    sim = Simulation(sc, slots=True)
    try:
        out = sim.tick(0.0)
        snap = out.state
        assert snap["traffic"]["mode"] == "SEGMENTS"
        assert set(snap["resources"]) == {"CORRIDOR", "ROOM_B", "ROOM_E"}
        assert "CORRIDOR_TOP" in snap["traffic"]["segments"]
        assert "CROSSROAD" in snap["traffic"]["segments"]
        assert "DOOR_B" in snap["traffic"]["segments"]
        schemas.parse_fleet_state(schemas.dumps(snap))
    finally:
        sim.core.store.close()


def test_config_rejects_invalid_traffic_mode(tmp_path):
    raw = read_config_yaml("src/part3_fleet_manager/config/fleet.yaml")
    raw["traffic"]["mode"] = "BOGUS"
    with pytest.raises(ValueError, match="traffic.mode"):
        config_from_dict(raw)
