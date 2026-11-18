"""One hospital_nav protocol session per namespace. All times are supplied by the caller."""

from dataclasses import dataclass
import json
from math import isfinite

from .config import RobotLinkCfg

DONE = {"IDLE", "COMPLETE", "CANCELED", "FAILED"}
STATES = DONE | {"MOVING", "UNDOCKING", "STOPPING_CANCELED", "STOPPING_PAUSE", "PAUSED"}
EVENT_STATE = {
    "MISSION_ACCEPTED": "MOVING", "NAVIGATING": "MOVING", "RESUMED": "MOVING",
    "PAUSED": "PAUSED", "ARRIVED": "COMPLETE", "MISSION_FAILED": "FAILED",
    "MISSION_CANCELED": "CANCELED", "UNDOCKING": "UNDOCKING",
}


@dataclass
class Move:
    leg_id: str
    zone: str
    level: int
    attempts: int = 0
    sent_at: float | None = None
    tagged: bool = False
    remote_id: str | None = None
    finished: bool = False


def number(value):
    if type(value) in (int, float):
        try:
            if isfinite(value):
                return float(value)
        except OverflowError:
            pass
    return None


class RobotProtocol:
    def __init__(self, ns: str, policy: RobotLinkCfg):
        self.ns = ns
        self.policy = policy
        self.tagged = {"always": True, "never": False, "auto": None}[policy.request_id]
        self.move: Move | None = None
        self.state = None
        self.remote_id = None
        self.state_at = self.pose_at = float("-inf")
        self.pose = self.percent = self.remaining_m = self.battery = self.docked = None
        self.last_event = None
        self.home_cancel = False
        self.cancel_wait = False
        self.cancel_id = None
        self.cancel_unaccepted = False
        self.cancel_sent_at = None
        self.control_sent = None
        self.retired = set()

    def online(self, now):
        return now - self.state_at <= self.policy.link_timeout_s

    def update_pose(self, xy, stamp):
        if (isinstance(xy, dict) and number(xy.get("x")) is not None
                and number(xy.get("y")) is not None and stamp >= self.pose_at):
            self.pose = (xy["x"], xy["y"])
            self.pose_at = stamp

    def forget(self):
        if self.move and self.move.remote_id:
            self.retired.add(self.move.remote_id)
        self.move = None
        self.control_sent = None

    def cancel(self, *, home=False):
        self.home_cancel = home
        self.cancel_wait = True
        self.cancel_sent_at = None
        self.cancel_unaccepted = bool(self.move and self.move.attempts and not self.move.remote_id
                                      and self.state in DONE)
        if self.cancel_unaccepted:
            self.cancel_id = None  # acceptance may still be in flight
        else:
            self.cancel_id = self.remote_id
        self.control_sent = None

    def go(self, leg_id, zone, level):
        if self.state in {"PAUSED", "STOPPING_PAUSE", "STOPPING_CANCELED"} and not self.cancel_wait:
            self.cancel()
        self.forget()
        self.move = Move(leg_id, zone, level)

    def _telemetry(self, data, stamp):
        self.update_pose(data.get("pose"), stamp)
        for key, attr in (("percent", "percent"), ("remaining_m", "remaining_m")):
            if key in data:
                setattr(self, attr, number(data[key]))
        if type(data.get("docked")) is bool:
            self.docked = data["docked"]

    def _canceled(self, remote):
        if self.cancel_wait and (self.cancel_id is None or remote == self.cancel_id):
            if remote:
                self.retired.add(remote)
                if self.move and self.move.remote_id == remote:
                    self.move.finished = True
            self.cancel_wait = False
            self.cancel_unaccepted = False
            self.cancel_sent_at = self.cancel_id = self.control_sent = None
            self.state = "CANCELED"
            return True
        return False

    def receive(self, text, stamp):
        """Return a correlated domain event, a warning, or None for telemetry/stale events."""
        try:
            data = json.loads(text)
        except (TypeError, ValueError, RecursionError):
            return {"event": "WARNING", "reason": "잘못된 hospital_nav JSON"}
        if not isinstance(data, dict) or data.get("robot_id") != self.ns:
            return {"event": "WARNING", "reason": "hospital_nav 로봇 식별자가 일치하지 않습니다."}
        remote = data.get("mission_id")
        if remote is not None and not isinstance(remote, str):
            return {"event": "WARNING", "reason": "잘못된 hospital_nav mission_id"}
        if data.get("type") == "STATE":
            state = data.get("state")
            if not isinstance(state, str) or stamp < self.state_at:
                return None
            self.state_at = stamp
            self.update_pose(data.get("pose"), stamp)
            if (self.home_cancel and self.cancel_wait and self.cancel_sent_at is not None
                    and stamp >= self.cancel_sent_at and state in DONE
                    and remote in (self.cancel_id, None)):
                self._canceled(self.cancel_id)
                self._telemetry(data, stamp)
                self.state, self.remote_id = state, remote
                return {"event": "CANCEL_CONFIRMED", "at_home": state == "COMPLETE"}
            if state == "CANCELED" and self._canceled(remote):
                return {"event": "CANCEL_CONFIRMED"}
            if (self.cancel_unaccepted and self.cancel_sent_at is not None and stamp > self.cancel_sent_at
                    and state in DONE and self._canceled(remote)):
                # Legacy firmware silently ignores both an invalid MOVE and CANCEL while idle.
                self.state, self.remote_id = state, remote
                return {"event": "CANCEL_CONFIRMED"}
            if self.move and self.move.remote_id and remote != self.move.remote_id:
                return None
            self._telemetry(data, stamp)
            self.remote_id, self.state = remote, state
            self._control_ack(state)
            if state not in STATES:
                return {"event": "WARNING", "reason": f"알 수 없는 PART 04 상태 {state}"}
            return None
        event = data.get("event")
        if data.get("type") != "STATUS" or not isinstance(event, str):
            return {"event": "WARNING", "reason": "알 수 없는 hospital_nav 보고"}
        self.last_event = event
        if event == "MISSION_CANCELED" and self._canceled(remote):
            return {"event": "CANCEL_CONFIRMED"}
        move = self.move
        if event == "COMMAND_REJECTED":
            if (move and move.attempts and not move.finished and not move.remote_id
                    and data.get("request_id") == move.leg_id
                    and str(data.get("command", "")).split(" ")[0] == "MOVE"):
                move.finished = True
                self.tagged = True if self.policy.request_id == "auto" else self.tagged
                reported = data.get("state")
                if isinstance(reported, str):
                    self.state, self.remote_id = reported, remote
                return {"event": "MOVE_REJECTED", "reason": str(data.get("reason", event))}
            if (self.home_cancel and self.control_sent == "CANCEL" and data.get("command") == "CANCEL"
                    and (remote is None or remote == self.cancel_id) and self._canceled(self.cancel_id)):
                return {"event": "CANCEL_CONFIRMED", "at_home": data.get("state") == "COMPLETE"}
            if data.get("command") in ("PAUSE", "RESUME", "CANCEL") and data["command"] == self.control_sent:
                self.control_sent = None
                return {"event": "WARNING", "reason": f"{data['command']} 거절: {data.get('reason', '')}"}
            return None
        if event not in EVENT_STATE and event not in {"PROGRESS", "UNDOCKED"}:
            return {"event": "WARNING", "reason": f"알 수 없는 PART 04 이벤트 {event}"}
        if not move or not remote or remote in self.retired or move.finished:
            return None
        if event == "MISSION_ACCEPTED":
            if (not move.attempts or move.remote_id or data.get("destination") != move.zone
                    or (move.tagged and remote != move.leg_id)):
                return None
            move.remote_id = remote
            if self.cancel_wait and self.cancel_id is None:
                self.cancel_id = remote
                self.cancel_unaccepted = False
            if self.policy.request_id == "auto":
                self.tagged = remote == move.leg_id
        elif remote != move.remote_id:
            return None
        if event == "ARRIVED" and data.get("destination") != move.zone:
            return None
        self.remote_id = remote
        self._telemetry(data, stamp)
        if event in EVENT_STATE:
            self.state = EVENT_STATE[event]
            self._control_ack(self.state)
        if event in {"ARRIVED", "MISSION_FAILED", "MISSION_CANCELED"}:
            move.finished = True
        if event == "UNDOCKED":
            self.docked = False
        return data

    def _control_ack(self, state):
        if ((state == "PAUSED" and self.control_sent == "PAUSE")
                or (state == "MOVING" and self.control_sent == "RESUME")):
            self.control_sent = None

    def step(self, now, *, stop=False, resume=False):
        """Produce at most one wire command and an optional acceptance failure."""
        if not self.online(now):
            return None, None
        if self.cancel_wait:
            if self.control_sent != "CANCEL" and self.state != "STOPPING_CANCELED":
                self.control_sent = "CANCEL"
                self.cancel_sent_at = now
                return "CANCEL", None
            return None, None
        move = self.move
        if stop:
            if self.state == "MOVING" and self.control_sent != "PAUSE":
                self.control_sent = "PAUSE"
                return "PAUSE", None
            return None, None
        if resume and self.state == "PAUSED" and self.control_sent != "RESUME":
            self.control_sent = "RESUME"
            return "RESUME", None
        if self.state == "MOVING" and self.control_sent == "RESUME":
            self.control_sent = None
        if not move or move.remote_id or move.finished:
            return None, None
        if move.sent_at is not None and now - move.sent_at < self.policy.accept_timeout_s:
            return None, None
        if move.attempts == 2:
            move.finished = True
            return None, "두 차례 MOVE에 MISSION_ACCEPTED 응답이 없습니다."
        if not move.attempts and self.state not in DONE:
            move.finished = True
            return None, f"현재 PART 04 상태 {self.state}에서는 새 MOVE를 보낼 수 없습니다."
        move.tagged = self.tagged if self.tagged is not None else move.attempts == 0
        move.attempts += 1
        move.sent_at = now
        suffix = f" id={move.leg_id}" if move.tagged else ""
        return f"MOVE {move.zone} {move.level}{suffix}", None
