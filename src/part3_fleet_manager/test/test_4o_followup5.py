"""Stage 4O follow-up 5: following on the same way in the same direction (D-59), and the slot links of the segment map
made from zones.<z>.slot2_blocks_exit (D-60)."""
from dataclasses import dataclass, replace
from math import dist

import pytest

from kinematic_mock import KinematicSim
from part3_fleet_manager.core.config import ConfigError, config_from_dict, read_config_yaml
from part3_fleet_manager.core.plan_traffic import _follows
from conftest import CONFIG_PATH

A, B = "robot_a", "robot_b"


# --- 1. following -----------------------------------------------------------------------------------------------

def _follow_scene(cfg, on_tick=None):
    """11/21 21:45:31~21:46:02 (mock record /tmp/bad_mock.db): robot_a (Level 1) D1 -> C2 is in the upper corridor
    when robot_b (Level 3) at A2 is sent to C; robot_b follows robot_a down the corridor. Returns the sim, the time
    robot_b was sent, robot_a's arrival time and the smallest distance until then."""
    sim = KinematicSim(cfg, poses={A: cfg.slot_point("D", 1), B: cfg.slot_point("A", 2)})
    sim.run_until(1.0)
    sim.cmd("GO_ZONE", robot=A, zone="C", level=1)
    top = sim.core.resources.segments["CORRIDOR_TOP"].spec
    while not top.contains(sim.robots[A].pose):
        sim.step(0.1)
    sim.run_until(sim.now + 2.8, dt=0.1)           # 21:45:38.2 into the corridor, 21:45:41.0 robot_b is sent
    sent = sim.now
    sim.cmd("GO_ZONE", robot=B, zone="C", level=3)
    arrived, closest = None, 9.0
    while sim.now < sent + 60.0 and arrived is None:
        out = sim.step(0.1)
        closest = min(closest, dist(sim.robots[A].pose, sim.robots[B].pose))
        if on_tick:
            on_tick(sim, out)
        if any(e["type"] == "ARRIVE" and e["robot"] == A for e in out.events):
            arrived = sim.now
    return sim, sent, arrived, closest


def _waits(sim, robot, start, end):
    waits = [e for e in sim.all_events if e["type"] == "PLAN_WAIT" and e["robot"] == robot and start <= e["ts"] <= end]
    pauses = [t for t, r, c in sim.all_commands if r == robot and c == "PAUSE" and start <= t <= end]
    return waits, pauses


def test_follower_on_the_same_way_waits_at_most_once(cfg):
    """Before: robot_b stopped four times (hall mouth, upper corridor 1 and 2, crossroad: the mock record; three in
    this replay) at 1.7~3.5 m behind robot_a. Now only the crossroad may hold it (one robot at a time)."""
    sim, sent, arrived, closest = _follow_scene(cfg)
    try:
        assert arrived is not None
        waits, pauses = _waits(sim, B, sent, arrived)
        assert len(waits) <= 1 and len(pauses) <= 1, [e["detail"]["reason"] for e in waits]
        assert all(e["detail"]["point"] == "CROSSROAD" for e in waits)
        assert closest >= cfg.corridor.follow_gap_m
        assert not [e for e in sim.all_events if e["type"] == "PROXIMITY_STOP"]
        sim.run_until(sim.now + 60.0)
        assert sim.core.mission.robots[B].zone == "C"
    finally:
        sim.close()


def test_follower_stops_at_its_stop_line_when_the_robot_ahead_stops(cfg):
    """The operator stops robot_a while robot_b follows it down toward the crossroad: robot_b is held at its stop line
    (no proximity stop), and goes on when robot_a drives again."""
    state = {"paused": None, "resumed": None}

    def on_tick(sim, out):
        if (state["paused"] is None and sim.robots[B].pose[1] < -1.5 and sim.robots[B].state == "MOVING"
                and sim.robots[A].state == "MOVING"):
            sim.pause(A)
            state["paused"] = sim.now
        if state["paused"] is not None and state["resumed"] is None and sim.now >= state["paused"] + 12.0:
            sim.resume(A)
            state["resumed"] = sim.now

    sim, sent, arrived, closest = _follow_scene(cfg, on_tick)
    try:
        assert state["paused"] is not None and state["resumed"] is not None and arrived is not None
        held = [e for e in sim.all_events if e["type"] == "PLAN_WAIT" and e["robot"] == B
                and state["paused"] <= e["ts"] <= state["resumed"]]
        assert held and any(c == "PAUSE" for t, r, c in sim.all_commands
                            if r == B and state["paused"] <= t <= state["resumed"])
        assert closest >= cfg.safety.proximity_stop_m
        assert not [e for e in sim.all_events if e["type"] == "PROXIMITY_STOP"]
    finally:
        sim.close()


@dataclass
class _E:
    point: str
    standing: bool = False


def _way(*points, last_standing=False):
    out = [_E(p) for p in points]
    if last_standing:
        out[-1].standing = True
    return out


def test_only_the_same_way_in_the_same_direction_is_following():
    top = [f"CORRIDOR_TOP_{k}" for k in (1, 2, 3)]
    assert _follows(_way(top[1], top[2], "CROSSROAD"), _way(top[0], top[1], top[2]))           # behind, same way
    assert _follows(_way("HALL_TOP", top[0]), _way("HALL_TOP", top[0]))                         # merging behind
    assert not _follows(_way(top[1], top[0], "HALL_TOP"), _way(top[0], top[1], top[2]))        # meeting head-on
    assert not _follows(_way("CROSSROAD", "CORRIDOR_BOTTOM_1"), _way(top[2], "CROSSROAD", "DOOR_E"))   # crossing
    assert not _follows(_way("C2", "C2", last_standing=True), _way("HALL_BOTTOM", "C2"))       # ahead stops there
    assert not _follows(_way("C2", "C2", last_standing=True)[1:], _way("C2"))                  # standing


