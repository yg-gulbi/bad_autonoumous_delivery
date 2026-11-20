"""Field traffic regression cases (BRIEF 44–49)."""
from dataclasses import replace

import pytest

from fleet_simulation import Simulation
from part3_fleet_manager.core.resource_manager import MotionRequest, ResourceManager, RobotReport

A, B = "robot_a", "robot_b"


def manager_for(cfg, mode, room_mode="DOOR"):
    return ResourceManager(replace(cfg, corridor=replace(cfg.corridor, mode=mode, room_mode=room_mode)))


@pytest.mark.parametrize("origins,goals", [
    (("D", "A"), ("C", "F")), (("D", "A"), ("C", "E")), (("C", "F"), ("D", "B")),
])
def test_44_same_heading_shares_including_room_door(cfg, origins, goals):
    rm = manager_for(cfg, "DIRECTIONAL")
    reports = {r: RobotReport(cfg.zones[z], True) for r, z in zip((A, B), origins)}
    for robot, goal in zip((A, B), goals):
        rm.request(robot, goal, 1, 0)
    assert len(rm.tick(1, reports).grants) == 2
    assert rm.snapshot()["CORRIDOR"]["holders"] == [A, B]


@pytest.mark.parametrize("room_mode", ["DOOR", "ROOM"])
@pytest.mark.parametrize("waiting_from,destination", [("F", "A"), ("E", "D"), ("B", "E")])
def test_44_opposing_and_transverse_wait_through_clearance(cfg, waiting_from, destination, room_mode):
    rm = manager_for(cfg, "DIRECTIONAL", room_mode)
    reports = {A: RobotReport(cfg.zones[waiting_from], True), B: RobotReport(cfg.zones["D"], True)}
    occupying = rm.request(B, "C", 1, 0)
    assert rm.tick(1, reports).grants == [occupying]
    waiting = rm.request(A, destination, 3, 1)
    assert not rm.tick(2, reports).grants  # Even an unentered reservation protects its direction.
    decision = rm.last_decision["detail"]
    assert decision["rule"] == "DIRECTION" and {r["robot"] for r in decision["contenders"]} == {A, B}
    if waiting_from != "B":
        assert "반대 방향" in decision["reason"]
    reports[B] = RobotReport((-1.55, -2.3), True)
    assert not rm.tick(3, reports).grants
    assert rm.snapshot()["CORRIDOR"]["holders"] == [B]
    if waiting_from in ("B", "E"):
        # ROOM (4I): the room shelters its robot and stays held. DOOR (4J): no door claim while waiting.
        assert rm.snapshot()["ROOM_" + waiting_from]["holders"] == ([A] if room_mode == "ROOM" else [])
    if destination == "E":
        assert not rm.snapshot()["ROOM_E"]["holders"]  # No partial reservation.
    reports[B] = RobotReport(cfg.zones["C"], True)
    assert not rm.tick(4, reports).grants
    assert not rm.tick(5, reports).grants
    assert rm.tick(6, reports).grants == [waiting]
    assert all(not e["type"].startswith("CORRIDOR_YIELD") for e in rm.events)


@pytest.mark.parametrize("origin,goal", [("B", "E"), ("E", "B")])
def test_44_transverse_holder_excludes_other_travel(cfg, origin, goal):
    rm = manager_for(cfg, "DIRECTIONAL")
    reports = {A: RobotReport(cfg.zones[origin], True), B: RobotReport(cfg.zones["D"], True)}
    crossing = rm.request(A, goal, 1, 0)
    assert rm.tick(1, reports).grants == [crossing]
    rm.request(B, "C", 3, 1)
    assert not rm.tick(2, reports).grants
    assert rm.last_decision["detail"]["rule"] == "DIRECTION"


def test_44_two_already_inside_use_yield_fallback(cfg):
    rm = manager_for(cfg, "DIRECTIONAL")
    reports = {A: RobotReport((-1.55, -1.8), True), B: RobotReport((-1.55, -2.6), True)}
    rm.tick(0, reports)
    rm.request(A, "C", 1, 0)
    rm.request(B, "D", 3, 0)
    result = rm.tick(1, reports)
    assert len(result.grants) == 2 and result.commands == [MotionRequest(A, "PAUSE")]
    assert rm.robot_state(A, "MOVING") == "GIVE_WAY"


