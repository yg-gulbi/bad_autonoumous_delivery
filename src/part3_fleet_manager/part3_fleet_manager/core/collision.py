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
    exit_zone: str | None = None


@dataclass
class FirstToMove:
    robot: str
    move: Move
    distance: float


class CollisionControl:
    def __init__(self, resources, links, decisions):
        self.rm, self.links, self.decisions = resources, links, decisions
        self.exit_pass = {}  # robot -> (leg, peer) for this departure only
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

    def _clear_zone(self, robot, now, *, close=True):
        wait = self.zone.pop(robot)
        if close:
            self._close(robot, "EXIT" if wait.exit_zone else "ZONE", now)
        reason = "출구 막힘 대기가 해제됐습니다." if wait.exit_zone else "목적지 대기가 해제됐습니다."
        self.rm.record("ZONE_OCCUPIED_CLEAR", now, reason, robot=robot, mission_id=wait.leg.mission_id,
                       destination=wait.leg.destination, peer=wait.peer, exit_zone=wait.exit_zone)

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
        origin = self.parked_slot(robot)
        exit_zone = old.exit_zone if old and old.leg is leg else None
        if origin and origin[1] == 1 and origin[0] in self.rm.config.exit_blocked_zones:
            exit_zone = origin[0]
        if exit_zone:
            blocker = next((r for r in self.links if r != robot and self.parked_slot(r) == (exit_zone, 2)), None)
            # Even an accepted MOVE is not evidence that the robot has left slot 2.
            if blocker and self.exit_pass.get(robot) != (leg, blocker):
                peer = blocker
            else:
                exit_zone = None
        if old and (old.leg is not leg or old.peer != peer or old.exit_zone != exit_zone):
            self._clear_zone(robot, now)
        if not peer:
            return False
        if robot not in self.zone:
            if exit_zone:
                reason = f"출구 막힘 대기 · {self.rm.zone_label(exit_zone)} 2번 자리에 {self.rm.robot_label(peer)} 있음"
            else:
                reason = f"목적지 {self.rm.zone_label(goal)}에 {self.rm.robot_label(peer)} 있음 → {self.rm.robot_label(robot)} 대기"
            due = now if exit_zone and leg.level == 3 else now + ZONE_CARD_S
            self.zone[robot] = Wait(peer, now, due, reason, leg, exit_zone)
            self.rm.record("ZONE_OCCUPIED_HOLD", now, reason, robot=robot, mission_id=leg.mission_id,
                           destination=goal, peer=peer, rule="EXIT" if exit_zone else "ZONE", exit_zone=exit_zone)
        return True

    def reconcile_zones(self, now):
        for robot, (leg, _) in list(self.exit_pass.items()):
            if leg is not (self.rm.active.get(robot) or self.rm.pending.get(robot)):
                del self.exit_pass[robot]
        for robot, wait in list(self.zone.items()):
            if wait.leg is not (self.rm.active.get(robot) or self.rm.pending.get(robot)):
                self._clear_zone(robot, now)
        # A grant may wait several ticks for a follow hold. Check again before its first MOVE.
        for robot, leg in self.rm.active.items():
            move = self.links[robot].move
            if move and not move.attempts:
                self.zone_held(leg, now)
                if robot in self.exit_pass:
                    self.hold_restart(robot, now)
                if move.zone != "DOCK":
                    move.slot = self.rm.slot_of(leg)

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
        return any(robot in pair and first.robot != robot for pair, first in self.priority.items())

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

    def stop_distance(self, a, b, now):
        """A parked peer at any slot uses the slot clearance, including through traffic."""
        policy = self.rm.config.safety
        for parked in (a, b):
            link = self.links[parked]
            if (parked not in self.rm.active and link.online(now)
                    and link.state in DONE | {"PAUSED"} and self.parked_slot(parked)):
                return min(policy.proximity_stop_m, policy.slot_stop_m)
        return policy.proximity_stop_m

    def observe_distance(self, now):
        policy = self.rm.config.safety
        for a, b in combinations(self.links, 2):
            left, right = self.links[a], self.links[b]
            if left.pose is None or right.pose is None:
                continue
            distance = dist(left.pose, right.pose)
            pair = frozenset((a, b))
            if distance >= policy.proximity_resume_m and left.online(now) and right.online(now):
                self.priority.pop(pair, None)
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
                    self._hold_near(robot, peer, distance, now)
                    if peer in self.rm.active:
                        self._hold_near(peer, robot, distance, now)
                else:
                    first.distance = distance
            if distance < self.stop_distance(a, b, now):
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
            if distance < self.stop_distance(robot, peer, now):
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
                    and robot not in self.zone and not self.yielding_priority(robot) and not self.rm.following(robot)
                    and not self.rm.reports[robot].stalled
                    and self.rm.robot_state(robot, unit.state) != "GIVE_WAY"
                    and not (self.contacts.get(robot, {}).keys() - {peer}))

    def cards(self, now, robots):
        for context, waits in (("ZONE", self.zone), ("PROXIMITY", self.near)):
            for robot, wait in waits.items():
                unit = robots[robot]
                if now < wait.next_card or unit.fault_stop or unit.replacing or unit.canceling:
                    continue
                card_context = "EXIT" if wait.exit_zone else context
                options = {"retry": "다시 확인", "return": "되돌아가기", "hold": "보류"}
                if wait.exit_zone:
                    options = {"retry": "다시 확인", "depart": "그대로 출발(근접 규칙 유지)", "hold": "보류"}
                if context == "PROXIMITY":
                    options["self_first"] = "이 로봇 먼저 보내기"
                    options["peer_first"] = f"{self.rm.robot_label(wait.peer)} 먼저 보내기"
                self.decisions.open("BLOCKED", robot, unit.job.journal_id if unit.job else None, wait.reason, now,
                                    context=card_context, options=options)

    def choose(self, card, choice, now, mission, by):
        robot = card["robots"][0]
        zone = card["context"] in ("ZONE", "EXIT")
        wait = (self.zone if zone else self.near).get(robot)
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
        elif choice == "depart" and wait.exit_zone:
            self.exit_pass[robot] = (wait.leg, wait.peer)
            self._clear_zone(robot, now, close=False)
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
