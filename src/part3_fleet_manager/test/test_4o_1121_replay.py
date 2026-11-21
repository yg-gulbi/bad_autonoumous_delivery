"""Stage 4O follow-up 3: the three PLAN scenes of the 11/21 field record (bad_real.db, 12:33 on = PLAN).

- 12:35:05 robot_a resumed at the crossroad stop line; PART 04 sat in _go() -> wait_for_server holding its lock, so
  no STATE came for ~4 s: LINK_LOST, and its four held segments (CORRIDOR_TOP, CROSSROAD, DOOR_E, E1) stayed UNKNOWN
  until the operator checked them at 12:44. 12:35:59 the same for robot_b standing in E2 (1 s loss).
- 12:38:46~12:39:51 with those segments UNKNOWN robot_a went C2 <-> F2 seven times and robot_b never moved: the plan
  took an UNKNOWN segment for "robot_a is still there".
- 12:43:25 robot_b was resumed by the operator inside room E; the plan sent it aside to E2 and PART 04 drove that MOVE
  through the crossroad (every MOVE to B or E goes to the crossroad first), where robot_a was coming up: 0.54 m.
"""
from dataclasses import replace
from math import dist

from kinematic_mock import KinematicSim
from part3_fleet_manager.core.segments import SegmentHolding

A, B = "robot_a", "robot_b"
FIELD_UNKNOWN = ("CORRIDOR_TOP", "CROSSROAD", "DOOR_E", "E1")


def _events(sim, kind, robot=None):
    """Events after the start (both robots come online at t = 0 with a LINK_BACK)."""
    return [e for e in sim.all_events if e["type"] == kind and (robot is None or e["robot"] == robot) and e["ts"] > 0.5]


def _unknown(sim):
    return {n: sorted(s.uncertain) for n, s in sim.core.resources.segments.items() if s.uncertain}


def _scene_1235(cfg, grace):
    """robot_a (L1) D1 -> E, robot_b (L3) F1 -> E2 a second later: robot_a waits for robot_b before the
    crossroad. Its RESUME then meets PART 04's Nav2 server wait (4 s without STATE)."""
    cfg = replace(cfg, robot_link=replace(cfg.robot_link, command_grace_s=grace))
    sim = KinematicSim(cfg, mode="PLAN")
    sim.cmd("GO_ZONE", robot=A, zone="E", level=1)
    sim.run_until(0.5)
    sim.cmd("GO_ZONE", robot=B, zone="E", level=3)
    resumed = None
    while sim.now < 80:
        out = sim.step(0.1)
        for c in out.commands:
            if c.robot == A and c.command == "PAUSE":
                sim.nav_wait(A, 4.0)              # the RESUME after this stop waits 4 s for Nav2
            if c.robot == A and c.command == "RESUME" and resumed is None:
                resumed = sim.now
    return sim, resumed


def test_1235_resume_during_part04_nav2_wait_is_not_a_link_loss(cfg):
    sim, resumed = _scene_1235(cfg, cfg.robot_link.command_grace_s)
    try:
        assert cfg.robot_link.command_grace_s == 5.0 and resumed is not None
        assert not _events(sim, "LINK_LOST") and not _events(sim, "RESOURCE_UNKNOWN")
        assert sim.core.mission.robots[A].zone == "E" and sim.core.mission.robots[B].zone == "E"
        assert not _unknown(sim)
    finally:
        sim.close()


def test_1235_without_the_grace_the_same_four_segments_go_unknown_and_come_back_by_themselves(cfg):
    """The field record again (command_grace_s 0 = the 3 s timeout of 11/21): the loss is short and robot_a has not
    moved, so at LINK_BACK it keeps only what it is inside and the rest is released without the operator."""
    sim, resumed = _scene_1235(cfg, 0.0)
    try:
        lost, = _events(sim, "LINK_LOST", A)
        back, = _events(sim, "LINK_BACK", A)
        assert 0 < lost["ts"] - resumed <= cfg.robot_link.link_timeout_s and back["ts"] - lost["ts"] < 2.0
        unknown = {n for e in _events(sim, "RESOURCE_UNKNOWN", A) for n in e["detail"]["resources"]}
        assert unknown == set(FIELD_UNKNOWN)
        auto = [e for e in _events(sim, "RESOURCE_RELEASE", A) if e["detail"].get("auto")]
        assert {n for e in auto for n in e["detail"]["resources"]} == set(FIELD_UNKNOWN)
        assert all(e["ts"] == back["ts"] and e["detail"]["moved_m"] < 0.05 for e in auto)
        assert not _events(sim, "RESOURCE_CLEAR_CONFIRM") and not _unknown(sim)
        assert sim.core.mission.robots[A].zone == "E"
    finally:
        sim.close()