@pytest.mark.parametrize("distance,targets", [(0.8, [A, B]), (1.0, [A, B]), (1.01, [A])])
def test_45_bumper_stops_neighbors_until_individually_resumed(cfg, distance, targets):
    cfg = replace(cfg, safety=replace(cfg.safety, use_hazard_topic=True))
    sim = Simulation(replace(cfg, corridor=replace(cfg.corridor, mode="DIRECTIONAL"), db_path=":memory:"))
    try:
        sim.frames[B]["pose"] = sim.pose("A")
        for r, dest in ((A, "C"), (B, "F")):
            sim.cmd("GO_ZONE", robot=r, zone=dest)
        sim.tick(0)
        assert len(sim.tick(1).commands) == 2
        for robot in (A, B):
            sim.event(robot, "MISSION_ACCEPTED")
        sim.frames[A]["pose"] = sim.pose((-1.50, -1.75))
        sim.frames[B]["pose"] = sim.pose((-1.50, -1.75 - distance))
        sim.pending.append(("hazards", ["BUMP"], A))
        out = sim.tick(2)
        bump, = [e for e in out.events if e["type"] == "BUMP"]
        assert bump["robot"] == A and bump["detail"]["targets"] == targets
        assert out.commands == [MotionRequest(r, "PAUSE") for r in targets]
        assert {r for r in (A, B) if sim.state(r) == "SAFE_PAUSE"} == set(targets)
        for r in targets:
            sim.event(r, "PAUSED")
        sim.pending.append(("hazards", ["BUMP"], A))
        sim.cmd("RESUME_ALL")
        held = sim.tick(2.1)
        assert not held.commands and not any(e["type"] == "BUMP" for e in held.events)
        for i, r in enumerate(reversed(targets)):
            sim.cmd("RESUME_ROBOT", robot=r)
            assert sim.tick(2.2 + i).commands == [MotionRequest(r, "RESUME")]
            if r == B:
                assert sim.state(A) == "SAFE_PAUSE"
                sim.event(B, "RESUMED")
                sim.frames[B]["pose"]["y"] -= 0.7  # clear the independent 1.4 m following hold
    finally:
        sim.core.store.close()


@pytest.mark.parametrize("mode,expected", [("DIRECTIONAL", 1), ("SHARED_YIELD", 2), ("EXCLUSIVE", 1)])
def test_46_arbitration_uses_selected_mode(cfg, mode, expected):
    rm = manager_for(cfg, mode)
    reports = {A: RobotReport(cfg.zones["D"], True), B: RobotReport(cfg.zones["F"], True)}
    rm.request(A, "C", 1, 0)
    rm.request(B, "A", 1, 0)
    assert len(rm.tick(1, reports).grants) == expected


def test_last_tie_uses_config_order_for_dispatch_yield_and_deadlock(cfg):
    from part3_fleet_manager.core.deadlock import DeadlockManager

    # Reverse the YAML mapping, keeping all IDs, labels and namespaces the same.
    cfg = replace(cfg, robots={B: cfg.robots[B], A: cfg.robots[A]}, assignment=replace(cfg.assignment, mode="IDLE_BEST_BATTERY"),
                  corridor=replace(cfg.corridor, mode="SHARED_YIELD"), db_path=":memory:")
    sim = Simulation(cfg)
    try:
        sim.frames[A]["pose"] = sim.frames[B]["pose"] = sim.pose("D")
        sim.call("tied", "A")
        assert sim.tick(0).state["missions"][0]["robot"] == B
    finally:
        sim.core.store.close()
    rm = ResourceManager(cfg)
    for robot, zone in ((A, "C"), (B, "D")):
        rm.request(robot, zone, 1, 0)
    rm.tick(1, {A: RobotReport(cfg.zones["D"], True), B: RobotReport(cfg.zones["F"], True)})
    assert rm.tick(2, {A: RobotReport((-1.55, -2), True), B: RobotReport((-1.55, -3), True)}).commands == [
        MotionRequest(A, "PAUSE")]

    rm = ResourceManager(replace(cfg, corridor=replace(cfg.corridor, room_mode="ROOM")))
    deadlock = DeadlockManager(rm)
    for robot, goal in ((A, "E"), (B, "B")):
        rm.request(robot, goal, 1, 0)
    reports = {A: RobotReport(cfg.zones["B"], True), B: RobotReport(cfg.zones["E"], True)}
    for now in (0, 2):
        rm.tick(now, reports)
        deadlock.tick(now)
    assert deadlock.card["recommended"] == "yield_a"


