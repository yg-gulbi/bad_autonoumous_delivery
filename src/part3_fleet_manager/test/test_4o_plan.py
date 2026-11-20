"""Stage 4O PLAN: point graph, joint planner (behaviour from cost alone), execution, config, LEGACY isolation.

The planner tests build agents the way PlanManager does (standing at a slot, or driving a route from a
pose) and check that the expected behaviour comes out of the cost. No test helper knows a zone pair.
"""
import json
import statistics
import time
from dataclasses import replace
from math import hypot
from pathlib import Path

import pytest

from kinematic_mock import KinematicSim
from part3_fleet_manager.core import schemas
from part3_fleet_manager.core import segments as segments_module
from part3_fleet_manager.core.config import config_from_dict, read_config_yaml
from part3_fleet_manager.core.fleet_core import FleetCore
from part3_fleet_manager.core.plan_traffic import plan_graph
from part3_fleet_manager.core.planner import SLOT2_PENALTY, Agent, Planner
from part3_fleet_manager.core.robot_protocol import RobotProtocol
from part3_fleet_manager.preflight_checks import traffic_mode_line

A, B = "robot_a", "robot_b"
CORRIDOR_POINTS = ("CORRIDOR_TOP", "CORRIDOR_BOTTOM", "CROSSROAD")


# --- helpers: agents as PlanManager builds them ---------------------------------------------------

class Kit:
    def __init__(self, cfg):
        self.cfg = cfg
        self.G = plan_graph(cfg)
        self.P = Planner(self.G)
        self.slots = [n for n, p in self.G.endpoints.items() if p.kind == "slot"]
        self.weights = cfg.traffic.graph.level_weights

    def aside(self, here, pose, zone, k=None):
        k = self.cfg.traffic.graph.aside_candidates if k is None else k
        names = [n for n in self.slots if self.G.endpoints[n].zone != zone and n != here]
        steps = {n: self.G.route_steps(self.G.from_endpoint[here][n] if here else self.G.route_from(pose, n))
                 for n in names}
        return sorted(steps, key=lambda n: (steps[n], n))[:k]

    def goals(self, zone):
        names = tuple(n for n in self.slots if self.G.endpoints[n].zone == zone)
        return names, {self.G.park[n]: SLOT2_PENALTY * (self.G.endpoints[n].slot == 2) for n in names}

    def standing(self, name, at, zone=None, level=None):
        """Robot standing in slot ``at``: idle (goal = this slot) or with a leg to ``zone``."""
        if zone is None:
            names, goals = (at,), {self.G.park[at]: 0.0}
            weight, own = self.cfg.traffic.graph.idle_weight, self.G.endpoints[at].zone
        else:
            (names, goals), weight, own = self.goals(zone), self.weights[level], zone
        return Agent(name, weight, [(self.G.stay_route(at), None)], goals,
                     tuple(self.aside(at, None, own)) + names, names)

    def driving(self, name, pose, target, zone, level):
        """Robot driving toward ``target``; it may turn at once only to routes that keep going forward."""
        names, goals = self.goals(zone)
        stop_m = self.cfg.traffic.stop_margin_m
        rid = self.G.route_from(pose, target)
        route = self.G.routes[rid]
        until = max((j for j, s0 in enumerate(route.starts) if j > 0 and s0 <= stop_m), default=None)
        starts = [(rid, until)]
        heading = (route.line[1][0] - pose[0], route.line[1][1] - pose[1])
        committed = [self.G.pieces[p].point for p in route.pieces[:(until or 0) + 1]]
        asides, room = 0, self.cfg.traffic.graph.aside_candidates
        # Aside slots: the nearest aside_candidates among those it can still turn to (slots ahead).
        for other in [*names, *self.aside(None, pose, zone, k=len(self.slots))]:
            if other == target:
                continue
            if other not in names and asides >= room:
                break
            alt = self.G.routes[self.G.route_from(pose, other)]
            step = (alt.line[1][0] - pose[0], alt.line[1][1] - pose[1])
            cos = (step[0] * heading[0] + step[1] * heading[1]) / ((hypot(*step) or 1) * (hypot(*heading) or 1))
            if cos > 0.7 and [self.G.pieces[p].point for p in alt.pieces[:len(committed)]] == committed:
                starts.append((alt.id, until))
                asides += other not in names
        return Agent(name, self.weights[level], starts, goals, names, names)

    def timeline(self, plan, name):
        """[(t, point, standing, route target)] at every change of position."""
        out = []
        for t, state in plan.events[name]:
            piece = self.G.pieces[self.P.piece_of(state)]
            item = (t, piece.point, piece.standing, self.G.routes[state[0]].target)
            if not out or out[-1][1:3] != item[1:3]:
                out.append(item)
        return out

    def first_target(self, plan, name):
        for _, state in plan.events[name]:
            route = self.G.routes[state[0]]
            if route.pieces:
                return route.target
        return None

    def per_step(self, plan, name, end):
        return [self.P.piece_of(self.P.at(plan.events[name], t)) for t in range(end + 1)]