def _standing_drop(cfg, seconds):
    """robot_b stands in E2 (12:35:59) and loses the link."""
    sim = KinematicSim(cfg, mode="PLAN", poses={B: cfg.slot_point("E", 2)})
    sim.run_until(2.05)
    board = sim.core.resources.segments
    assert B in board["E2"].holders and board["E2"].holders[B].state == "OCCUPIED"
    sim.drop_link(B, seconds)
    sim.run_until(sim.now + seconds + 3.0)
    return sim, board


def test_1235_short_link_loss_of_a_standing_robot_keeps_its_slot_without_an_operator_check(cfg):
    sim, board = _standing_drop(cfg, 4.0)          # STATE gap 4~5 s: LINK_LOST for about a second
    try:
        lost, = _events(sim, "LINK_LOST", B)
        back, = _events(sim, "LINK_BACK", B)
        assert back["ts"] - lost["ts"] <= 2.0
        assert [e["detail"]["resources"] for e in _events(sim, "RESOURCE_UNKNOWN", B)] == [["E2"]]
        assert board["E2"].state == "OCCUPIED" and B in board["E2"].holders and not board["E2"].uncertain
        assert not _events(sim, "RESOURCE_RELEASE", B)       # it is still inside: nothing to release
    finally:
        sim.close()


def test_long_link_loss_still_needs_the_operator_check(cfg):
    sim, board = _standing_drop(cfg, 8.0)          # offline ~5 s: 5 s x 0.2 m/s = 1.0 m > stop_margin_m 0.8 m
    try:
        lost, = _events(sim, "LINK_LOST", B)
        back, = _events(sim, "LINK_BACK", B)
        assert (back["ts"] - lost["ts"]) * 0.2 > cfg.traffic.stop_margin_m
        check = _events(sim, "RESOURCE_UNKNOWN", B)[-1]
        assert check["ts"] == back["ts"] and "현장 확인" in check["detail"]["reason"]
        assert _unknown(sim) == {"E2": [B]}
        sim.cmd("RESOURCE_CLEAR_CONFIRM", resource="E2")
        sim.run_until(sim.now + 0.4)
        assert not _unknown(sim) and board["E2"].holders[B].state == "OCCUPIED"
    finally:
        sim.close()


def _scene_1238(cfg):
    """robot_a stands in F2 (back online), CORRIDOR_TOP·CROSSROAD·DOOR_E·E1 are still UNKNOWN for it (no operator
    check yet), robot_b stands in E2 and gets GO_ZONE A (M-0072, Level 1)."""
    sim = KinematicSim(cfg, mode="PLAN", poses={A: cfg.slot_point("F", 2), B: cfg.slot_point("E", 2)})
    sim.run_until(1.0)
    board = sim.core.resources.segments
    for name in FIELD_UNKNOWN:
        board[name].holders[A] = SegmentHolding(entered_at=0.5)
        board[name].uncertain.add(A)
    sim.core.resources._mark("점유 확인")
    sim.cmd("GO_ZONE", robot=B, zone="A", level=1)
    return sim


def test_1238_unknown_segments_are_a_fence_and_nobody_moves_aside(cfg):
    sim = _scene_1238(cfg)
    try:
        sim.run_until(70.0)
        pm = sim.core.resources
        assert not [c for _, r, c in sim.all_commands if r == A]          # robot_a never drives (field: 7 times)
        assert not _events(sim, "PLAN_YIELD_MOVE")
        assert dist(sim.robots[A].pose, cfg.slot_point("F", 2)) < 1e-6
        assert not [c for _, r, c in sim.all_commands if r == B and c.startswith("MOVE")]
        reason = sim.core.wait_reason(B)
        assert reason.endswith(": 현장 확인 필요") and "교차로" in reason and "101호 병실(E) 문" in reason
        assert pm.fenced[B] and set(pm.fenced[B]) <= set(FIELD_UNKNOWN)
        waits = {e["detail"]["reason"] for e in _events(sim, "PLAN_WAIT", B)}
        assert waits == {reason}
        # The same state planned again gives the same instruction (and no "late" replans while it waits).
        updates = _events(sim, "PLAN_UPDATE")
        assert len(updates) <= 3 and all(e["detail"]["unknown"] == {B: list(pm.fenced[B])} for e in updates[-1:])
        for _ in range(5):
            pm._mark("같은 상태")
            sim.step(0.2)
            assert not pm.plans[B].legs and pm.plans[B].failed and A not in pm.plans or not pm.plans[A].legs
            assert pm.held[B]["reason"] == reason and A not in pm.held
        # The operator checks the four segments: robot_b goes, robot_a stays where it is.
        for name in FIELD_UNKNOWN:
            sim.cmd("RESOURCE_CLEAR_CONFIRM", resource=name)
        sim.run_until(sim.now + 60.0)
        assert sim.core.mission.robots[B].zone == "A" and not [c for _, r, c in sim.all_commands if r == A]
        assert not _events(sim, "PROXIMITY_STOP")
    finally:
        sim.close()