@pytest.mark.parametrize("limit", [0, 2])
def test_finished_limit_applies_live_and_after_restart(cfg, tmp_path, limit):
    cfg = replace(cfg, db_path=str(tmp_path / "history.db"), assignment=replace(cfg.assignment, mode="IDLE_BEST_BATTERY"),
                  publish=replace(cfg.publish, finished_mission_limit=limit))
    sim = Simulation(cfg)
    try:
        for ref in ("1", "2", "3"):
            sim.call(ref)
        sim.battery(A, 0)
        sim.battery(B, 0)
        sim.tick(0)
        for mid in sim.ids.values():
            sim.cmd("CANCEL_MISSION", mission_id=mid)
        sim.tick(1)
        expected = ["M-0003", "M-0002"][:limit]
        assert [r["mission_id"] for r in sim.core.snapshot(1)["missions"]] == expected
    finally:
        sim.core.store.close()
    restarted = Simulation(cfg)
    try:
        assert [r["mission_id"] for r in restarted.core.snapshot(2)["missions"]] == expected
        assert restarted.core.store.db.execute("SELECT count(*) FROM missions").fetchone()[0] == 3
    finally:
        restarted.core.store.close()


def test_ack_cache_keeps_last_500_and_replays_cached_duplicate(cfg):
    sim = Simulation(replace(cfg, db_path=":memory:"))
    try:
        for _ in range(501):
            sim.cmd("PAUSE_ALL")
        duplicate = sim.pending[1]
        out = sim.tick(0)
        assert len(out.acks) == 501 and sum(e["type"] == "PAUSE" for e in out.events) == 2
        cache = sim.core._acks_seen
        assert len(cache) == 500 and ("/ui/operator_cmd", "c-1") not in cache
        sim.pending.append(duplicate)
        replay = sim.tick(1)
        assert replay.acks == [out.acks[1]] and len(cache) == 500
        sim.cmd("PAUSE_ALL")
        sim.tick(2)
        assert ("/ui/operator_cmd", "c-2") in cache and ("/ui/operator_cmd", "c-3") not in cache
    finally:
        sim.core.store.close()


def test_45_stationary_robot_bumper_stops_the_moving_neighbor(cfg):
    cfg = replace(cfg, safety=replace(cfg.safety, use_hazard_topic=True))
    cfg = replace(cfg, corridor=replace(cfg.corridor, mode="DIRECTIONAL"), db_path=":memory:")
    sim = Simulation(cfg)
    try:
        sim.frames[A]["pose"] = sim.pose((-0.75, -2.30))  # inside E by the door, 0.75 m from B below
        sim.frames[B]["pose"] = sim.pose("D")
        sim.cmd("GO_ZONE", robot=B, zone="C")
        sim.tick(0)
        assert sim.tick(1).commands[0].robot == B
        sim.event(B, "MISSION_ACCEPTED")
        sim.frames[B]["pose"] = sim.pose((-1.50, -2.30))
        sim.pending.append(("hazards", ["BUMP"], A))
        out = sim.tick(2)
        assert out.commands == [MotionRequest(B, "PAUSE")]
        assert sim.state(A) == sim.state(B) == "SAFE_PAUSE"
        assert next(e for e in out.events if e["type"] == "BUMP")["detail"]["targets"] == [A, B]
    finally:
        sim.core.store.close()


@pytest.mark.parametrize("origin,other,dest,front_y,rear_y,delta", [("D","A","F",-2.8,-1.6,-0.2),
                                                                  ("C","F","D",-1.6,-2.8,0.2)])
