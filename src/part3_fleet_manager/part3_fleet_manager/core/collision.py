"""Position-based holds independent of mission faults and operator safety stops."""
from dataclasses import dataclass
from itertools import combinations
from math import dist

from .resource_manager import Leg
from .robot_protocol import DONE, Move

ZONE_CARD_S = 30
PROXIMITY_CARD_S = 10


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
    move: Move | None
    distance: float


class CollisionControl:
    def __init__(self, resources, links, decisions, zone_of=None):
        self.rm, self.links, self.decisions = resources, links, decisions
        self.zone_of = zone_of or (lambda robot: None)  # zone the robot last stopped in (mission)
        self.zone = {}
        self.contacts = {}  # held robot -> peer -> continuous stop
        self.priority = {}  # pair -> one operator-authorized departure

    @property
    def near(self):
        # One primary reason per robot for the display and decision card.
        return {robot: next(iter(peers.values())) for robot, peers in self.contacts.items()}

    def _close(self, robot, context, now):
        for card in list(self.decisions.cards.values()):
            if card.get("context") == context and robot in card["robots"]:
                self.decisions.close(card["card_id"], now, "resolved")

    def _clear_zone(self, robot, now):
        wait = self.zone.pop(robot)
        self._close(robot, "ZONE", now)
        self.rm.record("ZONE_OCCUPIED_CLEAR", now, "목적지 대기가 해제됐습니다.", robot=robot,
                       mission_id=wait.leg.mission_id, destination=wait.leg.destination, peer=wait.peer)

    def parked_slot(self, robot):
        """(zone, slot) whose point is this robot's nearest slot within zone_occupied_m."""
        pose, config = self.links[robot].pose, self.rm.config
        if pose is None:
            return None
        best = min(((dist(pose, point), zone, index) for zone in config.zones
                    for index, point in enumerate(config.slots.get(zone) or (config.zones[zone],), 1)),
                   default=None)
        return best[1:] if best and best[0] <= config.resources.zone_occupied_m else None

    def _slot_holder(self, robot, zone, slot):
        """Another robot standing at this slot, or an active leg heading to it (D-30)."""
        for other in self.links:
            if other == robot:
                continue
            if self.parked_slot(other) == (zone, slot):
                return other
            active = self.rm.active.get(other)
            # A dock approach keeps the whole zone as before 4J.
            if active and active.destination == zone and (active.dock_path or self.rm.slot_of(active) == slot):
                return other
        return None

    def zone_held(self, leg, now):
        robot, goal = leg.robot, leg.destination
        radius = self.rm.config.resources.zone_occupied_m
        peer = None
        if leg.dock_path:
            targets = (self.rm.config.zones[goal], *leg.dock_path)
            for other, link in self.links.items():
                if other == robot:
                    continue
                active = self.rm.active.get(other)
                if ((link.pose is not None and any(dist(link.pose, p) <= radius for p in targets))
                        or (active and active.destination == goal)):
                    peer = other
                    break
        else:
            slot, peer = 1, self._slot_holder(robot, goal, 1)
            # Slot 2 only for a robot whose PART 04 reports slots; earlier PART 04 rejects "MOVE F2".
            if (peer and self.rm.config.slot_count(goal) > 1 and self.links[robot].slots
                    and self._slot_holder(robot, goal, 2) is None):
                slot, peer = 2, None
            if slot == 1:
                self.rm.slot_choice.pop(leg, None)
            else:
                self.rm.slot_choice[leg] = slot
        old = self.zone.get(robot)
        if old and (old.leg is not leg or old.peer != peer):
            self._clear_zone(robot, now)
        if not peer:
            return False
        if robot not in self.zone:
            reason = f"목적지 {self.rm.zone_label(goal)}에 {self.rm.robot_label(peer)} 있음 → {self.rm.robot_label(robot)} 대기"
            self.zone[robot] = Wait(peer, now, now + ZONE_CARD_S, reason, leg)
            self.rm.record("ZONE_OCCUPIED_HOLD", now, reason, robot=robot, mission_id=leg.mission_id,
                           destination=goal, peer=peer, rule="ZONE")
        return True

    def reconcile_zones(self, now):
        for robot, wait in list(self.zone.items()):
            if wait.leg is not (self.rm.active.get(robot) or self.rm.pending.get(robot)):
                self._clear_zone(robot, now)
        # A grant may wait several ticks for a follow hold. Check again before its first MOVE.
        for robot, leg in self.rm.active.items():
            move = self.links[robot].move
            if move and not move.attempts:
                self.zone_held(leg, now)
                if move.zone != "DOCK":
                    move.slot = self.rm.slot_of(leg)

    def _clear_near(self, robot, peer, now, reason):
        peers = self.contacts.get(robot, {})
        if peer not in peers:
            return
        if peer == next(iter(peers)):
            self._close(robot, "PROXIMITY", now)
        wait = peers.pop(peer)
        if not peers:
            del self.contacts[robot]
        self.rm.record("PROXIMITY_CLEAR", now, reason, robot=robot, peer=peer,
                       mission_id=wait.leg.mission_id if wait.leg else None)

    def _preferred(self, robot, other, distance, now):
        pair = frozenset((robot, other))
        preferred = self.priority.get(pair)
        if not preferred or preferred.robot != robot:
            return False
        move = self.links[robot].move
        if preferred.move is None:
            preferred.move = move
        peer = self.links[other]
        if (preferred.move is move and peer.online(now) and peer.state in DONE | {"PAUSED"}
                and distance >= preferred.distance):
            preferred.distance = distance
            return True
        del self.priority[pair]
        return False

    def _hold_near(self, robot, peer, distance, now):
        if self._preferred(robot, peer, distance, now):
            return
        peers = self.contacts.setdefault(robot, {})
        if peer in peers:
            return
        reason = f"{self.rm.robot_label(peer)}과 너무 가까움 → 정지"
        leg = self.rm.active.get(robot)
        peers[peer] = Wait(peer, now, now + PROXIMITY_CARD_S, reason, leg)
        self.rm.record("PROXIMITY_STOP", now, reason, robot=robot, peer=peer, distance_m=distance,
                       rule="PROXIMITY", mission_id=leg.mission_id if leg else None)

    def stop_distance(self, a, b):
        """Proximity stop distance for this pair (D-30).

        A robot standing still at a slot and a robot arriving at or leaving the same zone use
        safety.slot_stop_m: the two slots can be 0.55 m apart (C), and slot 2 lies between
        slot 1 and the hall mouth (A, D, F), so the leaving robot passes beside it.
        """
        config = self.rm.config
        stop = config.safety.proximity_stop_m
        for mover, parked in ((a, b), (b, a)):
            at = self.parked_slot(parked)
            if not at or config.slot_count(at[0]) < 2 or self.links[parked].state in ("MOVING", "DOCKING"):
                continue
            leg = self.rm.active.get(mover)
            if leg is not None and not leg.dock_path and at[0] in (leg.destination, self.zone_of(mover)):
                return min(stop, config.safety.slot_stop_m)
        return stop

    def observe_distance(self, now):
        policy = self.rm.config.safety
        for a, b in combinations(self.links, 2):
            first, second = self.links[a], self.links[b]
            if first.pose is None or second.pose is None:
                continue
            distance = dist(first.pose, second.pose)
            if distance >= policy.proximity_resume_m and first.online(now) and second.online(now):
                self.priority.pop(frozenset((a,b)), None)
                for robot, peer in ((a,b), (b,a)):
                    self._clear_near(robot, peer, now, "로봇 사이에 재개 간격이 확보됐습니다.")
            elif distance < self.stop_distance(a, b):
                for robot, peer in ((a,b), (b,a)):
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

    def cards(self, now, robots):
        for context, waits in (("ZONE", self.zone), ("PROXIMITY", self.near)):
            for robot, wait in waits.items():
                unit = robots[robot]
                if now < wait.next_card or unit.fault_stop or unit.replacing or unit.canceling:
                    continue
                options = {"retry": "다시 확인", "return": "되돌아가기", "hold": "보류"}
                if context == "PROXIMITY":
                    options["peer_first"] = f"{self.rm.robot_label(wait.peer)} 먼저 보내기"
                self.decisions.open("BLOCKED", robot, unit.job.journal_id if unit.job else None, wait.reason, now,
                                    context=context, options=options)

    def choose(self, card, choice, now, mission, by):
        robot = card["robots"][0]
        zone = card["context"] == "ZONE"
        wait = (self.zone if zone else self.near).get(robot)
        if not wait:
            raise ValueError("E-409: 대기가 이미 해제됐습니다.")
        unit = mission.robots[robot]
        if choice == "peer_first":
            peer = wait.peer
            if (not self.links[robot].online(now) or not self.links[peer].online(now)
                    or self.links[robot].state not in DONE | {"PAUSED"}):
                raise ValueError("E-409: 양보할 로봇의 정지 확인을 기다립니다.")
            distance = dist(self.links[robot].pose, self.links[peer].pose)
            self.priority[frozenset((robot, peer))] = FirstToMove(peer, self.links[peer].move, distance)
            wait.next_card = now + PROXIMITY_CARD_S
            self._clear_near(peer, robot, now, "운영자가 이 로봇을 먼저 보내기로 했습니다.")
        elif choice == "retry":
            wait.next_card = now + (ZONE_CARD_S if zone else PROXIMITY_CARD_S)
        elif choice == "hold":
            if not unit.job:
                mission.safety.pause(robot, "MANUAL", now, by=by)
                wait.next_card = now + PROXIMITY_CARD_S
                return
            unit.state, unit.fault_stop = "FAULT", True
            if zone:
                mission.finish_leg(robot, now)
                self.links[robot].forget()
            self.rm.record("FAULT", now, "운영자가 충돌 방지 대기 중 임무를 보류했습니다.",
                           robot=robot, mission_id=unit.job.journal_id, by=by)
        elif choice == "return":
            if not unit.job:
                raise ValueError("E-409: 돌아갈 구간 기록이 없습니다.")
            unit.state = "FAULT"
            mission.retry(robot, now, back=True)