@pytest.fixture
def kit(cfg):
    return Kit(replace(cfg, traffic=replace(cfg.traffic, mode="PLAN")))


def corridor(kit, piece):
    return kit.G.pieces[piece].point.startswith(CORRIDOR_POINTS)


def assert_clean(kit, plan):
    """No conflicting pair at the same step and no swap (moving through each other) anywhere."""
    names = list(plan.events)
    end = max(plan.end, *(line[-1][0] for line in plan.events.values())) + 1
    steps = {n: kit.per_step(plan, n, end) for n in names}
    a, b = names
    for t in range(end + 1):
        pa, pb = steps[a][t], steps[b][t]
        if t == 0 and kit.G.conflict(pa, pb):
            continue
        assert not kit.G.conflict(pa, pb), f"conflict at step {t}: {kit.G.pieces[pa]} / {kit.G.pieces[pb]}"
        if t:
            qa, qb = steps[a][t - 1], steps[b][t - 1]
            if pa != qa and pb != qb:
                assert not (kit.G.pieces[pa].point == kit.G.pieces[qb].point
                            and kit.G.pieces[pb].point == kit.G.pieces[qa].point), f"swap at step {t}"


# --- 1. graph ---------------------------------------------------------------------------------------

def test_graph_points_cells_and_edges_come_from_config_and_routes(kit):
    G = kit.G
    kinds = {}
    for p in G.points.values():
        kinds.setdefault(p.kind, []).append(p.name)
    assert len(kinds["slot"]) == 12 and kinds["dock"] == ["DOCK_B"]
    assert sorted(kinds["door"]) == ["DOOR_B", "DOOR_E"] and sorted(kinds["room"]) == ["ROOM_B", "ROOM_E"]
    assert sorted(kinds["hall"]) == ["HALL_BOTTOM", "HALL_TOP"] and kinds["crossroad"] == ["CROSSROAD"]
    # CORRIDOR_TOP (about 1.0 m) and CORRIDOR_BOTTOM (about 1.4 m) are cut into about cell_m pieces.
    assert sorted(kinds["cell"]) == ["CORRIDOR_BOTTOM_1", "CORRIDOR_BOTTOM_2", "CORRIDOR_BOTTOM_3",
                                     "CORRIDOR_TOP_1", "CORRIDOR_TOP_2"]
    # Neighbours are what PART 04 drives: D1 -> C1 goes hall, two top cells, crossroad, three bottom cells.
    route = G.routes[G.from_endpoint["D1"]["C1"]]
    assert [G.pieces[p].point for p in route.pieces] == [
        "D1", "HALL_TOP", "CORRIDOR_TOP_1", "CORRIDOR_TOP_2", "CROSSROAD",
        "CORRIDOR_BOTTOM_1", "CORRIDOR_BOTTOM_2", "CORRIDOR_BOTTOM_3", "HALL_BOTTOM", "C1"]
    # Into a room: crossroad, the middle of the door, the point inside the room, then the slot (and back out).
    route = G.routes[G.from_endpoint["D1"]["E1"]]
    assert [G.pieces[p].point for p in route.pieces][-4:] == ["CROSSROAD", "DOOR_E", "ROOM_E", "E1"]
    route = G.routes[G.from_endpoint["B2"]["C1"]]
    assert [G.pieces[p].point for p in route.pieces][:4] == ["B2", "ROOM_B", "DOOR_B", "CROSSROAD"]
    assert ("DOOR_E", "E1") not in G.edges and ("DOOR_B", "B2") not in G.edges
    route = G.routes[G.from_endpoint["D1"]["C1"]]
    # Travel time is length / plan_speed_mps in plan steps.
    first = G.pieces[route.pieces[2]]
    assert first.steps == max(1, round(first.length / (kit.cfg.mock.speed_mps * kit.cfg.traffic.graph.plan_step_s)))


