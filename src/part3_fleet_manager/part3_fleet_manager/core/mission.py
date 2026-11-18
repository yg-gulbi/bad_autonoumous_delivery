"""Assign clinical missions and run each robot's confirmed stops and home legs."""
from collections import deque
from dataclasses import dataclass, field
from math import dist, inf

from . import geometry as g
from .robot_protocol import DONE


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
    replacing: bool = False
    fault_stop: bool = False
    resume_due: bool = False

    @property
    def returning(self):
        return self.job is not None and self.job.kind == "RETURN_HOME"


class MissionManager:
    def __init__(self, config, resources, deadlock, decisions, safety, links, next_id):
        self.config, self.resources, self.deadlock = config, resources, deadlock
        self.decisions, self.safety, self.links = decisions, safety, links
        self.next_id = next_id
        self.robots = {r: Robot(cfg.start_zone) for r, cfg in config.robots.items()}
        self.unassigned: list[Mission] = []
        # Preserve the order that arrivals, confirmations and new jobs made robots ready.
        self.ready = {}
        self.dirty = {}
        self.record = resources.record

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
        job.requested_by = request.get("requested_by")
        job.department = request.get("requesting_department")
        if robot is None:
            self.unassigned.append(job)
        else:
            self.robots[robot].queue.append(job)
            self.ready[robot] = None
        self.dirty[job.id] = job
        self.record("MISSION_CREATED", now, "임무 접수", robot=robot, mission_id=job.id, mission_type=kind)
        return job.id

    def _interruptible_return(self, robot):
        r = self.robots[robot]
        started = r.state in ("SENDING", "MOVING") or (r.state == "HOLD" and robot in self.resources.pending)
        return (r.returning and self.config.mission.return_interruptible and not r.canceling and not r.replacing
                and started and robot not in self.deadlock.yielding
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
                        dist(link.pose, goal) if link.pose is not None else inf, robot != "robot_a", robot)

            robot = min(available, key=rank)
            self.record("MISSION_ASSIGNED", now, "배터리와 첫 정류장 거리로 배정", robot=robot,
                        mission_id=job.id, candidates=list(available), battery_pct=self.links[robot].battery)
            available.remove(robot)
            job.robot = robot
            self.dirty[job.id] = job
            self.robots[robot].queue.append(job)
            self.ready[robot] = None

    def finish_leg(self, robot, now):
        leg = self.resources.active.get(robot) or self.resources.pending.get(robot)
        if leg:
            self.resources.finish(leg, now)

    def complete(self, robot, now, canceled=False, *, selected_card=None):
        r = self.robots[robot]
        job = r.job
        self.finish_leg(robot, now)
        if not r.canceling:
            self.links[robot].forget()
        self.decisions.resolve(robot, now, except_id=selected_card)
        if not r.returning:
            job.status, job.finished_at = ("CANCELED" if canceled else "DONE"), now
            self.dirty[job.id] = job
            self.record("MISSION_CANCELED" if canceled else "MISSION_DONE", now, job.status,
                        robot=robot, mission_id=job.id)
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
            r.state = "HOLD"
            self.ready[robot] = None

    def plan(self, now):
        self._assign(now)
        for robot, r in self.robots.items():
            if not r.queue or not self._interruptible_return(robot) or self.safety.causes[robot]:
                continue
            if robot in self.resources.pending:
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
                r.job.started_at, r.job.status, r.state = now, "ACTIVE", "HOLD"
                self.dirty[r.job.id] = r.job
                self.safety.protect(robot, now)
            if (r.job is None or r.state != "HOLD" or r.replacing
                    or robot in self.resources.pending or robot in self.resources.active):
                continue
            job = r.job
            destination = r.target_override or job.stops[job.index]["zone"]
            self.resources.request(robot, destination, job.level, now, mission_id=job.journal_id, mission_type=job.kind,
                                   carrying_patient=job.kind == "PATIENT_TRANSPORT" and job.index > 0)
        self.ready.clear()

    def granted(self, leg):
        r = self.robots[leg.robot]
        job = r.job
        job.sends += 1
        self.links[leg.robot].go(f"L-{job.id.removeprefix('M-')}-{job.sends}", leg.destination, leg.level)
        r.state = "SENDING"

    def fault(self, robot, kind, reason, now):
        r = self.robots[robot]
        if not r.job:
            return
        self.decisions.resolve(robot, now, {"BLOCKED"})
        self.record(kind, now, reason, robot=robot, mission_id=r.job.journal_id)
        self.record("FAULT", now, reason, robot=robot, mission_id=r.job.journal_id)
        r.state, r.fault_stop = "FAULT", True
        self.decisions.open(kind if kind != "WARNING" else "NAV_FAILED", robot, r.job.journal_id, reason, now)

    def received(self, robot, data, now):
        r, link = self.robots[robot], self.links[robot]
        event = data["event"]
        if event == "WARNING":
            self.record("WARNING", now, data["reason"], robot=robot)
            return
        if r.canceling:
            if r.returning and event == "MISSION_FAILED":
                return  # Wait for the terminal STATE or cancel reply.
            if r.returning and event == "ARRIVED":
                r.zone = data["destination"]
                self.record("ARRIVE", now, f"{r.zone} 도착", robot=robot, destination=r.zone, return_home=True)
            if r.returning and event == "CANCEL_CONFIRMED" and data.get("at_home"):
                r.zone = self.config.robots[robot].start_zone
            if event in {"CANCEL_CONFIRMED", "ARRIVED", "MISSION_FAILED", "MISSION_CANCELED"}:
                r.canceling = link.cancel_wait = False
                link.forget()
                if r.returning:
                    if r.queue or event == "ARRIVED" or data.get("at_home"):
                        self.complete(robot, now, canceled=True)
                    else:
                        # The replacement was withdrawn while CANCEL was in flight.
                        self._replan(robot, now)
                if r.queue:
                    self.ready[robot] = None
            return
        if not r.job:
            return
        if r.replacing and event in {"CANCEL_CONFIRMED", "ARRIVED", "MISSION_FAILED", "MISSION_CANCELED"}:
            link.cancel_wait = False
            self._replan(robot, now)
            return
        leg = self.resources.active.get(robot)
        if event == "MISSION_ACCEPTED" and r.state == "SENDING" and leg:
            r.state = "MOVING"
            self.record("LEG_START", now, f"{leg.destination}행 구간 수락", robot=robot,
                        mission_id=r.job.journal_id, destination=leg.destination, return_home=r.returning)
        elif event in ("MISSION_FAILED", "MOVE_REJECTED", "MISSION_CANCELED"):
            kind = {"MISSION_FAILED": "NAV_FAILED", "MOVE_REJECTED": "MOVE_REJECTED",
                    "MISSION_CANCELED": "WARNING"}[event]
            self.fault(robot, kind, str(data.get("reason") or event), now)
        elif event == "ARRIVED" and leg and data.get("destination") == leg.destination:
            self.record("ARRIVE", now, f"{leg.destination} 도착", robot=robot,
                        mission_id=r.job.journal_id, destination=leg.destination, return_home=r.returning)
            r.zone = leg.destination
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
            elif r.job.kind == "CALL" or (self.config.mission.confirm_at_stops
                                          and r.job.kind in ("PATIENT_TRANSPORT", "MED_ROUND")):
                r.state = "WAIT_CONFIRM"
            else:
                self.advance(robot, now)

    def cancel(self, mission_id, now, *, selected_card=None):
        for job in self.unassigned:
            if job.id == mission_id:
                self.unassigned.remove(job)
                job.status, job.finished_at = "CANCELED", now
                self.dirty[job.id] = job
                self.record("MISSION_CANCELED", now, "공용 대기열에서 취소", mission_id=job.id)
                return
        for robot, r in self.robots.items():
            for job in r.queue:
                if job.id == mission_id:
                    r.queue.remove(job)
                    job.status, job.finished_at = "CANCELED", now
                    self.dirty[job.id] = job
                    self.record("MISSION_CANCELED", now, "대기 임무 취소", robot=robot, mission_id=job.id)
                    return
            if not r.job or r.job.id != mission_id:
                continue
            corridor = self.resources.resources[g.CORRIDOR]
            holding = corridor.holders.get(robot)
            pose = self.links[robot].pose
            inside = pose is not None and self.config.corridor.region_of(pose) == g.CORRIDOR
            uncertain = holding and (corridor.uncertain or not self.links[robot].online(now))
            within_margin = holding and holding.state == "OCCUPIED" and (
                pose is None or not self.resources.planner.is_clear(g.CORRIDOR, pose, self.config.resources.clear_margin_m))
            if r.state != "FAULT" and (inside or uncertain or within_margin):
                raise ValueError("E-423: 복도 통과 후 취소하세요.")
            link = self.links[robot]
            if link.move and (link.state not in DONE or (link.move.attempts and not link.move.remote_id
                                                        and r.state != "FAULT")):
                r.canceling = True
                link.cancel()
            # The clinical mission ends now. Confirm the old motion stopped before the next dispatch.
            self.complete(robot, now, canceled=True, selected_card=selected_card)
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

    def confirm(self, robot, mission_id, result, now):
        r = self.robots[robot]
        if (not r.job or r.job.id != mission_id or r.state != "WAIT_CONFIRM"
                or self.safety.causes[robot] or any(c["robots"] == [robot] and c["kind"] == "STOP_MISMATCH"
                                                    for c in self.decisions.cards.values())):
            raise ValueError("E-409: 확인할 정류장이 없습니다.")
        self.record("CONFIRM_STEP", now, result, robot=robot, mission_id=mission_id, destination=r.zone)
        if result == "MISMATCH":
            self.decisions.open("STOP_MISMATCH", robot, mission_id, "정류장 확인 결과가 불일치합니다.", now)
        else:
            self.advance(robot, now)

    def wire_step(self, robot, now, corridor_command=None):
        """Combine motion intents once, after safety, arbitration and operator decisions."""
        r, link = self.robots[robot], self.links[robot]
        if r.returning and r.canceling and link.cancel_sent_at is not None:
            if now - link.cancel_sent_at >= link.policy.accept_timeout_s:
                r.canceling = link.cancel_wait = False
                self.fault(robot, "NAV_FAILED", "복귀 취소 후 정지 또는 홈 도착 확인을 받지 못했습니다.", now)
            return None
        giving_way = self.resources.robot_state(robot, r.state) == "GIVE_WAY"
        stop = bool(self.safety.causes[robot]) or r.fault_stop or giving_way or corridor_command == "PAUSE"
        if stop or corridor_command == "RESUME":
            r.resume_due = True
        resume = r.resume_due and r.state == "MOVING" and not stop
        if r.state == "FAULT" and not link.cancel_wait and not stop:
            return None
        command, error = link.step(now, stop=stop, resume=resume)
        if command == "RESUME" or (not stop and link.state == "MOVING" and link.control_sent != "PAUSE"):
            r.resume_due = False
        if error:
            self.fault(robot, "MOVE_REJECTED", error, now)
        return command
