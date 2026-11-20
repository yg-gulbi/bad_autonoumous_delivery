"""Order departures and arrivals at the physical slots, independently of corridor release."""
from dataclasses import dataclass
from math import dist

from .resource_manager import Leg

# A stop request is not confirmation that the chassis is stationary.
IN_MOTION = {"MOVING", "DOCKING", "UNDOCKING", "STOPPING_PAUSE", "STOPPING_CANCELED"}


@dataclass
class Departure:
    leg: Leg
    origin: str | None
    at: float
    cleared: bool = False
    entered_at: float | None = None


@dataclass(frozen=True)
class AccessWait:
    leg: Leg
    peer: str
    zone: str
    rule: str
    reason: str
    since: float


class ZoneTraffic:
    def __init__(self, rm, links, parked_slot):
        self.rm, self.links, self.parked_slot = rm, links, parked_slot
        self.departures = {}
        self.waits = {}

    def distance(self, robot, zone):
        points = self.rm.config.slots.get(zone) or (self.rm.config.zones[zone],)
        return min(dist(self.links[robot].pose, point) for point in points)

    def sent(self, robot, now):
        leg = self.rm.active[robot]
        previous = self.departures.get(robot)
        if previous and previous.leg is leg:
            return
        slot = self.parked_slot(robot)
        self.departures[robot] = Departure(leg, slot[0] if slot else None, now)

    def inside(self, robot, zone, margin=0.0):
        return (self.links[robot].pose is not None and
                self.distance(robot, zone) <= self.rm.config.resources.zone_arrival_clear_m + margin)

    def reconcile(self, now):
        for robot, departure in list(self.departures.items()):
            if self.rm.active.get(robot) is not departure.leg:
                del self.departures[robot]
                continue
            if departure.origin and self.links[robot].pose and not self.inside(robot, departure.origin):
                departure.cleared = True
            if departure.entered_at is None and self.inside(robot, departure.leg.destination):
                departure.entered_at = now
        for robot, wait in list(self.waits.items()):
            if wait.leg is not (self.rm.active.get(robot) or self.rm.pending.get(robot)):
                self._set(wait.leg, None, now)
        blocks = {}
        for robot, departure in self.departures.items():
            zone = departure.leg.destination
            waiting = self.waits.get(robot)
            if waiting and waiting.rule == "OUTBOUND" and (self.links[waiting.peer].pose is None
                                                          or self.inside(waiting.peer, zone)):
                # A terminal STATE or a replacement command is not physical exit evidence.
                blocks[robot] = waiting.peer, zone, "OUTBOUND", waiting.reason
                continue
            block = None
            for peer, outward in self.departures.items():
                if (robot == peer or outward.origin != zone or outward.leg.destination == zone
                        or outward.cleared or not self.inside(peer, zone)):
                    continue
                # Stop on approach, before entering. Once stopped keep the entrance reserved
                # even if localization drifts back outside the approach margin.
                if self.inside(robot, zone, self.rm.config.resources.clear_margin_m):
                    block = peer, zone, "OUTBOUND", f"{self.rm.zone_label(zone)}에서 나오는 로봇 대기"
                    break
            if block:
                blocks[robot] = block

        # The two approach circles can overlap. Preserve an established turn; otherwise the
        # first robot already inside its destination circle goes first (then first departure).
        for robot, (peer, *_) in list(blocks.items()):
            if robot in blocks and peer in blocks and blocks[peer][0] == robot:
                first = min((robot, peer), key=self._arrival_order)
                del blocks[first]
        for robot, departure in self.departures.items():
            wait = self.waits.get(robot)
            move = self.links[robot].move
            if wait and wait.rule != "OUTBOUND" and not blocks.get(robot) and move and not move.remote_id and not move.finished:
                continue  # an unacknowledged MOVE is rechecked at the wire
            self._set(departure.leg, blocks.get(robot), now)

    def _arrival_order(self, robot):
        departure = self.departures[robot]
        waiting = self.waits.get(robot)
        return (bool(waiting and waiting.rule == "OUTBOUND"),
                departure.entered_at if departure.entered_at is not None else float("inf"),
                departure.at, tuple(self.links).index(robot))

    def before_departure(self, leg, now):
        robot = leg.robot
        pose = self.links[robot].pose
        slot = self.parked_slot(robot)
        old = self.waits.get(robot)
        block = None
        for peer, link in self.links.items():
            if peer == robot or pose is None or link.pose is None:
                continue
            incoming = self.departures.get(peer)
            prior = old and old.leg is leg and old.rule == "INBOUND" and old.peer == peer
            zone = old.zone if prior else slot[0] if slot else None
            peer_wait = self.waits.get(peer)
            already_yielding = peer_wait and peer_wait.rule == "OUTBOUND" and peer_wait.peer == robot
            approaching = (zone and incoming and incoming.leg.destination == zone and incoming.origin != zone
                           and (prior or self.inside(peer, zone)))
            # ARRIVED/COMPLETE can reach the fleet before the pose at the slot.
            peer_slot = self.parked_slot(peer) if prior and not incoming else None
            awaiting_pose = (prior and not incoming and link.state in {"COMPLETE", "IDLE"}
                             and self.inside(peer, zone) and (not peer_slot or peer_slot[0] != zone))
            if not already_yielding and (approaching or awaiting_pose):
                block = peer, zone, "INBOUND", f"{self.rm.zone_label(zone)}에 들어오는 로봇 대기"
                break
            starting = link.move and link.move.attempts and not link.move.remote_id and not link.move.finished
            if ((link.state in IN_MOTION or starting or link.control_sent == "RESUME")
                    and dist(pose, link.pose) < self.rm.config.safety.proximity_resume_m):
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
                           mission_id=old.leg.mission_id, destination=old.leg.destination, zone=old.zone, peer=old.peer, rule=old.rule)
        if block:
            peer, zone, rule, reason = block
            self.waits[robot] = AccessWait(leg, peer, zone, rule, reason, now)
            self.rm.record("ZONE_OCCUPIED_HOLD", now, reason, robot=robot, mission_id=leg.mission_id,
                           destination=leg.destination, zone=zone, peer=peer, rule=rule)
