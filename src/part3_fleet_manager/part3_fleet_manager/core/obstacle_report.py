"""Obstacle report mode (BRIEF 5.5, D-31): PART 04 sends one photo when Nav2 gives up, PART 06 judges it.

Timeline for one failed leg:
  MISSION_FAILED ("Nav2: ABORTED")  -> hold the NAV_FAILED card up to report_wait_s
  OBSTACLE (same PART 04 mission ID) -> wait up to timeout_s for the first PART 06 result
  verdict OBJECT  -> block the robot's region + obstacle card
          NONE    -> card (4K default retry_if_clear: false: "no box" is not "path clear").
                     With retry_if_clear: one automatic retry after retry_delay_s, a second failure gets a card
          UNKNOWN / no result -> obstacle card "판별 불가", no automatic retry
No OBSTACLE in time -> the ordinary NAV_FAILED card.
"""
from dataclasses import dataclass

from . import obstacle

BOX_OPTIONS = {"retry": "치웠음, 다시 출발", "return": "되돌아가기", "hold": "보류"}


@dataclass
class Failure:
    move: object                  # the failed robot_protocol.Move
    remote: str | None            # its PART 04 mission ID
    failed_at: float
    report: dict | None = None    # OBSTACLE payload
    reported_at: float | None = None
    early: tuple | None = None    # (stamp, payload) that came between the failure and OBSTACLE
    verdict: dict | None = None
    retry_at: float | None = None