@pytest.mark.parametrize("front_id", [A, B])
@pytest.mark.parametrize("cause", ["safe_pause", "stalled", "link_loss", "fault"])
def test_47_front_stop_latched_until_progress_or_exit(cfg, front_id, cause, origin, other, dest, front_y, rear_y, delta):
    rm = manager_for(cfg, "DIRECTIONAL")
    rear = B if front_id == A else A
    reports = {front_id: RobotReport(cfg.zones[origin], True), rear: RobotReport(cfg.zones[other], True)}
    for robot in reports:
        rm.request(robot, dest, 1, 0)
    assert len(rm.tick(1, reports).grants) == 2
    reports[front_id] = RobotReport((-1.55, front_y), True)
    reports[rear] = RobotReport((-1.55, rear_y), True)
    rm.tick(2, reports)
    fields = {"link_loss": {"link_ok": False}, "fault": {"can_yield": False}}.get(cause, {cause: True})
    reports[front_id] = replace(reports[front_id], **fields)
    assert rm.tick(3, reports).commands == [MotionRequest(rear, "PAUSE")]
    assert rm.tick(4, reports).commands == []
    reports[front_id] = RobotReport((-1.55, front_y), True)
    assert not rm.tick(5, reports).commands
    reports[front_id] = RobotReport((-1.55, front_y + delta), True)
    assert rm.tick(6, reports).commands == [MotionRequest(rear, "RESUME")]
    assert [e["type"] for e in rm.events if "FOLLOW" in e["type"]] == ["CORRIDOR_FOLLOW_STOP", "CORRIDOR_FOLLOW_CLEAR"]


def test_47_rear_waits_before_move_and_keeps_own_stop(cfg):
    sim = Simulation(replace(cfg, db_path=":memory:"))
    try:
        sim.frames[B]["pose"] = sim.pose("A")
        sim.cmd("GO_ZONE", robot=A, zone="C")
        sim.tick(0)
        sim.tick(1)
        sim.event(A, "MISSION_ACCEPTED")
        sim.frames[A]["pose"] = sim.pose((-1.6, -2.5))
        sim.cmd("PAUSE_ROBOT", robot=A)
        sim.cmd("GO_ZONE", robot=B, zone="F")
        assert sim.tick(2).commands == [MotionRequest(A, "PAUSE")]
        sim.event(A, "PAUSED")
        assert not sim.tick(3).commands
        assert sim.state(B) == "HOLD" and B in sim.core.resources.active
        sim.cmd("RESUME_ROBOT", robot=A)
        assert sim.tick(4).commands == [MotionRequest(A, "RESUME")]
        sim.event(A, "RESUMED")
        sim.frames[A]["pose"] = sim.pose((-1.6, -2.7))
        assert sim.tick(5).commands[0].command.startswith("MOVE F")
        sim.event(B, "MISSION_ACCEPTED")
        sim.frames[B]["pose"] = sim.pose((-1.55, -1.5))
        sim.cmd("PAUSE_ROBOT", robot=A)
        assert sim.tick(6).commands == [MotionRequest(A, "PAUSE"), MotionRequest(B, "PAUSE")]
        sim.event(A, "PAUSED")
        sim.event(B, "PAUSED")
        sim.cmd("PAUSE_ROBOT", robot=B)
        sim.cmd("RESUME_ROBOT", robot=A)
        sim.tick(7)
        sim.event(A, "ARRIVED")
        assert not sim.tick(8).commands and sim.state(B) == "SAFE_PAUSE"
    finally:
        sim.core.store.close()


def test_role_keeps_second_call_queued_while_patient_robot_idle(cfg):
    sim = Simulation(replace(cfg, db_path=":memory:"))
    try:
        sim.call("first")
        sim.call("second")
        state = sim.tick(0).state
        assert state["robots"][A]["state"] == "IDLE"
        assert [(m["robot"], m["status"]) for m in state["missions"]] == [(B, "ACTIVE"), (B, "QUEUED")]
    finally:
        sim.core.store.close()


