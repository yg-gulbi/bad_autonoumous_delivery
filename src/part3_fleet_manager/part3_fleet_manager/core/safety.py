"""Latched stop causes; only explicit operator input releases a safety stop."""

from collections import deque
from dataclasses import dataclass
from math import dist


MONITOR_DENIED = "침상 감시가 끊겨 재개할 수 없습니다. 웹캠을 확인하세요"
EXIT_DENIED = "침상 이탈 해제 전에는 재개할 수 없습니다"


@dataclass(frozen=True)
class BedExitSignal:
    value: bool | None = None
    normal_since: float | None = None
    last_rx: float | None = None

    def after_sample(self, value, stamp, policy):
        if value:
            return BedExitSignal(True, None, stamp)
        start = self.normal_since
        if start is None or stamp >= self.last_rx + policy.warn_s:
            start = stamp  # A reception gap does not count as continuous normal readings.
        confirmed = False if stamp >= start + policy.exit_clear_hold_s else self.value
        return BedExitSignal(confirmed, start, stamp)


class BedMonitor:
    """Track Bool receipt times independently of the last reported bed-exit value."""

    window = 3.0

    def __init__(self, policy, targets, record, decisions):
        self.policy, self.targets = policy, targets
        self.record, self.decisions = record, decisions
        self.samples = deque()
        self.enabled = policy.enabled
        self.state, self.warning = ("NEVER" if self.enabled else "OFF"), None
        self.since = self.first_rx = self.last_rx = self.recovery_start = None
        self.rate_ready = None
        self.outage = self.bypass = False
        self.card_id = None

    def blocked(self, now):
        if not self.enabled:
            return False
        overdue = (self.policy.require_at_start if self.last_rx is None
                   else now - self.last_rx >= self.policy.lost_s)
        return not self.bypass and (self.outage or overdue)

    def switch(self, enabled, now):
        """Monitoring on or off (D-51). Off: the webcam samples are still counted, but no WARN, LOST or BACK is
        recorded, no card opens and nothing is blocked. On again: judged afresh from the next tick, as at start."""
        self.enabled = enabled
        self.outage = self.bypass = False
        self.recovery_start = None
        if self.card_id is not None:
            self.decisions.close(self.card_id, now, "resolved")
            self.card_id = None
        if enabled:
            # since=None: the next tick decides as at start (NEVER -> LOST card with require_at_start, a gap -> LOST).
            self.state, self.warning, self.since = ("NEVER" if self.last_rx is None else "OK"), None, None
        else:
            self.state, self.warning, self.since = "OFF", None, now

    def transition(self, state, now, warning=None):
        if (state, warning) == (self.state, self.warning) and self.since is not None:
            return False
        self.state, self.warning, self.since = state, warning, now
        return True

    def lose(self, now, reason):
        if self.outage:
            return
        self.outage = True
        self.record("BED_MONITOR_LOST", now, reason, targets=list(self.targets))
        card = self.decisions.open("BED_MONITOR_LOST", None, None, reason, now, targets=self.targets)
        self.card_id = card["card_id"]

    def trim(self, now):
        while self.samples and self.samples[0] <= now - self.window:
            self.samples.popleft()

    def tick(self, now):
        self.trim(now)
        if not self.enabled:
            return
        if self.last_rx is None:
            if self.transition("NEVER", now):
                if self.policy.require_at_start:
                    self.lose(now, "침상 감시 신호를 아직 받지 못했습니다. 웹캠을 확인하세요.")
                else:
                    self.record("BED_MONITOR_WARN", now, "침상 감시 연결 안 됨")
            return
        gap = now - self.last_rx
        if gap >= self.policy.warn_s:
            self.recovery_start = None
        if gap >= self.policy.lost_s:
            self.transition("LOST", now)
            self.lose(now, "침상 감시 신호가 끊겼습니다. 웹캠을 확인하세요.")
        elif gap >= self.policy.warn_s and not self.outage and self.transition("WARN", now, "gap"):
            self.record("BED_MONITOR_WARN", now, "침상 감시 신호가 지연되고 있습니다.")

    def receive(self, stamp):
        # Detect a gap even when several messages arrive before the next fleet tick.
        if self.last_rx is not None:
            self.tick(stamp)
        if self.first_rx is None:
            self.first_rx = stamp
            self.rate_ready = stamp + self.window
        self.last_rx = stamp
        self.samples.append(stamp)
        if not self.enabled:
            return
        if self.outage:
            self.transition("LOST", stamp)
            if self.recovery_start is None:
                self.recovery_start = stamp
            if stamp - self.recovery_start < self.policy.recover_s:
                return
            self.outage = self.bypass = False
            self.record("BED_MONITOR_BACK", stamp, "침상 감시 신호가 돌아왔습니다. 정지한 로봇은 운영자가 재개하세요.")
            self.decisions.close(self.card_id, stamp, "resolved")
            self.card_id = self.recovery_start = None
            self.rate_ready = self.last_rx + max(0.0, self.window - self.policy.recover_s)
        if stamp >= self.rate_ready and self.rate(stamp) < self.policy.min_rate_hz:
            if self.transition("WARN", stamp, "rate"):
                self.record("BED_MONITOR_WARN", stamp, "침상 감시 불안정")
        else:
            self.transition("OK", stamp)

    def rate(self, now):
        if self.first_rx is None:
            return 0.0
        elapsed = now - self.first_rx
        if elapsed < self.window:
            return (len(self.samples) - 1) / elapsed if elapsed > 0 and self.samples else 0.0
        return len(self.samples) / self.window

    def choose(self, choice, by, now):
        self.decisions.close(self.card_id, now, "selected", choice=choice, by=by)
        self.card_id = None
        if choice == "bypass":
            self.bypass = True
            self.record("BED_MONITOR_BYPASS", now, "운영자가 침상 감시 없이 운행을 선택했습니다.", by=by)

    def snapshot(self, now):
        return {"state": self.state, "since": now if self.since is None else self.since, "last_rx": self.last_rx,
                "rate_hz": round(self.rate(now), 1), "bypass": self.bypass, "warning": self.warning,
                "enabled": self.enabled,
                "require_at_start": self.policy.require_at_start, "targets": list(self.targets),
                "card": self.decisions.cards.get(self.card_id)}


