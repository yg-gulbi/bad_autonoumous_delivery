"""One hospital_nav protocol session per namespace. All times are supplied by the caller."""

from collections import deque
from dataclasses import dataclass
import json
from math import isfinite

from .config import RobotLinkCfg

DONE = {"IDLE", "COMPLETE", "CANCELED", "FAILED"}
PREPARING = {"UNDOCKING", "LOCALIZING"}
STATES = DONE | PREPARING | {"MOVING", "DOCKING", "STOPPING_CANCELED", "STOPPING_PAUSE", "PAUSED"}
EVENT_STATE = {
    "MISSION_ACCEPTED": "MOVING", "NAVIGATING": "MOVING", "RESUMED": "MOVING",
    "PAUSED": "PAUSED", "ARRIVED": "COMPLETE", "MISSION_FAILED": "FAILED",
    "MISSION_CANCELED": "CANCELED", "UNDOCKING": "UNDOCKING", "UNDOCKED": "LOCALIZING",
    "LOCALIZING": "LOCALIZING", "DOCKING": "DOCKING", "DOCKED": "COMPLETE",
}
# Known STATUS events that do not change the robot state (4J: crossroad leg, obstacle photo).
PASSING_EVENTS = {"PROGRESS", "VIA_PASSED", "OBSTACLE"}


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
    preparing_since: float | None = None
    previous_id: str | None = None
    slot: int | None = None       # 2 only for a robot whose STATE carries "slot"
    via: bool | None = None       # NAVIGATING said via the crossroad (None before NAVIGATING)
    via_passed: bool = False      # VIA_PASSED received for this move
    wire_slot: int | None = None  # slot the last MOVE asked for (1 when it carried no number); None for DOCK
    arrival: dict | None = None   # 4K: a COMPLETE STATE for this move, used if no ARRIVED comes in the same batch


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
        self.battery_at = self.battery_status_at = self.dock_state_at = self.dock_at = float("-inf")
        self.battery_status = self.dock_position = None
        self.last_event = None
        self.warned_states = deque(maxlen=32)
        self.home_cancel = False
        self.cancel_wait = False
        self.cancel_id = None
        self.cancel_unaccepted = False
        self.cancel_sent_at = None
        self.control_sent = None
        # 4K PAUSE confirmation: sends in the current stop (first + resends), last send time, and
        # whether more than control_retries resends went unanswered (operator card).
        self.pause_sends = 0
        self.pause_sent_at = None
        self.stop_unconfirmed = False
        self.retired = set()
        self.slots = False  # STATE has carried a "slot" key at least once (4J PART 04)

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

    def go(self, leg_id, zone, level, slot=None):
        if self.state in {"PAUSED", "STOPPING_PAUSE", "STOPPING_CANCELED"} and not self.cancel_wait:
            self.cancel()
        self.forget()
        self.move = Move(leg_id, zone, level, previous_id=self.remote_id, slot=slot)

    def update_battery(self, percent, stamp):
        value = number(percent)
        if value is not None and 0 <= value <= 100 and stamp >= self.battery_at:
            self.battery, self.battery_at = value, stamp

    def dock_topic(self, docked, stamp):
        if type(docked) is bool and stamp >= self.dock_at and stamp - self.dock_state_at > self.policy.link_timeout_s:
            self.docked, self.dock_at = docked, stamp

    def _dock_report(self, docked, stamp):
        if type(docked) is not bool or stamp < self.dock_state_at:
            return
        self.dock_state_at = stamp
        if stamp + self.policy.link_timeout_s >= self.dock_at:
            self.docked, self.dock_at = docked, stamp

    def _state_sensors(self, data, stamp):
        self.update_pose(data.get("pose"), stamp)
        battery = data.get("battery")
        if isinstance(battery, dict):
            self.update_battery(battery.get("percent"), stamp)
            if stamp >= self.battery_status_at:
                self.battery_status_at = stamp
                self.battery_status = battery.get("status") if isinstance(battery.get("status"), str) else None
        self._dock_report(data.get("docked"), stamp)
        if data.get("docked") is True and self.dock_state_at == stamp and self.pose_at == stamp:
            self.dock_position = self.pose

    def _accept(self, move, remote):
        move.remote_id = remote
        if self.cancel_wait and self.cancel_id is None:
            self.cancel_id = remote
            self.cancel_unaccepted = False
        if self.policy.request_id == "auto" and move.zone != "DOCK":
            self.tagged = remote == move.leg_id

    def _phase(self, state, stamp):
        self.state = state
        move = self.move
        if move and move.remote_id:
            if state in PREPARING:
                if move.preparing_since is None:
                    move.preparing_since = stamp
            else:
                move.preparing_since = None

    def _telemetry(self, data, stamp):
        self.update_pose(data.get("pose"), stamp)
        for key, attr in (("percent", "percent"), ("remaining_m", "remaining_m")):
            if key in data:
                setattr(self, attr, number(data[key]))

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
            if "slot" in data:
                self.slots = True
            self._state_sensors(data, stamp)
            if (self.home_cancel and self.cancel_wait and self.cancel_sent_at is not None
                    and stamp >= self.cancel_sent_at and state in DONE
                    and remote in (self.cancel_id, None)):
                self._canceled(self.cancel_id)
                self._telemetry(data, stamp)
                self.state, self.remote_id = state, remote
                docking = self.move and self.move.zone == "DOCK"
                at_home = state == "COMPLETE" and (not docking or (remote and remote == self.move.remote_id
                                                                   and data.get("docked") is True))
                return {"event": "CANCEL_CONFIRMED", "at_home": bool(at_home)}
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
            self.remote_id = remote
            move = self.move
            # A periodic STATE can survive a lost acceptance or docking event. Bind only
            # a fresh mission for this command and destination, never the preceding leg.
            inferred_accept = (move and move.attempts and not move.finished and not move.remote_id
                               and remote and remote != move.previous_id and remote not in self.retired
                               and data.get("destination") == move.zone and (not move.tagged or remote == move.leg_id)
                               and (state in PREPARING | {"MOVING", "DOCKING"}
                                    or (move.zone == "DOCK" and state == "COMPLETE" and data.get("docked") is True)))
            if inferred_accept:
                self._accept(move, remote)
            self._phase(state, stamp)
            self._control_ack(state)
            if (move and move.zone == "DOCK" and not move.finished and remote and remote == move.remote_id
                    and state == "COMPLETE" and data.get("docked") is True):
                move.finished = True
                return {"event": "DOCKED", "mission_id": remote}
            slot = data.get("slot")
            if (move and move.zone != "DOCK" and not move.finished and remote and remote == move.remote_id
                    and state == "COMPLETE" and data.get("destination") == move.zone
                    and (slot is None or slot == move.wire_slot)):
                # The STATUS ARRIVED may have been lost. settle() uses this only if it did not come.
                move.arrival = {"event": "ARRIVED", "destination": move.zone, "slot": slot,
                                "mission_id": remote, "from_state": True}
            if inferred_accept:
                return {"event": "MISSION_ACCEPTED", "destination": move.zone, "mission_id": remote}
            if state not in STATES and state not in self.warned_states:
                self.warned_states.append(state)
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
                at_home = data.get("state") == "COMPLETE" and (not move or move.zone != "DOCK")
                return {"event": "CANCEL_CONFIRMED", "at_home": at_home}
            if data.get("command") in ("PAUSE", "RESUME", "CANCEL") and data["command"] == self.control_sent:
                self.control_sent = None
                return {"event": "WARNING", "reason": f"{data['command']} 거절: {data.get('reason', '')}"}
            return None
        if event == "READY":
            self.update_pose(data.get("pose"), stamp)
            return None
        if event == "OBSTACLE":
            # Sent after MISSION_FAILED, so the move is already finished. Pair by PART 04 mission ID.
            paired = bool(move and remote and remote == move.remote_id)
            return {**data, "event": "OBSTACLE", "paired": paired}
        if event not in EVENT_STATE and event not in PASSING_EVENTS:
            return {"event": "WARNING", "reason": f"알 수 없는 PART 04 이벤트 {event}"}
        if event in {"DOCKED", "UNDOCKED"} and not move and remote not in self.retired:
            self._dock_report(event == "DOCKED", stamp)
        if not move or not remote or remote in self.retired or move.finished:
            return None
        if event == "MISSION_ACCEPTED":
            if (not move.attempts or move.remote_id or data.get("destination") != move.zone
                    or (move.tagged and remote != move.leg_id)):
                return None
            self._accept(move, remote)
        elif (event == "DOCKED" and move.zone == "DOCK" and move.attempts and not move.remote_id
              and remote != move.previous_id):
            self._accept(move, remote)
        elif remote != move.remote_id:
            return None
        if event in {"DOCKING", "DOCKED"} and move.zone != "DOCK":
            return None
        if event == "ARRIVED" and (data.get("destination") != move.zone or move.zone == "DOCK"):
            return None
        self.remote_id = remote
        self._telemetry(data, stamp)
        if event == "NAVIGATING":
            # The final leg after VIA_PASSED comes with via=null; the move still went through the crossroad.
            move.via = bool(move.via or data.get("via"))
        elif event == "VIA_PASSED":
            move.via_passed = True
        if event in EVENT_STATE:
            state = EVENT_STATE[event]
            if event == "MISSION_ACCEPTED" and self.docked and move.zone != "DOCK":
                state = "UNDOCKING"
            self._phase(state, stamp)
            self._control_ack(self.state)
        if event in {"ARRIVED", "DOCKED", "MISSION_FAILED", "MISSION_CANCELED"}:
            move.finished = True
        if event in {"DOCKED", "UNDOCKED"}:
            self._dock_report(event == "DOCKED", stamp)
        return data

    def settle(self):
        """After a batch of STATUS/STATE: a matching COMPLETE STATE stands for a lost ARRIVED (4K, D-34)."""
        move = self.move
        if move and move.arrival and not move.finished:
            move.finished = True
            return dict(move.arrival)
        return None

    def resend_pause(self):
        """Operator chose [다시 보내기] on the unconfirmed stop card: start a fresh round of PAUSE."""
        self.pause_sends, self.stop_unconfirmed = 0, False

    def _pause(self, now):
        """PAUSE while MOVING, again every control_retry_s until STOPPING_PAUSE or PAUSED (4K, D-32).

        PART 04 ignores PAUSE unless MOVING, so a resend is harmless. After control_retries resends
        the robot is marked unconfirmed and nothing more is sent until the operator decides.
        """
        if self.control_sent != "PAUSE":
            self.pause_sends, self.stop_unconfirmed = 0, False
        if self.state != "MOVING" or self.stop_unconfirmed:
            return None
        if self.pause_sends and now - self.pause_sent_at < self.policy.control_retry_s:
            return None
        if self.pause_sends > self.policy.control_retries:
            self.stop_unconfirmed = True
            return None
        self.control_sent, self.pause_sent_at = "PAUSE", now
        self.pause_sends += 1
        return "PAUSE"

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
            return self._pause(now), None
        self.pause_sends, self.stop_unconfirmed = 0, False
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
            return None, f"두 차례 {'DOCK' if move.zone == 'DOCK' else 'MOVE'}에 MISSION_ACCEPTED 응답이 없습니다."
        if not move.attempts and self.state not in DONE:
            move.finished = True
            return None, f"현재 PART 04 상태 {self.state}에서는 새 이동 명령을 보낼 수 없습니다."
        if move.zone == "DOCK":
            move.attempts += 1
            move.sent_at = now
            move.wire_slot = None
            return "DOCK", None
        move.tagged = self.tagged if self.tagged is not None else move.attempts == 0
        move.attempts += 1
        move.sent_at = now
        suffix = f" id={move.leg_id}" if move.tagged else ""
        # A missing slot number means slot 1. Earlier PART 04 rejects "F2" as a syntax error.
        room = f"{move.zone}2" if move.slot == 2 and self.slots else move.zone
        move.wire_slot = 2 if room != move.zone else 1
        return f"MOVE {room} {move.level}{suffix}", None
