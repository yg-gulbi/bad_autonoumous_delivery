"""Stage 4O follow-up 3 (9d1be54 -> ): bed monitor switch, link grace after MOVE/RESUME, the plan's start
permission, WAIT_CONFIRM kept still, the plan's proximity distances, cancel of a MOVE that never arrived, preflight."""
from dataclasses import replace
import json

import pytest

from kinematic_mock import KinematicSim
from part3_fleet_manager.core import schemas
from part3_fleet_manager.core.clearance import resume_distance, stop_distance
from part3_fleet_manager.core.legs import Leg
from part3_fleet_manager.core.robot_protocol import RobotProtocol
from part3_fleet_manager.preflight_checks import running_mode_line, traffic_mode_line

A, B = "robot_a", "robot_b"


# --- 1. bed monitor on/off -------------------------------------------------------------------------------

def _bed_sim(cfg, **kw):
    sim = KinematicSim(cfg, bed_feed=True, require_at_start=True, **kw)
    sim.run_until(1.0)
    return sim


def _kinds(sim, after, *kinds):
    return [e for e in sim.all_events if e["ts"] > after and e["type"] in kinds]


def test_bed_monitor_off_no_stop_no_card_no_alarm_events(cfg):
    sim = _bed_sim(cfg)
    try:
        assert cfg.safety.bed_monitor.enabled is True                 # default: 4M behaviour
        cid = sim.cmd("BED_MONITOR_SET", enabled=False)
        out = sim.step()
        assert next(a for a in out.acks if a["ref"] == cid)["ok"]
        off, = _kinds(sim, 0, "BED_MONITOR_DISABLED")
        assert off["detail"]["by"] == "nurse" and sim.core.snapshot(sim.now)["bed_exit"]["monitor"]["enabled"] is False
        t0 = sim.now
        sim.submit_patient("C", "A", level=2)
        sim.run_until(sim.now + 3.0)
        sim.bed_value = True                                          # the patient gets up
        sim.run_until(sim.now + 3.0)
        sim.bed_feed = False                                          # and the webcam stops
        sim.run_until(sim.now + 8.0)
        assert not sim.core.safety.causes[A]
        assert not _kinds(sim, t0, "BED_EXIT", "BED_EXIT_CLEAR", "BED_MONITOR_WARN", "BED_MONITOR_LOST", "CARD_OPEN", "PAUSE")
        assert sim.core.safety.denial([A], sim.now, sim.core.safety.bed_exit) is None
        assert sim.core.snapshot(sim.now)["bed_exit"]["monitor"]["state"] == "OFF"
        # On again: judged as at start - the webcam is lost and the bed shows an exit, so robot_a stops.
        sim.cmd("BED_MONITOR_SET", enabled=True)
        sim.run_until(sim.now + 0.6)
        on, = _kinds(sim, t0, "BED_MONITOR_ENABLED")
        assert on["detail"]["by"] == "nurse"
        assert _kinds(sim, on["ts"] - 1e-6, "BED_EXIT") and _kinds(sim, on["ts"] - 1e-6, "BED_MONITOR_LOST")
        assert sim.core.safety.causes[A] >= {"BED_EXIT", "BED_MONITOR_LOST"}
        assert sim.core.decisions.cards
    finally:
        sim.close()


def test_bed_monitor_off_closes_the_lost_card_and_keeps_existing_stops(cfg):
    sim = _bed_sim(cfg)
    try:
        sim.submit_patient("C", "A", level=2)
        sim.run_until(sim.now + 2.0)
        sim.bed_feed = False
        sim.run_until(sim.now + 5.0)
        card, = [c for c in sim.core.decisions.cards.values() if c["kind"] == "BED_MONITOR_LOST"]
        assert "BED_MONITOR_LOST" in sim.core.safety.causes[A]
        sim.cmd("BED_MONITOR_SET", enabled=False)
        sim.run_until(sim.now + 0.4)
        assert card["card_id"] not in sim.core.decisions.cards
        # Switching off is not a resume: the stop stays until the operator resumes, which is now allowed.
        assert "BED_MONITOR_LOST" in sim.core.safety.causes[A]
        sim.resume(A)
        sim.run_until(sim.now + 0.4)
        assert not sim.core.safety.causes[A]
        same = sim.cmd("BED_MONITOR_SET", enabled=False)                # already off: nothing recorded
        out = sim.step()
        assert next(a for a in out.acks if a["ref"] == same)["ok"]
        assert len(_kinds(sim, 0, "BED_MONITOR_DISABLED")) == 1
    finally:
        sim.close()