# --- 2. slot links from slot2_blocks_exit -----------------------------------------------------------------------

# traffic.links of fleet.yaml up to 39b1336, written by hand (the slot links are made from the zones now).
HAND_LINKS = [
    ("A1", "A2"), ("A2", "HALL_TOP"), ("D1", "HALL_TOP"), ("D2", "HALL_TOP"), ("HALL_TOP", "CORRIDOR_TOP"),
    ("CORRIDOR_TOP", "CROSSROAD"), ("CROSSROAD", "CORRIDOR_BOTTOM"), ("CORRIDOR_BOTTOM", "HALL_BOTTOM"),
    ("HALL_BOTTOM", "C2"), ("C2", "C1"), ("HALL_BOTTOM", "F2"), ("F2", "F1"), ("CROSSROAD", "DOOR_B"),
    ("DOOR_B", "ROOM_B"), ("ROOM_B", "B1"), ("ROOM_B", "B2"), ("CROSSROAD", "DOOR_E"), ("DOOR_E", "ROOM_E"),
    ("ROOM_E", "E1"), ("ROOM_E", "E2"),
]


def _links(cfg):
    return {frozenset(pair) for pair in cfg.traffic.links}


def test_slot_links_made_from_slot2_blocks_exit_match_the_hand_written_links(cfg):
    raw = read_config_yaml(CONFIG_PATH)
    slots = {name for name, spec in raw["traffic"]["segments"].items() if "slot" in spec}
    assert not [pair for pair in raw["traffic"]["links"] if set(pair) & slots]      # none written by hand any more
    assert _links(cfg) == {frozenset(pair) for pair in HAND_LINKS}
    assert len(cfg.traffic.links) == len(HAND_LINKS)


def test_slot_links_follow_slot2_blocks_exit():
    raw = read_config_yaml(CONFIG_PATH)
    raw["zones"]["D"]["slot2_blocks_exit"] = True                 # hall -> D2 -> D1
    raw["zones"]["A"]["slot2_blocks_exit"] = False                # hall -> A1, hall -> A2
    cfg = config_from_dict(raw)
    links = _links(cfg)
    assert {frozenset(("HALL_TOP", "D2")), frozenset(("D2", "D1"))} <= links and frozenset(("HALL_TOP", "D1")) not in links
    assert {frozenset(("HALL_TOP", "A1")), frozenset(("HALL_TOP", "A2"))} <= links and frozenset(("A1", "A2")) not in links


def test_a_hand_written_slot_link_must_agree_with_slot2_blocks_exit():
    raw = read_config_yaml(CONFIG_PATH)
    raw["traffic"]["links"].append(["HALL_TOP", "A2"])           # the same as the made one: accepted
    assert _links(config_from_dict(raw)) == {frozenset(pair) for pair in HAND_LINKS}
    raw = read_config_yaml(CONFIG_PATH)
    raw["traffic"]["links"].append(["HALL_TOP", "A1"])           # A1 is behind A2 (slot2_blocks_exit: true)
    with pytest.raises(ConfigError, match="slot2_blocks_exit"):
        config_from_dict(raw)


def test_plan_graph_from_the_made_links_is_unchanged(cfg):
    from part3_fleet_manager.core.segments import SegmentMap
    hand = replace(cfg, traffic=replace(cfg.traffic, links=tuple(HAND_LINKS)))
    made, written = SegmentMap(cfg).graph, SegmentMap(hand).graph
    assert {frozenset(e) for e in made.edges} == {frozenset(e) for e in written.edges}


# --- found by the random test (seed 188) -------------------------------------------------------------------------

def test_waiting_mission_cancelled_in_the_tick_the_cancel_is_confirmed_still_sends_the_robot_home(cfg):
    """robot_b's running mission is cancelled in the corridor; in the tick its CANCEL is confirmed the next waiting
    mission is cancelled too. The return home after a cancel (PLAN) was skipped because a mission still waited:
    robot_b stayed in the corridor holding it. Now it goes home once the queue is empty."""
    sim = KinematicSim(cfg)
    try:
        running = sim.submit_call("A", level=1)
        waiting = sim.submit_call("D", level=1)
        top = sim.core.resources.segments["CORRIDOR_TOP"].spec
        while sim.now < 60 and not (top.contains(sim.robots[B].pose) and sim.robots[B].state == "MOVING"):
            sim.step(0.2)
        unit = sim.core.mission.robots[B]
        assert unit.job.id == sim.ids[running] and [j.id for j in unit.queue] == [sim.ids[waiting]]
        sim.cancel(sim.ids[running])
        sim.step(0.2)                                   # CANCEL goes out
        sim.cancel(sim.ids[waiting])
        sim.step(0.2)                                   # CANCEL confirmed and the waiting mission cancelled
        assert unit.job is not None and unit.job.kind == "RETURN_HOME"
        sim.run_until(sim.now + 60.0)
        assert unit.zone == "F" and unit.job is None
        assert not [n for n in sim.core.resources.map.narrow_names if sim.core.resources.segments[n].holders]
    finally:
        sim.close()