def test_conflicts_are_geometric_and_use_the_slot_distance_for_a_standing_robot(kit):
    G = kit.G
    stop, slot_stop = kit.cfg.safety.proximity_stop_m, kit.cfg.safety.slot_stop_m
    # Two moving positions closer than proximity_stop_m conflict.
    d1c1 = G.routes[G.from_endpoint["D1"]["C1"]].pieces
    c1d1 = G.routes[G.from_endpoint["C1"]["D1"]].pieces
    assert G.conflict(d1c1[2], c1d1[-3]) and G.distance(d1c1[2], c1d1[-3]) < stop
    # A robot standing at D2: the D1 approach passes it at 0.535 m (between slot_stop_m and proximity_stop_m).
    approach = [p for p in G.routes[G.from_endpoint["C1"]["D1"]].pieces if G.pieces[p].point == "HALL_TOP"][0]
    assert slot_stop <= G.distance(approach, G.park["D2"]) < stop
    assert not G.conflict(approach, G.park["D2"])
    # Two standing robots never stop each other; a single segment holds one robot.
    assert not G.conflict(G.park["C1"], G.park["C2"])
    cross = [p for p in d1c1 if G.pieces[p].point == "CROSSROAD"][0]
    e_door = [p for p in G.routes[G.from_endpoint["E1"]["B1"]].pieces if G.pieces[p].point == "DOOR_E"][0]
    assert "CROSSROAD" in G.shares(cross, e_door) or G.conflict(cross, e_door)


def test_segment_map_comes_only_from_fleet_yaml(cfg):
    assert not hasattr(segments_module, "build_default_map_specs")
    raw = read_config_yaml(Path(__file__).resolve().parents[1] / "config/fleet.yaml")
    raw["traffic"]["mode"] = "PLAN"
    raw["traffic"]["segments"] = {}
    raw["traffic"]["links"] = []
    raw["traffic"].pop("graph")
    with pytest.raises(ValueError, match="칸 지도"):
        config_from_dict(raw)


# --- 2. behaviour from cost alone -------------------------------------------------------------------

def test_lower_level_waits_outside_the_corridor(kit):
    plan = kit.P.plan([kit.standing("low", "D1", "C", 1), kit.standing("high", "C1", "D", 3)])
    assert plan.ok and plan.method == "JOINT"
    assert_clean(kit, plan)
    end = plan.end
    low, high = kit.per_step(plan, "low", end), kit.per_step(plan, "high", end)
    high_in = [t for t, p in enumerate(high) if corridor(kit, p)]
    low_in = [t for t, p in enumerate(low) if corridor(kit, p)]
    assert high_in and low_in and max(high_in) < min(low_in)       # the high Level goes through first
    waits = [t for t in range(1, end) if low[t] == low[t - 1] and t < min(low_in)]
    assert waits and not any(corridor(kit, low[t]) for t in waits)  # the low Level waits outside