def test_bed_monitor_config_launch_and_schema(cfg):
    assert schemas.COMMAND_FLAGS == {"BED_MONITOR_SET": "enabled"}
    ok = schemas.parse_operator_cmd(json.dumps({"v": 1, "cmd": "BED_MONITOR_SET", "cmd_id": "c", "by": "n", "enabled": False}))
    assert ok["enabled"] is False
    with pytest.raises(schemas.SchemaError, match="enabled"):
        schemas.parse_operator_cmd(json.dumps({"v": 1, "cmd": "BED_MONITOR_SET", "cmd_id": "c", "by": "n"}))
    off = replace(cfg, safety=replace(cfg.safety, bed_monitor=replace(cfg.safety.bed_monitor, enabled=False)))
    sim = KinematicSim(off, require_at_start=True)
    try:
        sim.run_until(5.0)
        snap = sim.core.snapshot(sim.now)
        schemas.parse_fleet_state(schemas.dumps(snap))
        assert snap["bed_exit"]["monitor"]["state"] == "OFF" and not sim.core.decisions.cards
    finally:
        sim.close()


# --- link grace after MOVE / RESUME --------------------------------------------------------------------------

def _state(link, stamp, state="IDLE", mission=None):
    return link.receive(json.dumps({"type": "STATE", "robot_id": link.ns, "mission_id": mission, "state": state,
                                    "pose": {"x": 0.0, "y": 0.0}, "docked": False}), stamp)


def test_link_grace_after_move_and_resume_until_the_next_state(cfg):
    link = RobotProtocol("robot3", cfg.robot_link)
    timeout, grace = cfg.robot_link.link_timeout_s, cfg.robot_link.command_grace_s
    assert (timeout, grace) == (3.0, 5.0)
    _state(link, 10.0)
    assert link.online(13.0) and not link.online(13.01)               # no command: 3 s as before
    link.go("L-1", "C", 1)
    assert link.step(12.0)[0].startswith("MOVE C")
    assert link.online(17.9) and link.online(10.0 + timeout + grace) and not link.online(18.01)
    _state(link, 15.0, "MOVING", "L-1")                               # PART 04 answered: back to 3 s
    assert link.online(18.0) and not link.online(18.01)
    link.state = "PAUSED"
    assert link.step(17.0, resume=True)[0] == "RESUME"
    assert link.online(22.9) and not link.online(23.01)


def test_no_link_loss_while_part04_waits_for_nav2_after_a_move(cfg):
    sim = KinematicSim(cfg)
    try:
        sim.nav_wait(A, 5.0)
        sim.cmd("GO_ZONE", robot=A, zone="C", level=1)
        sim.run_until(20.0)
        assert not [e for e in sim.all_events if e["type"] == "LINK_LOST" and e["ts"] > 0.5]
        assert sim.robots[A].state == "MOVING"
    finally:
        sim.close()


def test_cancel_of_a_move_that_never_arrived_ends_that_move(cfg):
    """The MOVE was lost in a link drop, the CANCEL meets an idle PART 04 (STATE CANCELED after it): the MOVE is
    over. Before, it was sent again after accept_timeout_s (seed 23 of the 4O random test)."""
    link = RobotProtocol("robot3", cfg.robot_link)
    _state(link, 10.0, "CANCELED", "L-0031-2")
    link.go("Y-0-2", "F", 1)
    assert link.step(10.2)[0].startswith("MOVE F")
    link.cancel()
    assert link.cancel_unaccepted and link.step(10.4)[0] == "CANCEL"
    assert _state(link, 10.3, "CANCELED", "L-0031-2") == {"event": "CANCEL_CONFIRMED"}   # stamp before the CANCEL
    assert not link.move.finished                                        # an old STATE cannot end the MOVE
    link.cancel()
    link.step(10.5)
    assert _state(link, 11.0, "CANCELED", "L-0031-2") == {"event": "CANCEL_CONFIRMED"}
    assert link.move.finished and link.step(15.0) == (None, None)


