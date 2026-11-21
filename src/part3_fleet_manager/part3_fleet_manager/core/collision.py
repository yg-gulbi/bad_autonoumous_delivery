"""Position-based holds independent of mission faults and operator safety stops."""
from collections import deque
from dataclasses import dataclass
from itertools import combinations, pairwise
from math import dist

from .clearance import resume_distance, stop_distance
from .legs import Leg
from .robot_protocol import DONE, Move

PROXIMITY_CARD_S = 10
# A stop request is not confirmation that the chassis is stationary.
IN_MOTION = {"MOVING", "DOCKING", "UNDOCKING", "STOPPING_PAUSE", "STOPPING_CANCELED"}


@dataclass
class Departure:
    leg: Leg
    origin: str | None
    at: float


@dataclass(frozen=True)
class AccessWait:
    leg: Leg
    peer: str
    zone: str
    rule: str
    reason: str
    since: float


class NearStartGuard:
    """Pre-departure 0.9 m moving-peer check and departure log. The plan decides destinations and order (D-43);
    this check stays as a safety net for the first MOVE of a leg."""

    def __init__(self, rm, links, parked_slot):
        self.rm, self.links, self.parked_slot = rm, links, parked_slot
        self.departures = {}
        self.waits = {}

    def sent(self, robot, now):
        leg = self.rm.active.get(robot)
        if leg is None:
            return
        previous = self.departures.get(robot)
        if previous and previous.leg is leg:
            return
        slot = self.parked_slot(robot)
        self.departures[robot] = Departure(leg, slot[0] if slot else None, now)

    def reconcile(self, now):
        for robot, departure in list(self.departures.items()):
            if self.rm.active.get(robot) is not departure.leg:
                del self.departures[robot]
        for robot, wait in list(self.waits.items()):
            if wait.leg is not (self.rm.active.get(robot) or self.rm.pending.get(robot)):
                self._set(wait.leg, None, now)

    def before_departure(self, leg, now):
        robot = leg.robot
        pose = self.links[robot].pose
        slot = self.parked_slot(robot)
        block = None
        for peer, link in self.links.items():
            if peer == robot or pose is None or link.pose is None:
                continue
            starting = link.move and link.move.attempts and not link.move.remote_id and not link.move.finished
            if ((link.state in IN_MOTION or starting or link.control_sent == "RESUME")
                    and dist(pose, link.pose) < resume_distance(self.rm.config.safety, False, False)):
                block = peer, slot[0] if slot else leg.destination, "NEAR_START", "가까이 움직이는 로봇 대기"
                break
        self._set(leg, block, now)
        return block is not None

    def _set(self, leg, block, now):
        robot = leg.robot
        old = self.waits.get(robot)
        if old and block and old.leg is leg and (old.peer, old.zone, old.rule) == block[:3]:
            return
        if old:
            del self.waits[robot]
            self.rm.record("ZONE_OCCUPIED_CLEAR", now, f"{old.reason} 해제", robot=robot,
                           mission_id=old.leg.mission_id, destination=old.leg.destination, zone=old.zone,
                           peer=old.peer, rule=old.rule)
        if block:
            peer, zone, rule, reason = block
            self.waits[robot] = AccessWait(leg, peer, zone, rule, reason, now)
            self.rm.record("ZONE_OCCUPIED_HOLD", now, reason, robot=robot, mission_id=leg.mission_id,
                           destination=leg.destination, zone=zone, peer=peer, rule=rule)


@dataclass
class Wait:
    peer: str
    since: float
    next_card: float
    reason: str
    leg: Leg | None = None


@dataclass
class FirstToMove:
    robot: str
    move: Move
    distance: float


