from dataclasses import replace

import networkx as nx
import pytest

from part3_fleet_manager.core.deadlock import DeadlockManager
from part3_fleet_manager.core.resource_manager import ResourceManager, RobotReport
from part3_fleet_manager.core.schemas import dumps, parse_decision_req


A, B = "robot_a", "robot_b"
MID = (-1.6, -2.0)


class RoomSwap:
    def __init__(self, cfg, *, mode="SHARED_YIELD", levels=(2, 2), since=(0, 0), patients=(False, False)):
        cfg = replace(cfg, corridor=replace(cfg.corridor, mode=mode))
        self.cfg = cfg
        self.resources = ResourceManager(cfg)
        self.deadlock = DeadlockManager(self.resources)
        self.reports = {A: RobotReport(cfg.zones["B"], True), B: RobotReport(cfg.zones["E"], True)}
        self.legs = {}
        for robot, dest, level, stamp, patient in sorted(
            zip((A, B), ("E", "B"), levels, since, patients), key=lambda args: args[3]
        ):
            self.legs[robot] = self.resources.request(
                robot, dest, level, stamp, mission_id=f"M-{robot}", carrying_patient=patient,
            )
        self.tick(max(since))

    def tick(self, now):
        grants = self.resources.tick(now, self.reports).grants
        self.deadlock.tick(now)
        return grants

    def pose(self, robot, xy):
        self.reports[robot] = RobotReport(xy, True)


@pytest.mark.parametrize("mode", ["EXCLUSIVE", "SHARED_YIELD"])
def test_5_swap_times_out_yields_and_completes_both_original_legs(cfg, mode):
    swap = RoomSwap(cfg, mode=mode)
    shared = mode == "SHARED_YIELD"
    rm, dl = swap.resources, swap.deadlock
    assert isinstance(dl.wait_graph(), nx.DiGraph)
    assert set(dl.wait_graph().edges) == {(A, B), (B, A)}
    assert not swap.tick(1.99) and dl.card is None
    assert not swap.tick(2)
    card = dl.card.copy()
    assert parse_decision_req(dumps(card)) == card
    assert card["recommended"] == card["default"] == "yield_b"
    assert card["created_at"] == 2 and card["timeout_s"] == 20
    assert not swap.tick(21.99) and dl.card == card
    assert not swap.tick(22)
    assert dl.card is None and dl.deadlock_count == 1
    detour = dl.yielding[B].detour
    assert detour.destination == "F" and rm.resources["ROOM_E"].holder == B
    assert swap.tick(22.1) == [detour]
    assert rm.resources["CORRIDOR"].holder == B
    swap.pose(B, MID)
    assert not swap.tick(23)
    assert swap.tick(24) == ([swap.legs[A]] if shared else [])
    assert rm.resources["ROOM_E"].state == ("GRANTED" if shared else "FREE")
    swap.pose(B, cfg.zones["F"])
    assert not swap.tick(25)
    retry = dl.arrived(detour, 25)
    assert retry.robot == B and retry.destination == "B" and retry.mission_id == swap.legs[B].mission_id
    assert dl.arrived(detour, 25) is None

    assert not swap.tick(26)
    assert rm.resources["CORRIDOR"].state == ("GRANTED" if shared else "FREE")
    assert swap.tick(27) == ([] if shared else [swap.legs[A]])
    swap.pose(A, MID)
    assert not swap.tick(28)
    assert swap.tick(29) == ([retry] if shared else [])
    assert rm.resources["ROOM_B"].state == ("GRANTED" if shared else "FREE")
    swap.pose(A, cfg.zones["E"])
    assert not swap.tick(30)
    assert rm.finish(swap.legs[A], 30)
    assert not swap.tick(31)
    assert swap.tick(32) == ([] if shared else [retry])
    swap.pose(B, MID)
    swap.tick(33)
    swap.pose(B, cfg.zones["B"])
    swap.tick(34)
    assert rm.finish(retry, 34)
    swap.tick(35)
    assert not rm.active and not rm.pending and not dl.yielding
    assert rm.resources["ROOM_B"].holder == B and rm.resources["ROOM_E"].holder == A
    assert rm.resources["CORRIDOR"].state == "FREE"
    assert dl.resolved_count == 1 and dl.mean_resolve_s == 22
    kinds = [event["type"] for event in rm.events]
    for kind in ("DEADLOCK_DETECTED", "CARD_OPEN", "CARD_CLOSED", "YIELD_START", "YIELD_DONE"):
        assert kinds.count(kind) == 1