def _scene_1243(cfg, resume_after):
    """robot_b leaves E2 for F (its return home), the plan stops it in room E and the operator pauses it; robot_a
    (Level 2) goes C2 -> A2 up through the crossroad; the operator resumes robot_b ``resume_after`` s later."""
    sim = KinematicSim(cfg, mode="PLAN", poses={A: cfg.slot_point("C", 2), B: cfg.slot_point("E", 2)})
    sim.run_until(1.0)
    sim.cmd("GO_ZONE", robot=B, zone="F", level=1)
    paused = resumed = None
    log = {"min_m": 9.0, "both_in_crossroad": 0, "go_missing": [], "per_tick": [], "resume_held": []}
    crossroad = sim.core.resources.segments["CROSSROAD"].spec
    while sim.now < 120:
        if paused is None and sim.robots[B].state == "MOVING" and dist(sim.robots[B].pose, cfg.slot_point("E", 2)) > 0.25:
            sim.pause(B)
            sim.cmd("GO_ZONE", robot=A, zone="A", level=2)
            paused = sim.now
        if paused is not None and resumed is None and sim.now >= paused + resume_after:
            sim.resume(B)
            resumed = sim.now
        out = sim.step(0.1)
        pm = sim.core.resources
        log["min_m"] = min(log["min_m"], dist(sim.robots[A].pose, sim.robots[B].pose))
        log["both_in_crossroad"] += all(crossroad.contains(sim.robots[r].pose) for r in (A, B))
        moves = [c for c in out.commands if c.command.startswith(("MOVE", "DOCK"))]
        log["per_tick"].append(len(moves) - len({c.robot for c in moves}))
        for c in out.commands:
            if c.command.startswith(("MOVE", "DOCK", "RESUME")) and c.robot not in pm.go:
                log["go_missing"].append((sim.now, c.robot, c.command))
            if c.robot == B and c.command == "RESUME" and B in pm.held:
                log["resume_held"].append(sim.now)
    return sim, resumed, log


def test_1243_part04_goes_through_the_crossroad_even_inside_room_e(cfg):
    """The plan's route for a MOVE to E2 from inside room E is PART 04's: out to the crossroad and back."""
    sim = KinematicSim(cfg, mode="PLAN")
    try:
        graph = sim.core.resources.graph
        inside = (-0.52, -2.67)                      # where robot_b stood at 12:43 (a little out of E2)
        route = graph.routes[graph.route_from(inside, "E2")]
        points = [graph.pieces[p].point for p in route.pieces]
        assert "CROSSROAD" in points and points.count("DOOR_E") >= 1
        assert "CROSSROAD" in [graph.pieces[p].point for p in graph.routes[graph.from_endpoint["E1"]["E2"]].pieces]
        # Past the crossroad (VIA_PASSED) a robot coming in goes straight on to its slot.
        assert "CROSSROAD" not in [graph.pieces[p].point
                                   for p in graph.routes[graph.route_from(inside, "E2", via_done=True)].pieces]
    finally:
        sim.close()


def test_1243_operator_resume_keeps_the_plan_wait_and_nobody_meets_in_the_crossroad(cfg):
    for resume_after in (3.0, 6.0, 8.9, 12.0):       # 8.9 s gave the field's 0.59 m with the old route model
        sim, resumed, log = _scene_1243(cfg, resume_after)
        try:
            assert resumed is not None
            assert not _events(sim, "PROXIMITY_STOP"), resume_after
            assert log["min_m"] >= cfg.safety.proximity_stop_m and log["both_in_crossroad"] == 0
            assert not log["go_missing"] and not log["resume_held"] and not any(log["per_tick"])
            # The operator resume only lifted the operator stop: robot_b's next motion is the plan's.
            first = next(t for t, r, c in sim.all_commands if r == B and t >= resumed and c != "PAUSE")
            wait = [e for e in _events(sim, "PLAN_WAIT", B) if resumed <= e["ts"] <= first]
            assert first > resumed + 0.05 and wait
            # An aside MOVE (if any) is told as such: LEG_START names the slot it goes to and the leg's goal.
            for e in _events(sim, "LEG_START", B):
                if e["detail"].get("aside"):
                    assert e["detail"]["target"] != e["detail"]["destination"] and "비켜서기" in e["detail"]["reason"]
            assert sim.core.mission.robots[A].zone == "A" and sim.core.mission.robots[B].zone == "F"
        finally:
            sim.close()