@pytest.mark.parametrize("condition", [{"safe_pause": True}, {"can_yield": False}, {"link_ok": False}, {"stalled": True}])
def test_47_follow_clear_never_overrides_rear_inhibit(cfg, condition):
    rm = manager_for(cfg, "DIRECTIONAL")
    reports = {A: RobotReport(cfg.zones["A"], True), B: RobotReport(cfg.zones["D"], True)}
    for robot in reports:
        rm.request(robot, "C", 1, 0)
    rm.tick(1, reports)
    reports[A] = RobotReport((-1.6, -3), True, safe_pause=True)
    reports[B] = RobotReport((-1.5, -1), True)
    assert rm.tick(2, reports).commands == [MotionRequest(B, "PAUSE")]
    reports[A] = RobotReport(cfg.zones["C"], True)
    reports[B] = replace(reports[B], **condition)
    assert not rm.tick(3, reports).commands
    assert rm.follow is None


def test_47_recheck_replacement_even_with_unentered_grant(cfg):
    rm = manager_for(cfg, "DIRECTIONAL")
    reports = {A: RobotReport(cfg.zones["A"], True), B: RobotReport(cfg.zones["D"], True)}
    rm.request(A, "C", 1, 0)
    rm.tick(1, reports)
    reports[A] = RobotReport((-1.6, -3), True, safe_pause=True)
    leg = rm.request(B, "F", 1, 1)
    assert rm.tick(2, reports).commands == [MotionRequest(B, "PAUSE")]
    rm.finish(leg, 2)
    replacement = rm.request(B, "C", 1, 2)
    rm.tick(3, reports)
    assert rm.follow.rear is replacement


def test_47_live_stall_window_stops_rear_before_obstacle_card(cfg):
    sim = Simulation(replace(cfg, db_path=":memory:"))
    try:
        sim.frames[B]["pose"] = sim.pose("A")
        for robot, zone in ((A, "C"), (B, "F")):
            sim.cmd("GO_ZONE", robot=robot, zone=zone)
        sim.tick(0)
        sim.tick(1)
        for robot in (A, B):
            sim.event(robot, "MISSION_ACCEPTED")
        sim.frames[A]["pose"] = sim.pose((-1.6, -3))
        sim.frames[B]["pose"] = sim.pose((-1.5, -1))
        sim.tick(2)
        sim.frames[B]["pose"]["y"] -= .2
        stopped = sim.tick(2 + cfg.stall.window_s)
        assert not stopped.cards and stopped.commands == [MotionRequest(B, "PAUSE")]
        sim.event(B, "PAUSED")
        sim.frames[A]["pose"]["y"] -= .2
        assert sim.tick(3 + cfg.stall.window_s).commands == [MotionRequest(B, "RESUME")]
    finally:
        sim.core.store.close()


@pytest.mark.parametrize("a_from,a_to,b_from,b_to", [
    ("D", "C", "C", "D"), ("A", "F", "F", "A"), ("D", "F", "F", "D"),
])
@pytest.mark.parametrize("levels", [(1, 1), (3, 1), (1, 3)])
def test_44_same_tick_opposite_requests_grant_only_one(cfg, a_from, a_to, b_from, b_to, levels):
    rm = manager_for(cfg, "DIRECTIONAL")
    reports = {A: RobotReport(cfg.zones[a_from], True), B: RobotReport(cfg.zones[b_from], True)}
    rm.request(A, a_to, levels[0], 0)
    rm.request(B, b_to, levels[1], 0)
    granted = [g.robot for t in range(1, 8) for g in rm.tick(t, reports).grants]
    assert len(granted) == 1
    assert rm.snapshot()["CORRIDOR"]["holders"] == granted
    if levels[0] != levels[1]:
        assert granted == [A if levels[0] > levels[1] else B]


@pytest.fixture
def field(cfg, request):
    cfg = replace(cfg, safety=replace(cfg.safety, use_hazard_topic=getattr(request, "param", False)))
    sim = Simulation(replace(cfg, db_path=":memory:", corridor=replace(cfg.corridor, mode="DIRECTIONAL")))
    yield sim
    sim.core.store.close()