@pytest.mark.parametrize(("levels", "since", "patients", "choice"), [
    ((1, 3), (0, 0), (True, False), "yield_a"),
    ((2, 2), (1, 0), (True, False), "yield_a"),
    ((2, 2), (0, 0), (False, True), "yield_a"),
    ((2, 2), (0, 0), (False, False), "yield_b"),
])
def test_recommendation_order(cfg, levels, since, patients, choice):
    swap = RoomSwap(cfg, levels=levels, since=since, patients=patients)
    swap.tick(max(since) + 2)
    assert swap.deadlock.card["recommended"] == choice


def test_operator_choice_runs_once_and_uses_resource_arbitration(cfg):
    swap = RoomSwap(cfg)
    swap.tick(2)
    card_id = swap.deadlock.card["card_id"]
    swap.deadlock.choose(card_id, "yield_a", "nurse01", 3)
    assert swap.deadlock.yielding[A].detour.destination == "D"
    with pytest.raises(ValueError, match="E-409"):
        swap.deadlock.choose(card_id, "yield_b", "nurse01", 3)
    swap.resources.resources["CORRIDOR"].stall_blockers.add(B)
    assert not swap.tick(4) and swap.resources.resources["CORRIDOR"].state == "FREE"
    swap.resources.resources["CORRIDOR"].stall_blockers.discard(B)
    assert swap.tick(5) == [swap.deadlock.yielding[A].detour]
    closed = next(e for e in swap.resources.events if e["type"] == "CARD_CLOSED")
    assert closed["detail"]["by"] == "nurse01" and closed["detail"]["choice"] == "yield_a"


def test_only_continuous_cycle_counts_and_stale_card_closes(cfg):
    swap = RoomSwap(cfg)
    swap.reports[A] = RobotReport(cfg.zones["B"], True, safe_pause=True)
    swap.tick(1)
    swap.pose(A, cfg.zones["B"])
    swap.tick(2)
    swap.tick(3.99)
    assert swap.deadlock.card is None
    swap.tick(4)
    card_id = swap.deadlock.card["card_id"]
    swap.resources.finish(swap.legs[B], 5)
    swap.tick(5)
    assert swap.deadlock.card is None and not swap.deadlock.yielding
    with pytest.raises(ValueError, match="E-409"):
        swap.deadlock.choose(card_id, "yield_b", "nurse01", 5)
    assert any(e["type"] == "CARD_CLOSED" and e["detail"]["closed_by"] == "resolved"
               for e in swap.resources.events)


def test_three_robots_wait_graph_and_cycle(cfg):
    c = "robot_c"
    fleet = replace(cfg, corridor=replace(cfg.corridor, mode="EXCLUSIVE"), robots={**cfg.robots, c: replace(cfg.robots[A], ns="robot4")})
    rm = ResourceManager(fleet)
    dl = DeadlockManager(rm)
    for robot, destination in ((A, "D"), (B, "B"), (c, "E")):
        rm.request(robot, destination, 1, 0)
    reports = {A: RobotReport(cfg.zones["B"], True), B: RobotReport(cfg.zones["E"], True),
               c: RobotReport(MID, True)}
    assert rm.tick(0, reports).grants == []
    dl.tick(0)
    graph = dl.wait_graph()
    assert {(A, c), (c, B), (B, A)} <= set(graph.edges)
    assert rm.tick(2, reports).grants == []
    dl.tick(2)
    assert dl.card["kind"] == "DEADLOCK"
    assert c in dl.card["robots"]