@pytest.mark.parametrize("level, aside", [(1, True), (2, True), (3, False)])
def test_low_level_in_corridor_moves_aside_or_drives_out_by_cost(kit, level, aside):
    # X drives down the top corridor toward C; a Level 3 robot leaves C1 for D.
    x = kit.driving("X", (-1.47, -1.45), "C2", "C", level)
    plan = kit.P.plan([x, kit.standing("Y", "C1", "D", 3)])
    assert plan.ok
    assert_clean(kit, plan)
    first = kit.first_target(plan, "X")
    if aside:     # stepping into a B or E slot is cheaper than making Level 3 wait
        assert kit.G.endpoints[first].zone in ("B", "E")
    else:         # equal Level: driving out is cheaper; Y waits out of the way
        assert kit.G.endpoints[first].zone == "C"


def test_idle_robot_blocking_an_exit_steps_aside_and_comes_back(cfg):
    # A map where E2 stands on E1's way out (inside the room, in front of the door).
    e2 = (-0.848, -1.718)
    segs = dict(cfg.traffic.segments)
    segs["E2"] = replace(segs["E2"], circle=(e2[0], e2[1], segs["E2"].circle[2]))
    k = Kit(replace(cfg, slots={**cfg.slots, "E": (cfg.zones["E"], e2)},
                    traffic=replace(cfg.traffic, mode="PLAN", segments=segs)))
    plan = k.P.plan([k.standing("idle", "E2"), k.standing("out", "E1", "D", 2)])
    assert plan.ok
    assert_clean(k, plan)
    idle = k.timeline(plan, "idle")
    targets = [target for *_, target in idle]
    moves = [t for i, t in enumerate(targets) if i == 0 or t != targets[i - 1]]
    assert len(moves) == 2 and moves[-1] == "E2"                 # from E2: MOVE aside, then MOVE back
    assert k.G.endpoints[moves[0]].zone != "E"
    out = k.timeline(plan, "out")
    leaves_e2 = next(t for t, point, standing, _ in idle if point != "E2")
    assert next(t for t, point, *_ in out if point == "DOOR_E") > leaves_e2


def test_same_direction_robots_pass_one_after_the_other(kit):
    plan = kit.P.plan([kit.standing("first", "D1", "C", 1), kit.standing("second", "D2", "F", 1)])
    assert plan.ok
    assert_clean(kit, plan)
    end = plan.end
    a, b = kit.per_step(plan, "first", end), kit.per_step(plan, "second", end)
    for steps in (a, b):
        assert any(corridor(kit, p) for p in steps)
    assert kit.G.pieces[a[-1]].point in ("C1", "C2") and kit.G.pieces[b[-1]].point in ("F1", "F2")


def test_plans_never_hold_a_conflict_or_a_swap(kit):
    starts = ["A1", "A2", "B1", "C1", "D1", "D2", "E1", "E2", "F1", "F2"]
    for a in starts:
        for b in starts:
            if a == b:
                continue
            za, zb = kit.G.endpoints[a].zone, kit.G.endpoints[b].zone
            if za == zb:
                continue
            plan = kit.P.plan([kit.standing("p", a, zb, 2), kit.standing("q", b, za, 1)])
            assert plan.ok, (a, b)
            assert_clean(kit, plan)