def start_convoy(sim):
    sim.frames[B]["pose"] = sim.pose("A")
    for robot, goal in ((A, "C"), (B, "F")):
        sim.cmd("GO_ZONE", robot=robot, zone=goal)
    sim.tick(0)
    assert len(sim.tick(1).commands) == 2
    for robot in (A, B):
        sim.event(robot, "MISSION_ACCEPTED")


def test_47_moving_leader_gap_hysteresis(field):
    sim = field
    start_convoy(sim)
    sim.frames[A]["pose"] = sim.pose((-1.55, -2.8))
    sim.frames[B]["pose"] = sim.pose((-1.55, -1.2))
    assert not sim.tick(2).commands
    sim.frames[B]["pose"]["y"] = -2
    out = sim.tick(2.1)
    assert out.commands == [MotionRequest(B, "PAUSE")]
    assert next(e for e in out.events if e["type"] == "CORRIDOR_FOLLOW_STOP")["detail"]["rule"] == "GAP"
    sim.event(B, "PAUSED")
    for i, gap in enumerate((1.09, 1.32, 1.03, 1.39)):
        sim.frames[A]["pose"]["y"] = -2 - gap
        assert not sim.tick(3+i).commands
    sim.frames[A]["pose"]["y"] = -3.5
    assert sim.tick(7).commands == [MotionRequest(B, "RESUME")]
    sim.event(B, "RESUMED")
    sim.frames[A]["pose"]["y"] = -3.1
    assert not sim.tick(8).commands


def test_47_entrance_gap_defers_first_move(field):
    sim = field
    sim.frames[B]["pose"] = sim.pose("A")
    sim.cmd("GO_ZONE", robot=A, zone="C")
    sim.tick(0)
    sim.tick(1)
    sim.event(A, "MISSION_ACCEPTED")
    sim.frames[A]["pose"] = sim.pose((-1.45, -1.3))
    sim.cmd("GO_ZONE", robot=B, zone="F")
    sim.tick(2)
    assert not sim.tick(3).commands
    assert sim.core.resources.follow.rule == "GAP" and sim.core.links[B].move.attempts == 0
    sim.frames[A]["pose"]["y"] = -2.1
    assert sim.tick(4).commands[0].command.startswith("MOVE F")


@pytest.mark.parametrize("choice", ["retry", "hold", "return"])
def test_48_idle_occupant_card_and_clear(field, choice):
    sim = field
    sim.cmd("GO_ZONE", robot=A, zone="F")
    out = sim.tick(0)
    assert any(e["type"] == "ZONE_OCCUPIED_HOLD" for e in out.events)
    assert not sim.tick(29.99).cards and A not in sim.last
    card, = sim.tick(30).cards
    assert card["context"] == "ZONE" and [o["label"] for o in card["options"]] == ["다시 확인", "되돌아가기", "보류"]
    sim.choose(card, choice)
    out = sim.tick(31)
    assert out.acks[0]["ok"] and not out.commands
    assert not sim.tick(31.1).cards
    sim.frames[B]["pose"] = sim.pose("C")
    if choice == "hold":
        assert not sim.tick(32).commands and sim.state(A) == "FAULT"
        sim.cmd("RETRY", robot=A)
    out = sim.tick(33)
    if choice == "return":
        assert sim.last[A].startswith("MOVE D")
    else:
        if choice == "hold":
            out = sim.tick(34)
        assert sim.last[A].startswith("MOVE F")
        if choice == "retry":
            assert "ZONE_OCCUPIED_CLEAR" in {e["type"] for e in out.events}


def test_48_same_destination_stays_reserved_through_arrival(field):
    sim = field
    sim.frames[B]["pose"] = sim.pose("A")
    for robot in (A, B):
        sim.cmd("GO_ZONE", robot=robot, zone="C")
    sim.tick(0)
    out = sim.tick(1)
    assert len(out.commands) == 1 and out.commands[0].robot == A
    assert sim.core.snapshot(1)["robots"][B]["zone_wait"]["peer"] == A
    sim.event(A, "MISSION_ACCEPTED")
    sim.event(A, "ARRIVED")
    assert not sim.tick(2).commands  # still physically at the reserved destination
    sim.frames[A]["pose"] = sim.pose("D")
    assert sim.tick(3).commands[0].robot == B


