"""Assign clinical missions and run each robot's confirmed stops and home legs."""
from collections import deque
from dataclasses import dataclass, field
from math import dist, inf

from . import geometry as g
from .decisions import CONFIRM_REASONS, DONE_REASON
from .robot_protocol import DONE, PREPARING

ARRIVED_FROM_STATE = "도착 알림이 빠져 로봇 상태 보고(완료)로 도착을 확인했습니다."


@dataclass
class Mission:
    id: str
    robot: str | None
    kind: str
    level: int
    stops: list
    patient: dict | None
    created_at: float
    status: str = "QUEUED"
    started_at: float | None = None
    finished_at: float | None = None
    index: int = 0
    requested_by: str | None = None
    department: str | None = None
    sends: int = 0

    req_id: str | None = None
    assigned_at: float | None = None
    cancel_reason: str | None = None
    stop_log: list = field(init=False)

    def __post_init__(self):
        self.stop_log = [{"status": "PENDING", "arrived_at": None, "confirmed_at": None, "confirmed_by": None}
                         for _ in self.stops]

    @property
    def journal_id(self):
        # Home travel has its own wire ID and must not alter completed clinical trip metrics.
        return None if self.kind == "RETURN_HOME" else self.id


@dataclass
class Robot:
    zone: str
    queue: deque = field(default_factory=deque)
    job: Mission | None = None
    state: str = "IDLE"
    target_override: str | None = None
    canceling: bool = False
    return_after_cancel_id: str | None = None
    replacing: bool = False
    fault_stop: bool = False
    resume_due: bool = False
    slot: int | None = None  # slot of `zone` where the robot stopped (2 = entrance side / second slot)

    @property
    def returning(self):
        return self.job is not None and self.job.kind == "RETURN_HOME"