# --- 4. the plan's start permission -----------------------------------------------------------------------------

def test_plan_hold_blocks_resume_and_move_whoever_asks(cfg):
    """A robot the plan holds gets no RESUME (operator resume, card retry) and no MOVE (resend) until the plan
    lets it go; PAUSE and CANCEL still go out."""
    sim = KinematicSim(cfg)
    try:
        pm, link, unit = sim.core.resources, sim.core.links[A], sim.core.mission.robots[A]
        link.state = "PAUSED"
        unit.resume_due, unit.state = True, "MOVING"
        assert sim.core.mission.wire_step(A, sim.now, None, hold_start=True) is None
        assert sim.core.mission.wire_step(A, sim.now, None, hold_start=False) == "RESUME"
        link.state = "IDLE"
        link.go("L-1-1", "C", 1)
        assert link.step(sim.now + 1.0, hold=True) == (None, None)
        assert link.step(sim.now + 1.0)[0].startswith("MOVE C")
        assert sim.core.traffic.may_start(A) == (A in pm.go)
    finally:
        sim.close()


# --- 5. WAIT_CONFIRM is kept still; the plan's proximity distances match collision.py ----------------------------

def test_wait_confirm_robot_is_not_moved_aside(cfg):
    """robot_b waits for its stop confirmation at A2 (the entrance slot of A); robot_a is sent to A. The plan
    does not move robot_b aside (a patient may be getting on); robot_a waits, and goes after the confirmation."""
    sim = KinematicSim(cfg, poses={B: cfg.slot_point("A", 2)})
    try:
        sim.submit_call("A", level=1)
        sim.run_until(2.0, auto_confirm=False)
        unit = sim.core.mission.robots[B]
        assert unit.state == "WAIT_CONFIRM" and sim.core.resources.confirming[B]
        sim.cmd("GO_ZONE", robot=A, zone="A", level=3)
        sim.run_until(60.0, auto_confirm=False)
        assert not [c for t, r, c in sim.all_commands if r == B and t > 1.0]
        assert not [e for e in sim.all_events if e["type"] == "PLAN_YIELD_MOVE" and e["robot"] == B]
        sim.confirm(B)
        sim.run_until(200.0)
        assert sim.core.mission.robots[A].zone == "A" and sim.core.mission.robots[B].zone == "F"
    finally:
        sim.close()


def test_plan_and_proximity_stop_use_one_distance_rule(cfg):
    """clearance.stop_distance (4O 정리): slot_stop_m (0.45 m) for a robot standing still at a slot without a granted
    leg, online or not; 0.6 m for one granted and not started. The plan's standing piece and collision.py agree."""
    sim = KinematicSim(cfg)
    try:
        pm, graph = sim.core.resources, sim.core.resources.graph

        def standing_piece(robot):
            agent = pm._agent(robot, {})
            return graph.pieces[graph.routes[agent.starts[0][0]].park]

        col = sim.core.collision
        assert standing_piece(B).at_slot and col._still_at_slot(B)          # parked at F1
        pm.reports[B] = replace(pm.reports[B], link_ok=False)
        pm.robots.reports[B] = pm.reports[B]
        assert standing_piece(B).at_slot and col._still_at_slot(B)          # offline: still standing there
        pm.reports[B] = replace(pm.reports[B], link_ok=True)
        pm.active[B] = Leg(B, "C", 1, 0.0, "M-0001", False, "CALL")         # granted, MOVE not sent yet
        assert not standing_piece(B).at_slot and not col._still_at_slot(B)
        del pm.active[B]
        safety = cfg.safety
        assert (stop_distance(safety, False, False), resume_distance(safety, False, False)) == (0.6, 0.9)
        assert stop_distance(safety, True, False) == 0.45 and abs(resume_distance(safety, False, True) - 0.75) < 1e-9
        moving = graph.pieces[graph.routes[graph.route_from(cfg.slot_point("F", 2), "D1")].pieces[0]]
        for parked, limit in ((graph.park["F1"], cfg.safety.slot_stop_m),
                              (graph.parked("F1", slot_rule=False), cfg.safety.proximity_stop_m)):
            assert graph.threshold(parked, moving.id) == limit
    finally:
        sim.close()