def test_plan_time_for_every_slot_pair_and_level_pair(kit, tmp_path):
    """Every ordered pair of slots heading into each other's zone, for all Level pairs."""
    times, methods, samples = [], {}, []
    slots = kit.slots
    for a in slots:
        for b in slots:
            za, zb = kit.G.endpoints[a].zone, kit.G.endpoints[b].zone
            if a == b or za == zb:
                continue
            for la in (1, 2, 3):
                for lb in (1, 2, 3):
                    start = time.perf_counter()
                    plan = kit.P.plan([kit.standing("p", a, zb, la), kit.standing("q", b, za, lb)],
                                      limit_ms=kit.cfg.traffic.graph.plan_limit_ms)
                    times.append((time.perf_counter() - start) * 1000.0)
                    samples.append((times[-1], plan.method))
                    methods[plan.method] = methods.get(plan.method, 0) + 1
                    assert plan.ok, (a, b, la, lb)
    stats = {"plans": len(times), "mean_ms": round(statistics.mean(times), 1),
             "p95_ms": round(sorted(times)[int(0.95 * len(times))], 1), "max_ms": round(max(times), 1),
             "methods": methods}
    print(json.dumps(stats, ensure_ascii=False))
    gcfg = kit.cfg.traffic.graph
    assert gcfg.plan_limit_ms == 200
    assert stats["mean_ms"] <= gcfg.plan_budget_ms
    # The joint search stops at plan_limit_ms (checked every expansion); a timed-out one is followed by the
    # Level-order plan, which has its own plan_limit_ms.
    for ms, method in samples:
        assert ms <= gcfg.plan_limit_ms * (1.1 if method == "JOINT" else 2.2), (ms, method)


def test_joint_search_over_the_limit_falls_back_to_level_order(kit):
    """Past plan_limit_ms the joint search stops and robots are planned one at a time in Level order."""
    plan = kit.P.plan([kit.standing("p", "D2", "C", 2), kit.standing("q", "C1", "D", 2)], limit_ms=0.01)
    assert plan.ok and plan.method == "PRIORITIZED" and plan.reason == "계획 시간 초과"
    assert_clean(kit, plan)


# --- 3. config, LEGACY isolation, snapshot --------------------------------------------------------

def test_mode_names_default_legacy_and_preflight(cfg):
    assert cfg.traffic.mode == "LEGACY"
    assert cfg.effective_return_after_cancel is False
    pc = replace(cfg, traffic=replace(cfg.traffic, mode="PLAN"))
    assert pc.effective_return_after_cancel is True
    assert "LEGACY" in traffic_mode_line(cfg) and "PLAN" in traffic_mode_line(pc)
    raw = read_config_yaml(Path(__file__).resolve().parents[1] / "config/fleet.yaml")
    raw["traffic"]["mode"] = "SEGMENTS"
    with pytest.raises(ValueError, match="traffic.mode"):
        config_from_dict(raw)


def test_resume_and_cancel_resend_only_in_plan(cfg):
    legacy = FleetCore(replace(cfg, db_path=":memory:"))
    plan = FleetCore(replace(cfg, db_path=":memory:", traffic=replace(cfg.traffic, mode="PLAN")))
    try:
        assert not any(link.resend_controls for link in legacy.links.values())
        assert all(link.resend_controls for link in plan.links.values())
    finally:
        legacy.store.close()
        plan.store.close()
    for resend, expected in ((False, None), (True, "RESUME")):
        link = RobotProtocol("robot2", cfg.robot_link, resend_controls=resend)
        link.state, link.state_at = "PAUSED", 0.0
        assert link.step(0.0, resume=True)[0] == "RESUME"
        link.state_at = 1.0          # STATE still PAUSED after the RESUME
        assert link.step(cfg.robot_link.accept_timeout_s + 0.1, resume=True)[0] == expected


def test_retreat_blocks_home_interrupt_only_in_plan(cfg):
    for mode, interruptible in (("LEGACY", True), ("PLAN", False)):
        core = FleetCore(replace(cfg, db_path=":memory:", traffic=replace(cfg.traffic, mode=mode)))
        try:
            m = core.mission
            r = m.robots[A]
            from part3_fleet_manager.core.mission import Mission
            r.job = Mission("H-x", A, "RETURN_HOME", 1, [{"zone": "D"}], None, 0.0, status="ACTIVE")
            r.state, r.target_override = "MOVING", "E"
            assert m._interruptible_return(A) is interruptible
        finally:
            core.store.close()