class MissionManager:
    def __init__(self, config, resources, deadlock, decisions, safety, links, next_id, *, traffic=None):
        self.config, self.resources, self.deadlock = config, resources, deadlock
        self.decisions, self.safety, self.links = decisions, safety, links
        self.traffic = traffic
        self.next_id = next_id
        self.robots = {r: Robot(cfg.start_zone) for r, cfg in config.robots.items()}
        self.unassigned: list[Mission] = []
        # Preserve the order that arrivals, confirmations and new jobs made robots ready.
        self.ready = {}
        self.dirty = {}
        self.record = resources.record
        # fleet_core sets this in obstacle report mode: True holds the NAV_FAILED card for the photo result.
        self.defer_failure = None

    def submit(self, request, now, *, manual_robot=None):
        kind = request["type"]
        robot = manual_robot
        if robot is None and self.config.assignment.mode == "ROLE":
            robot = self.config.type_to_robot[kind]
        if kind == "PATIENT_TRANSPORT":
            stops = [{"zone": request["pickup"]}, {"zone": request["destination"]}]
        elif kind == "CALL":
            stops = [{"zone": request["destination"]}]
            if "item" in request:
                stops[0]["item"] = request["item"]
        else:
            stops = request["stops"]
        patient = request["patient"] if kind == "PATIENT_TRANSPORT" else None
        job = Mission(self.next_id(), robot, kind, request["level"], stops, patient, now)
        job.req_id = request.get("req_id")
        job.assigned_at = now if robot is not None else None
        job.requested_by = request.get("requested_by")
        job.department = request.get("requesting_department")
        if robot is None:
            self.unassigned.append(job)
        else:
            self.robots[robot].queue.append(job)
            self.ready[robot] = None
        self.dirty[job.id] = job
        self.record("MISSION_CREATED", now, "임무 접수", robot=robot, mission_id=job.id, mission_type=kind, by=job.requested_by)
        return job.id

    def _interruptible_return(self, robot):
        r = self.robots[robot]
        started = r.state in ("SENDING", "MOVING") or (r.state == "HOLD" and robot in self.resources.pending)
        # PLAN: a retreat to the previous zone (target_override) is not a home leg a new mission may cut into.
        retreat = r.target_override and self.config.traffic.mode == "PLAN"
        return (r.returning and self.config.mission.return_interruptible and not r.canceling and not r.replacing
                and not retreat and started and robot not in self.deadlock.yielding
                and self.resources.robot_state(robot, r.state) != "GIVE_WAY")

    def _assign(self, now):
        if not self.unassigned:
            return
        available = []
        for robot, r in self.robots.items():
            link = self.links[robot]
            if (r.queue or r.canceling or self.safety.causes[robot] or not link.online(now)
                    or (link.battery is not None and link.battery <= self.config.assignment.min_battery_pct)):
                continue
            if (r.job is None and r.state == "IDLE") or self._interruptible_return(robot):
                available.append(robot)
        # Stable ordering retains receipt order for ties, including several submissions in one tick.
        self.unassigned.sort(key=lambda job: -job.level)
        while available and self.unassigned:
            job = self.unassigned.pop(0)
            goal = self.config.zones[job.stops[0]["zone"]]

            def rank(robot):
                link = self.links[robot]
                return (-link.battery if link.battery is not None else inf,
                        dist(link.pose, goal) if link.pose is not None else inf, self.resources.robot_order[robot])

            robot = min(available, key=rank)
            self.record("MISSION_ASSIGNED", now, "배터리와 첫 정류장 거리로 배정", robot=robot,
                        mission_id=job.id, candidates=list(available), battery_pct=self.links[robot].battery)
            available.remove(robot)
            job.robot, job.assigned_at = robot, now
            self.dirty[job.id] = job
            self.robots[robot].queue.append(job)
            self.ready[robot] = None

    def finish_leg(self, robot, now):
        leg = self.resources.active.get(robot) or self.resources.pending.get(robot)
        if leg:
            self.resources.finish(leg, now)

    def complete(self, robot, now, canceled=False, *, selected_card=None, by=None):
        r = self.robots[robot]
        job = r.job
        self.finish_leg(robot, now)
        if not r.canceling:
            self.links[robot].forget()
        self.decisions.resolve(robot, now, except_id=selected_card)
        if not r.returning:
            if canceled:
                self._cancel_job(job, now, "운영자 임무 취소", by)
            else:
                job.status, job.finished_at = "DONE", now
                self.record("MISSION_DONE", now, DONE_REASON, result=job.status, robot=robot, mission_id=job.id)
            self.dirty[job.id] = job
        r.job, r.state, r.target_override = None, "IDLE", None
        r.replacing = r.fault_stop = r.resume_due = False
        if not canceled and job.kind in ("CALL", "PATIENT_TRANSPORT", "MED_ROUND") and self.config.mission.return_home:
            home = self.config.robots[robot].start_zone
            r.job = Mission("H-" + job.id, robot, "RETURN_HOME", self.config.mission.return_level,
                            [{"zone": home}], None, now, status="ACTIVE", started_at=now)
            r.state = "HOLD"
        if r.queue or r.returning:
            self.ready[robot] = None

    def advance(self, robot, now):
        r = self.robots[robot]
        r.job.index += 1
        if r.job.index == len(r.job.stops):
            self.complete(robot, now)
        else:
            r.job.stop_log[r.job.index]["status"] = "ACTIVE"
            self.dirty[r.job.id] = r.job
            r.state = "HOLD"
            self.ready[robot] = None

    def _at_home_slot(self, robot):
        """Standing in a home-zone slot: in PLAN, a slot reached by a completed MOVE (a robot stopped on the
        way inside a slot circle is not parked there); otherwise inside a slot region (zone_occupied_m)."""
        home = self.config.robots[robot].start_zone
        parked = self.traffic.parked_zone(robot) if self.traffic is not None else None
        if parked is not None:
            return parked == home
        pose = self.links[robot].pose
        if pose is None:
            return self.robots[robot].zone == home
        best = min(((dist(pose, point), zone, idx) for zone in self.config.zones
                    for idx, point in enumerate(self.config.slots.get(zone) or (self.config.zones[zone],), 1)),
                   default=None)
        return bool(best and best[0] <= self.config.resources.zone_occupied_m and best[1] == home)

    def _maybe_return_after_cancel(self, robot, now):
        r = self.robots[robot]
        job_id = r.return_after_cancel_id
        r.return_after_cancel_id = None
        if not self.config.effective_return_after_cancel:
            return
        if r.queue or r.job is not None or r.canceling or self._at_home_slot(robot):
            return
        home = self.config.robots[robot].start_zone
        r.job = Mission(f"H-{job_id or 'CANCEL'}", robot, "RETURN_HOME", self.config.mission.return_level,
                        [{"zone": home}], None, now, status="ACTIVE", started_at=now)
        r.state = "HOLD"
        self.ready[robot] = None

    def retarget(self, robot, now):
        """PLAN: the new plan sends this robot to another slot first. Stop the MOVE it is driving; the
        leg is requested again once the robot has stopped (the same path as an operator retry)."""
        r, link = self.robots[robot], self.links[robot]
        if not r.job or r.canceling or r.replacing:
            return False
        if link.move and (link.state not in DONE or (link.move.attempts and not link.move.remote_id)):
            r.replacing = True
            link.cancel()
        else:
            self._replan(robot, now)
        return True

    def plan(self, now):
        self._assign(now)
        for robot, r in self.robots.items():
            if not r.queue or not self._interruptible_return(robot) or self.safety.causes[robot]:
                continue
            if robot in self.resources.pending or (self.links[robot].move and not self.links[robot].move.attempts):
                # No MOVE was sent: release the pending home request immediately.
                self.complete(robot, now, canceled=True)
            elif r.state == "MOVING":
                # Reserve the selected robot even while SENDING. Cancel only its accepted home leg.
                r.canceling = True
                self.links[robot].cancel(home=True)
        for robot in self.ready:
            r = self.robots[robot]
            if r.canceling:
                continue
            if r.job is None and r.queue:
                r.job = r.queue.popleft()
                r.job.status, r.state = "ACTIVE", "HOLD"
                r.job.stop_log[r.job.index]["status"] = "ACTIVE"
                self.dirty[r.job.id] = r.job
                self.safety.protect(robot, now)
            if (r.job is None or r.state != "HOLD" or r.replacing
                    or robot in self.resources.pending or robot in self.resources.active):
                continue
            job = r.job
            destination = r.target_override or job.stops[job.index]["zone"]
            dock_path = ()
            unit = self.config.robots[robot]
            if r.returning and not r.target_override and unit.return_to == "DOCK":
                observed = self.links[robot].dock_position
                dock_path = unit.dock.path_end() if unit.dock else ((observed,) if observed else ())
                zones = [z for z, region in g.ZONE_REGIONS.items()
                         if dock_path and all(self.config.corridor.region_of(p) == region for p in dock_path)]
                if not zones:
                    self.fault(robot, "NAV_FAILED", "도크 복귀 경로를 확인할 수 없습니다. 도크 좌표와 구역을 확인하세요.", now)
                    continue
                destination = min(zones, key=lambda z: dist(self.config.zones[z], dock_path[-1]))
            self.resources.request(robot, destination, job.level, now, mission_id=job.journal_id, mission_type=job.kind,
                                   dock_path=dock_path,
                                   carrying_patient=job.kind == "PATIENT_TRANSPORT" and job.index > 0)
        self.ready.clear()

    def granted(self, leg):
        r = self.robots[leg.robot]
        job = r.job
        job.sends += 1
        destination = "DOCK" if leg.dock_path else leg.destination
        slot = None if leg.dock_path else self.resources.slot_of(leg)
        if not leg.dock_path and self.config.traffic.mode == "PLAN":
            # The plan may stop the robot at another slot first to make way (D-45).
            destination, slot = self.resources.move_target(leg)
        self.links[leg.robot].go(f"L-{job.id.removeprefix('M-')}-{job.sends}", destination, leg.level, slot=slot)
        r.state, r.resume_due = "SENDING", False

    def fault(self, robot, kind, reason, now, diagnostic=None, *, card=True):
        r = self.robots[robot]
        if not r.job:
            return
        self.decisions.resolve(robot, now, {"BLOCKED"})
        self.record(kind, now, reason, diagnostic=diagnostic, robot=robot, mission_id=r.job.journal_id)
        self.record("FAULT", now, reason, robot=robot, mission_id=r.job.journal_id)
        r.state, r.fault_stop = "FAULT", True
        if card:
            self.decisions.open(kind if kind != "WARNING" else "NAV_FAILED", robot, r.job.journal_id, reason, now)

    def received(self, robot, data, now):
        r, link = self.robots[robot], self.links[robot]
        event = data["event"]
        if event == "WARNING":
            self.record("WARNING", now, "로봇 상태 또는 명령 응답을 확인하세요.", diagnostic=data["reason"], robot=robot)
            return
        leg = self.resources.active.get(robot)
        if event == "DOCKED":
            if not leg or not leg.dock_path:
                return
            # Reuse the confirmed arrival transition only after physical docking is confirmed.
            event = "ARRIVED"
            data = {**data, "destination": leg.destination}
        if r.canceling:
            if r.returning and event == "MISSION_FAILED":
                return  # Wait for the terminal STATE or cancel reply.
            if r.returning and event == "ARRIVED":
                r.zone = data["destination"]
                r.slot = data.get("slot") if data.get("slot") in (1, 2) else None
                self._from_state(robot, data, now, r.slot)
                self.record("ARRIVE", now, f"{self.resources.zone_label(r.zone, r.slot)} 도착", robot=robot,
                            destination=r.zone, return_home=True, slot=r.slot)
            if r.returning and event == "CANCEL_CONFIRMED" and data.get("at_home"):
                r.zone = leg.destination if leg else self.config.robots[robot].start_zone
            if event in {"CANCEL_CONFIRMED", "ARRIVED", "MISSION_FAILED", "MISSION_CANCELED"}:
                r.canceling = link.cancel_wait = False
                link.forget()
                if r.returning:
                    if r.queue or event == "ARRIVED" or data.get("at_home"):
                        self.complete(robot, now, canceled=True)
                    else:
                        # The replacement was withdrawn while CANCEL was in flight.
                        self._replan(robot, now)
                elif not r.queue:
                    self._maybe_return_after_cancel(robot, now)
                if r.queue:
                    self.ready[robot] = None
            return
        if not r.job:
            return
        if r.replacing and event in {"CANCEL_CONFIRMED", "ARRIVED", "MISSION_FAILED", "MISSION_CANCELED"}:
            link.cancel_wait = False
            self._replan(robot, now)
            return
        if event == "MISSION_ACCEPTED" and r.state == "SENDING" and leg:
            r.state = "MOVING"
            if not r.returning and r.job.started_at is None:
                r.job.started_at = now
                self.dirty[r.job.id] = r.job
            slot = self.resources.slot_of(leg) if not leg.dock_path else None
            self.record("LEG_START", now, f"{self.resources.zone_label(leg.destination, slot)} 이동 시작", robot=robot,
                        mission_id=r.job.journal_id, destination=leg.destination, return_home=r.returning,
                        slot=slot, by=r.job.requested_by if r.job.kind == "GO_ZONE" else None)
        elif event in ("MISSION_FAILED", "MOVE_REJECTED", "MISSION_CANCELED"):
            kind = {"MISSION_FAILED": "NAV_FAILED", "MOVE_REJECTED": "MOVE_REJECTED",
                    "MISSION_CANCELED": "WARNING"}[event]
            reason = {"NAV_FAILED": "목적지까지 주행하지 못했습니다.", "MOVE_REJECTED": "로봇이 이동 요청을 거절했습니다.",
                      "WARNING": "로봇에서 주행이 취소됐습니다."}[kind]
            deferred = kind == "NAV_FAILED" and self.defer_failure is not None and self.defer_failure(robot, data, now)
            self.fault(robot, kind, reason, now, diagnostic=data.get("reason") or event, card=not deferred)
        elif (event == "ARRIVED" and leg and data.get("destination") != leg.destination and self.traffic
              and self.traffic.arrived_aside(robot, data, now)):
            # PLAN: arrival at the slot the plan moved this robot aside to. Not a stop: plan again (D-45).
            r.zone = data["destination"]
            r.slot = data.get("slot") if data.get("slot") in (1, 2) else None
            r.state = "HOLD"
            link.forget()
        elif event == "ARRIVED" and leg and data.get("destination") == leg.destination:
            reported = data.get("slot")
            slot = None if leg.dock_path else reported if reported in (1, 2) else self.resources.slot_of(leg)
            self._from_state(robot, data, now, slot)
            self.record("ARRIVE", now, "도크 복귀 완료" if leg.dock_path else f"{self.resources.zone_label(leg.destination, slot)} 도착", robot=robot,
                        mission_id=r.job.journal_id, destination=leg.destination, return_home=r.returning, slot=slot)
            r.zone, r.slot = leg.destination, slot
            link.forget()
            self.decisions.resolve(robot, now)
            if self.deadlock.arrived(leg, now):
                r.state = "HOLD"
                return
            self.finish_leg(robot, now)
            if r.target_override:
                r.target_override = None
                if self.config.mission.retry_after_return:
                    r.state = "HOLD"
                    self.ready[robot] = None
                else:
                    r.state, r.fault_stop = "FAULT", True
                    self.record("FAULT", now, "이전 구역에 복귀하여 원래 구간의 재시도를 기다립니다.",
                                robot=robot, mission_id=r.job.journal_id)
            else:
                if not r.returning:
                    r.job.stop_log[r.job.index].update(status="ARRIVED", arrived_at=now)
                    self.dirty[r.job.id] = r.job
                if r.job.kind == "CALL" or (self.config.mission.confirm_at_stops
                                           and r.job.kind in ("PATIENT_TRANSPORT", "MED_ROUND")):
                    r.state = "WAIT_CONFIRM"
                else:
                    self.advance(robot, now)

    def _from_state(self, robot, data, now, slot):
        """4K: the arrival came from a COMPLETE STATE because the STATUS ARRIVED was lost."""
        if data.get("from_state"):
            job = self.robots[robot].job
            self.record("ARRIVED_FROM_STATE", now, ARRIVED_FROM_STATE, robot=robot,
                        mission_id=job.journal_id if job else None, destination=data["destination"], slot=slot,
                        part4_mission_id=data.get("mission_id"))

    def _cancel_job(self, job, now, reason, by):
        job.status, job.finished_at, job.cancel_reason = "CANCELED", now, reason
        for stop in job.stop_log[job.index:]:
            stop["status"] = "SKIPPED"
        self.dirty[job.id] = job
        self.record("MISSION_CANCELED", now, reason, robot=job.robot, mission_id=job.id, by=by)

    def cancel(self, mission_id, now, *, selected_card=None, by=None):
        for job in self.unassigned:
            if job.id == mission_id:
                self.unassigned.remove(job)
                self._cancel_job(job, now, "공용 대기열에서 취소", by)
                return
        for robot, r in self.robots.items():
            for job in r.queue:
                if job.id == mission_id:
                    r.queue.remove(job)
                    self._cancel_job(job, now, "대기 임무 취소", by)
                    return
            if not r.job or r.job.id != mission_id:
                continue
            link = self.links[robot]
            # LEGACY: E-423 inside the corridor (unchanged). PLAN: a cancel is a new goal (D-46).
            self.traffic.cancel_policy(robot, r.state, link, now)
            canceled_id = r.job.id
            if link.move and (link.state not in DONE or (link.move.attempts and not link.move.remote_id
                                                        and r.state != "FAULT")):
                r.canceling = True
                link.cancel()
            r.return_after_cancel_id = canceled_id
            # The clinical mission ends now. Confirm the old motion stopped before the next dispatch.
            self.complete(robot, now, canceled=True, selected_card=selected_card, by=by)
            if not r.canceling:
                self._maybe_return_after_cancel(robot, now)
            return
        raise ValueError("E-409: 취소할 임무가 없습니다.")

    def retry(self, robot, now, *, back=False):
        r = self.robots[robot]
        if not r.job or r.canceling or r.replacing or r.state != "FAULT":
            raise ValueError("E-409: 재시도할 구간이 없습니다.")
        if back:
            r.target_override = r.zone
        link = self.links[robot]
        if link.state not in DONE:
            # Keep unentered reservations until the old motion is confirmed stopped.
            r.replacing = True
            link.cancel()
        else:
            self._replan(robot, now)

    def _replan(self, robot, now):
        r = self.robots[robot]
        self.finish_leg(robot, now)
        self.links[robot].forget()
        r.fault_stop, r.replacing, r.state = False, False, "HOLD"
        self.ready[robot] = None
        if r.target_override is None:
            self.deadlock.retry_detour(robot, now)

    def confirm(self, robot, mission_id, result, now, *, by=None):
        r = self.robots[robot]
        if (not r.job or r.job.id != mission_id or r.state != "WAIT_CONFIRM"
                or self.safety.causes[robot] or any(c["robots"] == [robot] and c["kind"] == "STOP_MISMATCH"
                                                    for c in self.decisions.cards.values())):
            raise ValueError("E-409: 확인할 정류장이 없습니다.")
        self.record("CONFIRM_STEP", now, CONFIRM_REASONS[result], result=result, robot=robot, mission_id=mission_id,
                    destination=r.zone, slot=r.slot, by=by)
        stop = r.job.stop_log[r.job.index]
        self.dirty[mission_id] = r.job
        if result == "MISMATCH":
            stop["status"] = "MISMATCH"
            self.decisions.open("STOP_MISMATCH", robot, mission_id, "정류장 확인 결과가 불일치합니다.", now)
        else:
            stop.update(status="CONFIRMED", confirmed_at=now, confirmed_by=by)
            self.advance(robot, now)

    def wire_step(self, robot, now, corridor_command=None, *, traffic_stop=False, stalled=False, hold_restart=None):
        """Combine motion intents once, after safety, arbitration and operator decisions."""
        r, link = self.robots[robot], self.links[robot]
        if r.returning and r.canceling and link.cancel_sent_at is not None:
            if now - link.cancel_sent_at >= link.policy.accept_timeout_s:
                r.canceling = link.cancel_wait = False
                self.fault(robot, "NAV_FAILED", "복귀 취소 후 정지 또는 홈 도착 확인을 받지 못했습니다.", now)
            return None
        move = link.move
        if (move and move.preparing_since is not None and now - move.preparing_since > link.policy.start_timeout_s
                and not r.fault_stop and not r.canceling and not r.replacing):
            self.fault(robot, "NAV_FAILED", "도크에서 출발 준비 시간이 초과됐습니다.", now)
            link.cancel()
        giving_way = self.resources.robot_state(robot, r.state) == "GIVE_WAY"
        local_stop = bool(self.safety.causes[robot]) or r.fault_stop or giving_way
        stop = local_stop or traffic_stop or self.resources.following(robot) or corridor_command == "PAUSE"
        if stop and link.state in PREPARING | {"DOCKING"} and not link.cancel_wait and move and not move.finished:
            # PART 04 accepts PAUSE only while navigating. CANCEL also stops the Create3 action.
            if not r.fault_stop:
                self.fault(robot, "NAV_FAILED", "도크 동작을 중단했습니다. 정지 사유가 풀리면 다시 시도하세요.", now)
            link.cancel()
        if not stop or link.state == "PAUSED" or link.state in DONE:
            # The robot reported a stop (or ended its move), or no stop is needed any more.
            self.decisions.resolve(robot, now, {"STOP_UNCONFIRMED"})
        if (local_stop and r.job) or corridor_command == "RESUME":
            r.resume_due = True
        resume = r.resume_due and (r.state == "MOVING" or (not r.job and link.state == "PAUSED")) and not stop and not stalled
        if resume and link.state == "PAUSED" and hold_restart and hold_restart(robot, now):
            stop, resume = True, False
        if r.state == "FAULT" and not link.cancel_wait and not stop:
            return None
        unconfirmed = link.stop_unconfirmed
        command, error = link.step(now, stop=stop, resume=resume)
        if command == "PAUSE" and link.pause_sends > 1:
            self._stop_event(robot, "STOP_RESENT", now, attempt=link.pause_sends - 1)
        if link.stop_unconfirmed and not unconfirmed:
            self._stop_event(robot, "STOP_UNCONFIRMED", now, attempt=link.pause_sends - 1)
        if command == "PAUSE":
            r.resume_due = True
        if (command == "RESUME" and not link.resend_controls) or (
                not stop and link.state == "MOVING" and link.control_sent != "PAUSE"):
            # PLAN resends an unanswered RESUME (robot_protocol), so the intent stays until STATE is MOVING.
            r.resume_due = False
        if error:
            self.fault(robot, "MOVE_REJECTED", "이동 명령 수락을 확인하지 못했습니다.", now, diagnostic=error)
        return command

    def _stop_event(self, robot, kind, now, *, attempt):
        """4K: PAUSE resent while STATE stays MOVING, or the resends ran out (card for the operator)."""
        job = self.robots[robot].job
        label = self.resources.robot_label(robot)
        mission_id = job.journal_id if job else None
        if kind == "STOP_RESENT":
            self.record(kind, now, f"{label} 정지 보고 없음 → 정지 다시 보냄 ({attempt}번째)", robot=robot,
                        mission_id=mission_id, attempt=attempt, part4_state=self.links[robot].state)
            return
        reason = f"{label}에 정지를 보냈지만 멈췄다는 보고가 없습니다. 현장에서 확인하세요"
        self.record(kind, now, reason, robot=robot, mission_id=mission_id, attempt=attempt,
                    part4_state=self.links[robot].state)
        self.decisions.open("STOP_UNCONFIRMED", robot, mission_id, reason, now)