def test_leg_start_of_an_aside_move_names_the_slot_it_goes_to(cfg):
    sim = KinematicSim(cfg)
    try:
        pm, link, unit = sim.core.resources, sim.core.links[B], sim.core.mission.robots[B]
        sim.submit_call("E", level=1)
        sim.run_until(1.0)
        old = pm.active.pop(B, None) or pm.pending.pop(B)
        leg = replace(old, destination="C")                                 # a leg to C, stopped at E2 first
        link.go("L-9-9", "E", 1, slot=2)
        link.move.attempts, unit.state, pm.active[B] = 1, "SENDING", leg
        sim.core.mission.received(B, {"event": "MISSION_ACCEPTED"}, sim.now)
        start = [e for e in pm.events if e["type"] == "LEG_START"][-1]
        assert start["detail"]["destination"] == "C" and start["detail"]["target"] == "E"
        assert start["detail"]["target_slot"] == 2 and start["detail"]["aside"] is True
        assert start["detail"]["reason"].startswith("101호 병실(E) 2번 자리 이동 시작 (비켜서기, 목적지 수술실(C)")
    finally:
        sim.close()


# --- preflight ---------------------------------------------------------------------------------------------------

def test_preflight_names_the_traffic_judgement_and_an_old_running_control_room(cfg):
    assert traffic_mode_line(cfg).startswith("교통 판단 · PLAN (정지선 0.8 m") and traffic_mode_line(cfg).endswith("· fleet.yaml")
    assert "같음" in running_mode_line("PLAN")
    assert running_mode_line("LEGACY").startswith("경고 · 실행 중인 관제는 LEGACY입니다. 4O 정리 이전 관제")
    assert "관제 실행 전" in running_mode_line(None)


def test_failed_state_stands_for_a_lost_mission_failed(cfg):
    """Nav2 gave up while the link dropped (random seed 235): the STATUS MISSION_FAILED was lost and only STATE
    FAILED came. As 4K for ARRIVED, that STATE is the failure if no MISSION_FAILED comes in the same batch."""
    link = RobotProtocol("robot3", cfg.robot_link)
    _state(link, 10.0)
    link.go("L-1", "C", 1)
    link.step(10.1)
    link.receive(json.dumps({"type": "STATUS", "event": "MISSION_ACCEPTED", "robot_id": "robot3", "mission_id": "L-1",
                             "destination": "C"}), 10.2)
    assert _state(link, 11.0, "MOVING", "L-1") is None and link.settle() is None
    assert _state(link, 15.0, "FAILED", "L-1") is None
    failed = link.settle()
    assert failed["event"] == "MISSION_FAILED" and failed["from_state"] and "상태 보고로 확인" in failed["reason"]
    assert link.move.finished and link.settle() is None
    # When the STATUS does come in the same batch, it wins and the STATE adds nothing.
    other = RobotProtocol("robot3", cfg.robot_link)
    _state(other, 10.0)
    other.go("L-2", "C", 1)
    other.step(10.1)
    other.receive(json.dumps({"type": "STATUS", "event": "MISSION_ACCEPTED", "robot_id": "robot3", "mission_id": "L-2",
                              "destination": "C"}), 10.2)
    _state(other, 15.0, "FAILED", "L-2")
    assert other.receive(json.dumps({"type": "STATUS", "event": "MISSION_FAILED", "robot_id": "robot3",
                                     "mission_id": "L-2", "reason": "Nav2: ABORTED"}), 15.0)["event"] == "MISSION_FAILED"
    assert other.settle() is None
