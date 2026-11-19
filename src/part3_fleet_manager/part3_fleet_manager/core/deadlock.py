"""Wait-for cycles, operator choices and yield legs (BRIEF 5.4)."""

from dataclasses import dataclass
from uuid import uuid4

import networkx as nx

from . import geometry as g
from .decisions import close_detail
from .resource_manager import Leg, ResourceManager


@dataclass(frozen=True)
class Yield:
    original: Leg
    detour: Leg


class DeadlockManager:
    """Run after resource arbitration on each tick. Yield legs use that same arbiter."""

    def __init__(self, manager: ResourceManager):
        self.manager = manager
        self.policy = manager.config.deadlock
        self.cycle: tuple[Leg, ...] = ()
        self.first_seen = None
        self.card: dict | None = None
        self.yielding: dict[str, Yield] = {}
        self._options: dict[str, tuple[Leg, str]] = {}
        self._awaiting_release: list[tuple[str, str, float]] = []
        self.deadlock_count = 0
        self.resolved_count = 0
        self.total_resolve_s = 0.0

    def wait_graph(self) -> nx.DiGraph:
        graph = nx.DiGraph()
        for robot in sorted(self.manager.pending):
            if not self.manager.eligible(robot):
                continue
            for name in self.manager.needed(robot):
                resource = self.manager.resources[name]
                if not resource.uncertain:
                    graph.add_edges_from((robot, holder) for holder in resource.blockers(robot))
        return graph

    def _current_cycle(self):
        graph = self.wait_graph()
        # A detour already underway must never be replaced by another detour.
        graph.remove_nodes_from(self.yielding)
        try:
            edges = nx.find_cycle(graph)
        except nx.NetworkXNoCycle:
            return ()
        # Preserve edge order but identify the same cycle from any starting vertex.
        robots = [src for src, _ in edges]
        first = robots.index(min(robots))
        return tuple(self.manager.pending[r] for r in robots[first:] + robots[:first])

    def _open(self, now):
        options = {}
        for leg in self.cycle:
            region = self.manager.config.corridor.region_of(self.manager.reports[leg.robot].pose)
            if region in self.policy.yield_zones:
                option = "yield_" + leg.robot.removeprefix("robot_")
                options[option] = (leg, region)
        if not options:
            return

        def yield_order(option):
            leg, _ = options[option]
            return leg.level, -leg.since, leg.carrying_patient, -self.manager.robot_order[leg.robot]

        recommended = min(options, key=yield_order)
        self._options = options
        robots = [leg.robot for leg in self.cycle]
        reasons = []
        for leg in self.cycle:
            for name in self.manager.needed(leg.robot):
                for holder in self.manager.resources[name].blockers(leg.robot):
                    if holder in robots:
                        reasons.append(f"{self.manager.robot_label(leg.robot)} — {self.manager.resource_label(name)} 빈자리 대기 (사용 중인 로봇: {self.manager.robot_label(holder)})")
        reason = ", ".join(reasons) + "."
        self.card = {
            "v": 1, "card_id": f"K-{uuid4().hex}", "kind": "DEADLOCK", "robots": robots,
            "title": "교착 해소를 위해 비킬 로봇 선택", "reason": reason,
            "options": [{"id": key, "label": f"{self.manager.robot_label(leg.robot)} · {self.manager.zone_label(self.policy.yield_zones[room])}으로 양보"}
                        for key, (leg, room) in options.items()],
            "recommended": recommended, "timeout_s": self.policy.card_timeout_s,
            "default": recommended, "created_at": now,
        }
        self.deadlock_count += 1
        for kind in ("DEADLOCK_DETECTED", "CARD_OPEN"):
            detail = {"card": self.card} if kind == "CARD_OPEN" else {}
            self.manager.record(kind, now, reason, candidates=robots, card_id=self.card["card_id"], **detail)

    def _close(self, now, reason, choice=None, by=None):
        self.manager.record("CARD_CLOSED", now, **close_detail(self.card, reason, choice), candidates=self.card["robots"],
                            card_id=self.card["card_id"], by=by)
        self.card = None
        self._options = {}

    def _start(self, option, now, by, reason):
        leg, room = self._options[option]
        detected_at = self.card["created_at"]
        robots = self.card["robots"]
        self._close(now, reason, choice=option, by=by)
        self.manager.finish(leg, now)
        dest = self.policy.yield_zones[room]
        detour = self.manager.request(leg.robot, dest, leg.level, now,
                                      mission_id=leg.mission_id, carrying_patient=leg.carrying_patient, mission_type=leg.mission_type)
        self.yielding[leg.robot] = Yield(leg, detour)
        self._awaiting_release.append((room, leg.robot, detected_at))
        self.manager.record("YIELD_START", now, f"{self.manager.robot_label(leg.robot)} — {self.manager.resource_label(room)} 양보를 위해 {self.manager.zone_label(dest)} 이동 요청",
                            robot=leg.robot, mission_id=leg.mission_id, candidates=robots, destination=dest, source_room=room, detected_at=detected_at, by=by)
        self.cycle = ()

    def choose(self, card_id: str, choice: str, by: str, now: float):
        """Apply a validated UI decision after the latest reports have been processed."""
        if (not self.card or self.card["card_id"] != card_id or self._current_cycle() != self.cycle
                or now >= self.card["created_at"] + self.policy.card_timeout_s):
            raise ValueError("E-409: 교착이 풀렸거나 카드가 만료됐습니다.")
        if choice not in self._options or not by.strip():
            raise ValueError("E-400: 양보 선택지와 조작자 이름을 확인하세요.")
        self._start(choice, now, by, "selected")

    def _resolved(self, elapsed):
        self.resolved_count += 1
        self.total_resolve_s += elapsed

    @property
    def mean_resolve_s(self):
        return self.total_resolve_s / self.resolved_count if self.resolved_count else None

    def observe(self, now: float):
        """Update the wait graph and open continuously confirmed deadlocks."""
        for item in self._awaiting_release.copy():
            room, robot, detected_at = item
            resource = self.manager.resources[room]
            if resource.state != "UNKNOWN" and robot not in resource.holders:
                self._resolved(now - detected_at)
                self._awaiting_release.remove(item)

        for robot, action in list(self.yielding.items()):
            if (self.manager.pending.get(robot) is not action.detour
                    and self.manager.active.get(robot) is not action.detour):
                del self.yielding[robot]

        cycle = self._current_cycle()
        if cycle != self.cycle:
            if self.card:
                self._resolved(now - self.card["created_at"])
                self._close(now, "resolved")
            self.cycle = cycle
            self.first_seen = now if cycle else None
        if not cycle:
            return
        if self.card is None and now - self.first_seen >= self.policy.confirm_s:
            self._open(now)

    def expire(self, now):
        if self.card and now >= self.card["created_at"] + self.policy.card_timeout_s:
            self._start(self.card["default"], now, None, "timeout")

    def tick(self, now):
        self.observe(now)
        self.expire(now)

    def retry_detour(self, robot, now):
        action = self.yielding.get(robot)
        if action is None:
            return False
        leg = action.detour
        detour = self.manager.request(robot, leg.destination, leg.level, now,
                                      mission_id=leg.mission_id, carrying_patient=leg.carrying_patient, mission_type=leg.mission_type)
        self.yielding[robot] = Yield(action.original, detour)
        return True

    def arrived(self, leg: Leg, now: float) -> Leg | None:
        """Consume an ARRIVED for the granted detour and requeue its original destination."""
        action = self.yielding.get(leg.robot)
        if not action or action.detour is not leg or self.manager.active.get(leg.robot) is not leg:
            return None
        self.manager.finish(leg, now)
        del self.yielding[leg.robot]
        original = action.original
        self.manager.record("YIELD_DONE", now, f"{self.manager.robot_label(leg.robot)} · {self.manager.zone_label(leg.destination)} 도착, 원래 구간 다시 요청",
                            robot=leg.robot, mission_id=leg.mission_id, destination=leg.destination)
        return self.manager.request(leg.robot, original.destination, original.level, now,
                                    mission_id=original.mission_id, carrying_patient=original.carrying_patient, mission_type=original.mission_type)