class ObstacleReports:
    def __init__(self, core):
        self.core = core
        self.policy = core.config.obstacle
        self.yolo = {}  # latest informational sighting per robot
        self.failures: dict[str, Failure] = {}
        self.views: dict[str, tuple] = {}       # robot -> (observation for the robot card, failed move)
        self.retried: dict[str, tuple] = {}     # robot -> (job, stop index, target) already retried once

    @property
    def active(self):
        return self.policy.enabled and self.policy.mode == "report"

    def _record(self, event_type, robot, now, reason, **detail):
        job = self.core.mission.robots[robot].job
        self.core.resources.record(event_type, now, reason, robot=robot,
                                   mission_id=job.journal_id if job else None, **detail)

    def _key(self, robot):
        r = self.core.mission.robots[robot]
        return (r.job.id, r.job.index, r.target_override) if r.job else None

    def defer(self, robot, data, now):
        """Called on MISSION_FAILED. PART 04 sends OBSTACLE only after an ABORTED goal with a recovery."""
        if not self.active or data.get("reason") != "Nav2: ABORTED":
            return False
        move = self.core.links[robot].move
        self.failures[robot] = Failure(move, move.remote_id if move else None, now)
        self.views.pop(robot, None)
        return True

    def pending(self, robot):
        failure = self.failures.get(robot)
        return bool(failure and failure.report is not None and failure.verdict is None)

    def view(self, robot):
        entry = self.views.get(robot)
        return entry[0] if entry else None

    def report(self, robot, data, now):
        """OBSTACLE status from PART 04, paired with the failed move by its mission ID."""
        if data.get("source") == "yolo":
            label = data.get("class") or "종류 미상"
            confidence = f" {data['conf']:.2f}" if obstacle.finite(data.get("conf")) else ""
            depth = f"약 {data['depth_m']:g} m" if obstacle.finite(data.get("depth_m")) else "거리 미측정"
            text = f"주행 중 장애물 감지 · {label}{confidence} · {depth} · 계속 주행"
            fields = {k: data.get(k) for k in ("class", "conf", "depth_m", "bearing_deg", "map_x", "map_y",
                                                "robot_pose", "destination", "image_stamp")}
            self.yolo[robot] = dict(fields, text=text, at=now, part4_mission_id=data.get("mission_id"))
            self._record("OBSTACLE_REPORT", robot, now, text, **fields, source="yolo",
                         part4_mission_id=data.get("mission_id"))
            return
        failure = self.failures.get(robot)
        paired = bool(self.active and data.get("paired") and failure and failure.report is None
                      and failure.remote is not None and data.get("mission_id") == failure.remote)
        lidar = data.get("lidar_m")
        lidar = lidar if obstacle.finite(lidar) else None
        photo = bool(data.get("image_topic"))
        reason = "장애물 앞에서 주행 포기 · " + ("사진 수신" if photo else "사진 없음")
        if lidar is not None:
            reason += f", 로봇 앞 약 {lidar:.1f} m"
        fields = {key: data.get(key) for key in ("destination", "robot_pose", "captured", "image_topic", "depth_m",
                                                 "lidar_m", "bearing_deg", "map_x", "map_y")}
        self._record("OBSTACLE_REPORT", robot, now, reason, **fields, part4_mission_id=data.get("mission_id"),
                     nav_reason=data.get("reason"), paired=paired, photo=photo)
        if not paired:
            return
        failure.report, failure.reported_at = data, now
        if failure.early:
            stamp, payload = failure.early
            failure.early = None
            self._verdict(robot, failure, obstacle.classify(payload, self.policy, stamp), now)

    def _current(self, robot):
        """The failure still waiting for this robot, unless the operator already acted on it."""
        failure = self.failures.get(robot)
        r, link = self.core.mission.robots[robot], self.core.links[robot]
        if failure and (link.move is not failure.move or r.state != "FAULT" or not r.job or r.canceling or r.replacing):
            self.failures.pop(robot)
            if failure.early:
                stamp, payload = failure.early
                self._result_event(robot, obstacle.classify(payload, self.policy, stamp), stamp, applied=False)
            return None
        return failure

    def results(self, robot, results, now):
        """PART 06 results for this robot: the first one inside the window is the verdict."""
        failure = self._current(robot)
        for stamp, payload in results:
            answer = obstacle.classify(payload, self.policy, stamp)
            if failure and failure.verdict is None and stamp >= failure.failed_at:
                if failure.report is None and failure.early is None:
                    failure.early = (stamp, payload)  # the photo can beat the status message
                    continue
                if failure.report is not None and stamp <= failure.reported_at + self.policy.timeout_s:
                    self._verdict(robot, failure, answer, now)
                    continue
            self._result_event(robot, answer, now, applied=False)

    def _result_event(self, robot, answer, now, *, applied, text=None):
        reason = text if applied else "현재 판단에서 제외한 장애물 판별 결과"
        self._record("OBSTACLE_RESULT", robot, now, reason, **answer, applied=applied, source="report")

    def _verdict(self, robot, failure, answer, now):
        failure.verdict = answer
        lidar = failure.report.get("lidar_m") if obstacle.finite(failure.report.get("lidar_m")) else None
        text = obstacle.report_description(answer, lidar, self.policy)
        view = {**answer, "text": text, "lidar_m": lidar, "source": "report"}
        self.views[robot] = (view, failure.move)
        self._result_event(robot, answer, now, applied=True, text=text)
        kind = answer["kind"]
        key = self._key(robot)
        if kind == "NONE" and self.policy.retry_if_clear and key and self.retried.get(robot) != key:
            failure.retry_at = now + self.policy.retry_delay_s
            view["text"] = f"{text} · {self.policy.retry_delay_s:g}초 뒤 자동 재출발"
            return
        reason = text
        if kind == "NONE" and self.policy.retry_if_clear:
            reason = f"{text} · 자동 재출발 뒤 다시 멈춤 — 로봇이 찍은 사진 확인"
        elif kind == "NONE":
            reason = f"{text} — 길이 비었다는 뜻은 아님, 로봇이 찍은 사진과 현장 확인"
        if kind == "OBJECT":
            self._block(robot, view, reason, now)
        best = obstacle.best_detection(answer, self.policy)
        self.failures.pop(robot, None)
        r = self.core.mission.robots[robot]
        self.core.decisions.open(
            "BLOCKED", robot, r.job.journal_id if r.job else None, reason, now, obstacle_kind=kind,
            options=BOX_OPTIONS, extra={"obstacle": {
                "kind": kind, "text": text, "lidar_m": lidar, "reported_at": failure.reported_at,
                "photo": bool(failure.report.get("image_topic")),
                "label": best["label"] if best else None, "label_ko": best["label_ko"] if best else None,
                "confidence": best["confidence"] if best else None}})

    def _block(self, robot, view, reason, now):
        """Same sharing as a 30 s stall: other robots get no new grant into this region."""
        core = self.core
        pose = core.links[robot].pose
        region = core.traffic.block_robot(robot, pose, now)
        core.blocks[robot] = region
        stall = core.stalls[robot]
        stall.short = stall.long = True  # moving again reports UNBLOCKED, as for a long stall
        stall.obstacle = view
        self._record("BLOCKED", robot, now, reason, region=region)

    def tick(self, robot, now):
        core = self.core
        r, link = core.mission.robots[robot], core.links[robot]
        entry = self.views.get(robot)
        if entry and link.move is not entry[1]:
            self.views.pop(robot)  # a new move, a cancel or a retry replaces the old verdict
        failure = self._current(robot)
        if failure is None:
            return
        if failure.report is None and now >= failure.failed_at + self.policy.report_wait_s:
            self.failures.pop(robot)
            if failure.early:
                stamp, payload = failure.early
                self._result_event(robot, obstacle.classify(payload, self.policy, stamp), now, applied=False)
            core.decisions.open("NAV_FAILED", robot, r.job.journal_id, "목적지까지 주행하지 못했습니다.", now)
            return
        if failure.verdict is None and failure.report is not None and now > failure.reported_at + self.policy.timeout_s:
            self._verdict(robot, failure, {"kind": "UNKNOWN", "at": now, "labels": [], "detections": []}, now)
            return
        if failure.retry_at is not None and now >= failure.retry_at:
            self.failures.pop(robot)
            self.retried[robot] = self._key(robot)
            self._record("OBSTACLE_RETRY", robot, now, f"{self.policy.none_text} → 같은 구간 자동 재출발 (1회)")
            core.mission.retry(robot, now)
            core.stalls[robot].reset()