def test_48_return_waits_for_home(field):
    sim = field
    sim.call("trip", "C")
    sim.tick(0)
    sim.tick(1)
    sim.event(B,"MISSION_ACCEPTED")
    sim.event(B,"ARRIVED")
    sim.tick(2)
    sim.frames[A]["pose"] = sim.pose("F")
    sim.confirm(B,"trip")
    assert not sim.tick(3).commands
    home = sim.core.snapshot(3)["robots"][B]
    assert home["returning"] and home["zone_wait"]["zone"] == "F"
    sim.frames[A]["pose"] = sim.pose("D")
    assert sim.tick(4).commands[0].command.startswith("MOVE F")


def test_48_recheck_granted_but_unsent_leg(field):
    sim = field
    sim.frames[B]["pose"] = sim.pose("A")
    sim.cmd("GO_ZONE", robot=A, zone="C")
    sim.tick(0)
    sim.tick(1)
    sim.event(A, "MISSION_ACCEPTED")
    sim.frames[A]["pose"] = sim.pose((-1.45,-1.3))
    sim.cmd("GO_ZONE", robot=B, zone="F")
    sim.tick(2)
    assert not sim.tick(3).commands
    sim.frames[A]["pose"] = sim.pose("F")
    assert not sim.tick(4).commands and sim.core.links[B].move.attempts == 0
    sim.frames[A]["pose"] = sim.pose("C")
    assert sim.tick(5).commands[0].command.startswith("MOVE F")


def test_49_only_moving_robot_and_manual_overlap(field):
    sim = field
    sim.cmd("GO_ZONE",robot=A,zone="A")
    sim.tick(0)
    sim.tick(1)
    sim.event(A,"MISSION_ACCEPTED")
    sim.frames[A]["pose"] = sim.pose((0,0))
    sim.frames[B]["pose"] = sim.pose((.6,0))
    assert not sim.tick(2).commands
    sim.frames[B]["pose"] = sim.pose((.5,0))
    out = sim.tick(2.1)
    assert out.commands == [MotionRequest(A,"PAUSE")] and set(sim.core.collision.near) == {A}
    sim.event(A,"PAUSED")
    sim.frames[B]["pose"] = sim.pose((.89,0))
    sim.cmd("PAUSE_ROBOT", robot=A)
    assert not sim.tick(3).commands
    sim.frames[B]["pose"] = sim.pose((.9,0))
    out = sim.tick(4)
    assert not out.commands and [e["robot"] for e in out.events if e["type"] == "PROXIMITY_CLEAR"] == [A]
    sim.cmd("RESUME_ROBOT",robot=A)
    assert sim.tick(5).commands == [MotionRequest(A,"RESUME")]


def test_49_follow_clears_before_proximity(field):
    sim = field
    start_convoy(sim)
    sim.frames[A]["pose"] = sim.pose((-1.7,-4.1))
    sim.frames[B]["pose"] = sim.pose((-1.7,-3.6))
    assert sim.tick(2).commands == [MotionRequest(r,"PAUSE") for r in (A,B)]
    for r in (A,B):
        sim.event(r,"PAUSED")
    sim.frames[A]["pose"]["y"] = -4.48
    out = sim.tick(3)
    assert not out.commands and "CORRIDOR_FOLLOW_CLEAR" in {e["type"] for e in out.events}
    assert set(sim.core.collision.near) == {A,B}
    sim.frames[A]["pose"]["y"] = -4.6
    assert sim.tick(4).commands == [MotionRequest(r,"RESUME") for r in (A,B)]