def test_plan_snapshot_lists_routes_and_wait_reasons(cfg):
    sim = KinematicSim(cfg, mode="PLAN")
    try:
        sim.submit_patient("C", "A", level=1)
        sim.submit_med(("E", "B"), level=3)
        reasons = set()
        for _ in range(150):
            sim.confirm_waiting()
            out = sim.step(0.2, validate_wire=True)
            traffic = out.state["traffic"] if out.state else None
            if traffic:
                assert traffic["mode"] == "PLAN"
                for robot, plan in traffic["plans"].items():
                    assert plan["points"] and (plan["route"] or len(plan["points"]) == 1)
                    if plan["wait"]:
                        reasons.add(plan["wait"])
        kinds = {e["type"] for e in sim.all_events}
        assert {"PLAN_UPDATE", "PLAN_WAIT"} <= kinds
        assert any(": " in r and r.endswith("지나간 뒤") for r in reasons)
        assert not any(e["type"] == "PROXIMITY_STOP" for e in sim.all_events)
    finally:
        sim.close()


# --- 4. execution in the kinematic mock ---------------------------------------------------------

def test_cancel_in_the_corridor_is_a_new_goal_without_e423(cfg):
    sim = KinematicSim(cfg, mode="PLAN")
    try:
        sim.submit_patient("C", "A", level=2)
        while sim.now < 30 and not (-2.2 < sim.robots[A].pose[1] < -1.0):
            sim.confirm_waiting()
            sim.step(0.2)
        assert cfg.corridor.region_of(sim.robots[A].pose) == "CORRIDOR"
        job = sim.core.mission.robots[A].job
        ref = sim.cancel(job.id)
        ack = next(a for a in sim.step(0.2).acks if a.get("ref") == ref)
        assert ack.get("ok") is True, ack           # no "E-423: 복도 통과 후 취소하세요" in PLAN
        sim.run_until(sim.now + 60.0)
        assert any(e["type"] == "MISSION_CANCELED" for e in sim.all_events)
        unit = sim.core.mission.robots[A]
        assert unit.state == "IDLE" and unit.zone == cfg.robots[A].start_zone
        assert not any(e["type"] == "PROXIMITY_STOP" for e in sim.all_events)
    finally:
        sim.close()


def test_link_loss_keeps_segments_unknown_until_operator_check(cfg):
    sim = KinematicSim(cfg, mode="PLAN")
    try:
        sim.submit_patient("C", "A", level=2)
        while sim.now < 30 and "CORRIDOR_TOP" not in {
                n for n, s in sim.core.resources.segments.items() if A in s.holders and s.holders[A].state == "OCCUPIED"}:
            sim.step(0.2)
        sim.robots[A].drop_link(sim.now, 1e9)
        sim.run_until(sim.now + 6.0, auto_confirm=False)
        seg = sim.core.resources.segments["CORRIDOR_TOP"]
        assert seg.state == "UNKNOWN" and A in seg.uncertain
        sim.cmd("RESOURCE_CLEAR_CONFIRM", resource="CORRIDOR_TOP")
        sim.step(0.2)
        assert any(e["type"] == "RESOURCE_CLEAR_CONFIRM" for e in sim.all_events)
    finally:
        sim.close()


def test_snapshot_schema_in_plan_mode(cfg):
    core = FleetCore(replace(cfg, db_path=":memory:", traffic=replace(cfg.traffic, mode="PLAN")))
    try:
        state = core.snapshot(0.0)
        assert state["traffic"]["mode"] == "PLAN"
        assert set(state["resources"]) == {"CORRIDOR", "ROOM_B", "ROOM_E"}
        schemas.parse_fleet_state(schemas.dumps(state))
    finally:
        core.store.close()