class CollisionControl:
    def __init__(self, resources, links, decisions):
        self.rm, self.links, self.decisions = resources, links, decisions
        self.contacts = {}  # held robot -> peer -> continuous stop
        self.access = NearStartGuard(resources, links, self.parked_slot)
        self.distances = {}
        self.head_on = {}  # pair -> robot recommended to retreat
        self.retreats = {}  # awaiting cancellation and the new return leg
        self.priority = {}  # pair -> one operator-authorized departure

    @property
    def near(self):
        # One primary reason per robot for the display and decision card.
        return {robot: next(iter(peers.values())) for robot, peers in self.contacts.items()}

    def _close(self, robot, context, now):
        for card in list(self.decisions.cards.values()):
            if card.get("context") == context and robot in card["robots"]:
                self.decisions.close(card["card_id"], now, "resolved")

    def parked_slot(self, robot):
        """(zone, slot) whose point is this robot's nearest slot within zone_occupied_m."""
        pose, config = self.links[robot].pose, self.rm.config
        if pose is None:
            return None
        best = min(((dist(pose, point), zone, index) for zone in config.zones
                    for index, point in enumerate(config.slots[zone], 1)), default=None)
        return best[1:] if best and best[0] <= config.resources.zone_occupied_m else None

    def reconcile(self, now):
        self.access.reconcile(now)

    def _clear_near(self, robot, peer, now, reason, *, close=True):
        peers = self.contacts.get(robot, {})
        if peer not in peers:
            return
        if close and peer == next(iter(peers)):
            self._close(robot, "PROXIMITY", now)
        wait = peers.pop(peer)
        if not peers:
            del self.contacts[robot]
        self.rm.record("PROXIMITY_CLEAR", now, reason, robot=robot, peer=peer,
                       mission_id=wait.leg.mission_id if wait.leg else None)

    def yielding_priority(self, robot):
        return (any(robot in pair and first.robot != robot for pair, first in self.priority.items())
                or any(robot in pair and retreat[0] != robot for pair, retreat in self.retreats.items()))

    def _hold_near(self, robot, peer, distance, now):
        first = self.priority.get(frozenset((robot, peer)))
        if first and first.robot == robot:
            return
        peers = self.contacts.setdefault(robot, {})
        if peer in peers:
            return
        reason = f"{self.rm.robot_label(peer)}과 너무 가까움 → 정지"
        leg = self.rm.active.get(robot)
        peers[peer] = Wait(peer, now, now + PROXIMITY_CARD_S, reason, leg)
        self.rm.record("PROXIMITY_STOP", now, reason, robot=robot, peer=peer, distance_m=distance,
                       rule="PROXIMITY", mission_id=leg.mission_id if leg else None)

    def _still_at_slot(self, robot):
        """Standing still at a slot without a granted leg, online or not (clearance.py)."""
        return self.rm.robots.standing_still(robot, robot in self.rm.active) and self.parked_slot(robot) is not None

    def stop_distance(self, a, b):
        return stop_distance(self.rm.config.safety, self._still_at_slot(a), self._still_at_slot(b))

    def resume_distance(self, a, b):
        return resume_distance(self.rm.config.safety, self._still_at_slot(a), self._still_at_slot(b))

    def _separating(self, pair, stamp, distance, now):
        history = self.distances.setdefault(pair, deque())
        if not history or stamp > history[-1][0]:
            history.append((stamp, distance))
        while len(history) > 1 and history[1][0] <= stamp - 0.5:
            history.popleft()
        # A sparse or stale pose stream cannot establish a half-second trend.
        return (now - stamp <= 0.5 and history[0][0] <= stamp - 0.5
                and len(history) >= 2 and distance > history[-2][1]
                and all(t2 - t1 <= 0.500001 and d2 >= d1
                        for (t1, d1), (t2, d2) in pairwise(history)))

    def _face_to_face(self, pair, mover, now):
        departed = self.access.departures
        candidates = [r for r in self.links if r in pair and r in departed]
        wire_order = tuple(self.links)
        self.head_on[pair] = max(candidates, key=lambda r: (departed[r].at, wire_order.index(r))) if candidates else mover
        for robot in pair:
            self._close(robot, "PROXIMITY", now)

    def arm_retreat(self, mission, now):
        for pair, (robot, job, destination) in list(self.retreats.items()):
            unit, link = mission.robots[robot], self.links[robot]
            peer, = pair - {robot}
            if unit.job is not job or unit.target_override != destination or unit.canceling:
                del self.retreats[pair]
                continue
            if (not unit.replacing and link.move and not link.move.attempts and link.move.zone == destination
                    and self.can_go_first(robot, peer, mission, now)):
                distance = dist(link.pose, self.links[peer].pose)
                self.priority[pair] = FirstToMove(robot, link.move, distance)
                self._clear_near(robot, peer, now, "운영자가 이전 구역으로 되돌아가기를 선택했습니다.")
                del self.retreats[pair]

    def observe_distance(self, now):
        for a, b in combinations(self.links, 2):
            left, right = self.links[a], self.links[b]
            if left.pose is None or right.pose is None:
                continue
            distance = dist(left.pose, right.pose)
            pair = frozenset((a, b))
            separating = self._separating(pair, max(left.pose_at, right.pose_at), distance, now)
            if distance >= self.resume_distance(a, b) and left.online(now) and right.online(now):
                self.priority.pop(pair, None)
                self.head_on.pop(pair, None)
                self.retreats.pop(pair, None)
                for robot, peer in ((a, b), (b, a)):
                    self._clear_near(robot, peer, now, "로봇 사이에 재개 간격이 확보됐습니다.")
                continue
            first = self.priority.get(pair)
            if first:
                robot = first.robot
                peer = b if robot == a else a
                link = self.links[robot]
                if first.move is not link.move or first.move.finished:
                    del self.priority[pair]
                    if peer in self.rm.active:
                        self._hold_near(peer, robot, distance, now)
                elif (not self.links[peer].online(now) or self.links[peer].state not in DONE | {"PAUSED"}
                      or distance < first.distance):
                    del self.priority[pair]
                    if distance < first.distance:
                        self._face_to_face(pair, robot, now)
                    self._hold_near(robot, peer, distance, now)
                    if peer in self.rm.active:
                        self._hold_near(peer, robot, distance, now)
                else:
                    first.distance = distance
            fresh = left.online(now) and right.online(now) and now - min(left.pose_at, right.pose_at) <= 0.5
            if not (separating and fresh) and distance < self.stop_distance(a, b):
                for robot, peer in ((a, b), (b, a)):
                    if self.links[robot].state in {"MOVING", "DOCKING"} or robot in self.contacts:
                        self._hold_near(robot, peer, distance, now)

    def hold_restart(self, robot, now):
        """A different rule may release a PAUSED robot while the pair is still too close."""
        pose = self.links[robot].pose
        for peer, link in self.links.items():
            if peer == robot or pose is None or link.pose is None:
                continue
            distance = dist(pose, link.pose)
            if distance < self.stop_distance(robot, peer):
                self._hold_near(robot, peer, distance, now)
        return robot in self.contacts

    def can_go_first(self, robot, peer, mission, now):
        unit, link, other = mission.robots[robot], self.links[robot], self.links[peer]
        # An unacknowledged MOVE on the yielding robot can still start it.
        peer_still = (other.state in DONE | {"PAUSED"} and
                      not (other.move and other.move.attempts and not other.move.remote_id and not other.move.finished))
        return bool(peer_still and other.online(now) and link.online(now) and link.move and not link.move.finished
                    and (link.state == "PAUSED" or (link.state in DONE and not link.move.attempts))
                    and unit.job and not (unit.fault_stop or unit.canceling or unit.replacing or mission.safety.causes[robot])
                    and robot not in self.access.waits and not self.yielding_priority(robot)
                    and not self.rm.reports[robot].stalled
                    and not (self.contacts.get(robot, {}).keys() - {peer}))

    def cards(self, now, robots):
        for robot, wait in self.near.items():
            unit = robots[robot]
            if now < wait.next_card or unit.fault_stop or unit.replacing or unit.canceling:
                continue
            mission_id = unit.job.journal_id if unit.job else None
            pair = frozenset((robot, wait.peer))
            if pair in self.head_on:
                if self.head_on[pair] != robot or pair in self.retreats or pair in self.priority:
                    continue
                self.decisions.open("BLOCKED", robot, mission_id, "두 로봇이 마주 보고 있어 한쪽이 물러나야 합니다", now,
                                    context="PROXIMITY", options={"return": "되돌아가기", "hold": "보류"},
                                    extra={"head_on": True, "recommended": "return", "peer": wait.peer})
                continue
            options = {"retry": "다시 확인", "return": "되돌아가기", "hold": "보류", "self_first": "이 로봇 먼저 보내기",
                       "peer_first": f"{self.rm.robot_label(wait.peer)} 먼저 보내기"}
            self.decisions.open("BLOCKED", robot, mission_id, wait.reason, now, context="PROXIMITY", options=options)

    def choose(self, card, choice, now, mission, by):
        robot = card["robots"][0]
        wait = self.near.get(robot)
        if not wait:
            raise ValueError("E-409: 대기가 이미 해제됐습니다.")
        unit = mission.robots[robot]
        if choice in ("peer_first", "self_first"):
            mover, other = (wait.peer, robot) if choice == "peer_first" else (robot, wait.peer)
            if not self.can_go_first(mover, other, mission, now):
                raise ValueError("E-409: 해당 로봇이 먼저 출발할 조건을 확인하세요.")
            distance = dist(self.links[mover].pose, self.links[other].pose)
            self.priority[frozenset((mover, other))] = FirstToMove(mover, self.links[mover].move, distance)
            wait.next_card = now + PROXIMITY_CARD_S
            self._clear_near(mover, other, now, "운영자가 이 로봇을 먼저 보내기로 했습니다.", close=mover != robot)
        elif choice == "retry":
            wait.next_card = now + PROXIMITY_CARD_S
        elif choice == "hold":
            if not unit.job:
                mission.safety.pause(robot, "MANUAL", now, by=by)
                wait.next_card = now + PROXIMITY_CARD_S
                return
            unit.state, unit.fault_stop = "FAULT", True
            self.rm.record("FAULT", now, "운영자가 충돌 방지 대기 중 임무를 보류했습니다.",
                           robot=robot, mission_id=unit.job.journal_id, by=by)
        elif choice == "return":
            if not unit.job:
                raise ValueError("E-409: 돌아갈 구간 기록이 없습니다.")
            unit.state = "FAULT"
            mission.retry(robot, now, back=True)
            if card.get("head_on"):
                pair = frozenset((robot, wait.peer))
                departure = self.access.departures.get(robot)
                if departure and departure.origin:
                    unit.target_override = departure.origin
                self.retreats[pair] = robot, unit.job, unit.target_override