def test_49_cards_authorize_only_one_robot(field):
    sim = field
    sim.frames[A]["pose"] = sim.pose((-2.0,-.3))
    sim.frames[B]["pose"] = sim.pose((-.9,-.15))
    sim.cmd("GO_ZONE", robot=A, zone="A")
    sim.cmd("GO_ZONE", robot=B, zone="D")
    assert len(sim.tick(0).commands) == 2
    for robot in (A,B):
        sim.event(robot,"MISSION_ACCEPTED")
    # Hall-only trips isolate operator sequencing from the independent follow hold.
    sim.frames[A]["pose"] = sim.pose((-2.6,.5))
    sim.frames[B]["pose"] = sim.pose((-2.1,.5))
    assert sim.tick(2).commands == [MotionRequest(r,"PAUSE") for r in (A,B)]
    for r in (A,B):
        sim.event(r,"PAUSED")
    assert not sim.tick(11.99).cards
    cards = sim.tick(12).cards
    card = next(c for c in cards if c["robots"] == [A])
    assert card["options"][-1] == {"id":"peer_first","label":"약품 운송 로봇 먼저 보내기"}
    sim.choose(card, "peer_first")
    assert sim.tick(13).commands == [MotionRequest(B,"RESUME")]
    sim.event(B,"RESUMED")
    assert not sim.tick(14).commands and set(sim.core.collision.near) == {A}
    sim.frames[B]["pose"]["x"] = -.6
    assert sim.tick(15).commands == [MotionRequest(A,"RESUME")]


@pytest.mark.parametrize("inhibit", ["MANUAL", "BUMP", "APPROACH"])
@pytest.mark.parametrize("field", [True], indirect=True)
def test_49_peer_first_respects_other_stops_and_separation(field, inhibit):
    sim = field
    sim.frames[A]["pose"] = sim.pose((-2.0, -.3))
    sim.frames[B]["pose"] = sim.pose((-.9, -.15))
    sim.cmd("GO_ZONE", robot=A, zone="A")
    sim.cmd("GO_ZONE", robot=B, zone="D")
    sim.tick(0)
    for robot in (A, B):
        sim.event(robot, "MISSION_ACCEPTED")
    sim.frames[A]["pose"] = sim.pose((-2.6, .5))
    sim.frames[B]["pose"] = sim.pose((-2.1, .5))
    assert sim.tick(2).commands == [MotionRequest(r, "PAUSE") for r in (A, B)]
    for robot in (A, B):
        sim.event(robot, "PAUSED")
    card = next(c for c in sim.tick(12).cards if c["robots"] == [A])
    if inhibit == "MANUAL":
        sim.cmd("PAUSE_ROBOT", robot=B)
    elif inhibit == "BUMP":
        sim.pending.append(("hazards", ["BUMP"], B))
    sim.choose(card, "peer_first")
    out = sim.tick(13)
    if inhibit == "APPROACH":
        assert out.commands == [MotionRequest(B, "RESUME")]
        sim.event(B, "RESUMED")
        sim.frames[B]["pose"]["x"] -= .05
        assert sim.tick(14).commands == [MotionRequest(B, "PAUSE")]
    else:
        assert not out.commands and sim.state(B) == "SAFE_PAUSE"


def test_49_clear_proximity_still_waits_for_follow_gap(field):
    sim = field
    start_convoy(sim)
    sim.frames[A]["pose"] = sim.pose((-1.55, -2.6))
    sim.frames[B]["pose"] = sim.pose((-1.55, -2.1))
    assert sim.tick(2).commands == [MotionRequest(r, "PAUSE") for r in (A, B)]
    for robot in (A, B):
        sim.event(robot, "PAUSED")
    sim.frames[A]["pose"]["y"] = -3.2
    assert sim.tick(3).commands == [MotionRequest(A, "RESUME")]
    assert sim.state(B) == "HOLD"
    sim.event(A, "RESUMED")
    sim.frames[A]["pose"]["y"] = -3.7
    assert sim.tick(4).commands == [MotionRequest(B, "RESUME")]

@pytest.mark.parametrize("goal", list("ABCDEF"))
def test_48_all_zone_points_including_halls(field, goal):
    sim = field
    sim.frames[A]["pose"] = sim.pose("C" if goal == "D" else "D")
    sim.frames[B]["pose"] = sim.pose(goal)
    sim.cmd("GO_ZONE", robot=A, zone=goal)
    assert not sim.tick(0).commands and not sim.tick(2).commands
    assert sim.core.snapshot(2)["robots"][A]["zone_wait"]["zone"] == goal