class Safety:
    def __init__(self, config, record, decisions):
        self.targets = config.safety.bed_exit_pause_targets
        self.monitor = BedMonitor(config.safety.bed_monitor, self.targets, record, decisions)
        self.causes = {r: set() for r in config.robots}
        self.use_hazard_topic = config.safety.use_hazard_topic
        self.bump_neighbor_m = config.safety.bump_neighbor_m
        self.bump = dict.fromkeys(config.robots, False)
        self.exit_signal = BedExitSignal()
        self.since = None
        self.record = record

    def pause(self, robot, cause, now, *, by=None):
        if cause not in self.causes[robot]:
            self.causes[robot].add(cause)
            reason = {"BED_MONITOR_LOST": "침상 감시 끊김", "MANUAL": "운영자 안전 정지 요청",
                      "BUMP": "범퍼 충돌", "BED_EXIT": "침상 이탈 감지"}[cause]
            self.record("PAUSE", now, reason, robot=robot, cause=cause, by=by)

    def protect(self, robot, now):
        if robot in self.targets and self.monitor.enabled:
            if self.bed_exit:
                self.pause(robot, "BED_EXIT", now)
            if self.monitor.blocked(now):
                self.pause(robot, "BED_MONITOR_LOST", now)

    @property
    def bed_exit(self):
        return self.exit_signal.value

    def observe(self, value, active, stamp):
        self.monitor.receive(stamp)
        previous = self.bed_exit
        self.exit_signal = self.exit_signal.after_sample(value, stamp, self.monitor.policy)
        if self.bed_exit != previous:
            self.since = stamp
            if self.monitor.enabled:      # off: the value is kept for later, but it is not an alarm (D-51)
                self.record("BED_EXIT" if value else "BED_EXIT_CLEAR", stamp, "침상 이탈" if value else "침상 정상")
        for robot in active:
            self.protect(robot, stamp)

    def set_monitor(self, enabled, now, *, by=None):
        """BED_MONITOR_SET (D-51). Turning it off closes the webcam card but releases no stop: a robot stopped
        for a bed exit or a webcam loss stays stopped until the operator resumes it (now allowed). Turning it on
        again judges the webcam and the bed as at start; a bed exit at that moment stops the targets."""
        if enabled == self.monitor.enabled:
            return
        self.monitor.switch(enabled, now)
        if enabled:
            self.record("BED_MONITOR_ENABLED", now, "침상 감시를 켰습니다.", by=by, targets=list(self.targets))
            if self.bed_exit:
                self.since = now
                self.record("BED_EXIT", now, "침상 이탈")
        else:
            self.record("BED_MONITOR_DISABLED", now, "침상 감시를 껐습니다. 침상 이탈과 감시 끊김으로 로봇을 세우지 않습니다.",
                        by=by, targets=list(self.targets))

    def hazards(self, robot, detections, now, poses):
        """Consume normalized hazard names or mappings from the transport adapter."""
        if not self.use_hazard_topic:
            return
        if isinstance(detections, (str, dict)):
            detections = [detections]
        bumped = any((d.get("type") if isinstance(d, dict) else d) == "BUMP" for d in detections)

        if bumped and not self.bump[robot]:
            origin = poses[robot]
            targets = [robot]
            if origin is not None:
                targets.extend(other for other, pose in poses.items() if other != robot and pose is not None
                               and dist(origin, pose) <= self.bump_neighbor_m)
            self.record("BUMP", now, "범퍼 충돌", robot=robot, targets=targets)
            for target in targets:
                self.pause(target, "BUMP", now)
        self.bump[robot] = bumped

    def resume(self, robots, now, *, individual, by=None):
        reason = self.denial(robots, now, self.bed_exit)
        if reason:
            self.record("RESUME_REJECTED", now, reason, by=by)
            raise ValueError(f"E-409: {reason}")
        for robot in robots:
            causes = self.causes[robot]
            if not causes:
                continue
            if not individual and "BUMP" in causes:
                causes.intersection_update({"BUMP"})
                continue
            causes.clear()
            self.record("RESUME", now, "운영자 재개", robot=robot, by=by)

    def denial(self, robots, now, bed_value):
        if not self.monitor.enabled:
            return None
        if self.monitor.blocked(now) and any(r in self.targets for r in robots):
            return MONITOR_DENIED
        return EXIT_DENIED if bed_value else None
