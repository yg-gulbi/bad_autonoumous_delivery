"""Mission controller for one TurtleBot4: MOVE -> Nav2 -> ARRIVED.

The control console sends the destination (+ priority) and PAUSE/RESUME/CANCEL.
While moving, the robot reports the remaining path distance and progress (%)
to /<robot>/hospital_nav/status every second. There is no traffic control:
watch the robots when two of them share the corridor.
"""
import json
import math
import threading
import time
import uuid
from functools import wraps

from geometry_msgs.msg import PoseWithCovarianceStamped
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

from .goal_manager import get_goal
from .navigation_manager import NavigationManager

# AMCL publishes amcl_pose latched and only when its filter updates, so a
# volatile subscriber never sees the pose of a robot that is standing still.
AMCL_QOS = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                      durability=DurabilityPolicy.TRANSIENT_LOCAL)
NAV_FAILURES = {"ABORTED", "FAILED", "REJECTED", "SERVER_UNAVAILABLE", "CANCELED"}
DONE = {"IDLE", "COMPLETE", "FAILED", "CANCELED"}


def locked(fn):
    @wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._mission_lock:
            return fn(self, *args, **kwargs)
    return wrapper


class MissionManager(Node):
    def __init__(self, robot="robot3"):
        self.robot = robot.strip("/")
        super().__init__("hospital_mission_manager", namespace="/" + self.robot)
        self._mission_lock = threading.RLock()
        self.nav = NavigationManager("/" + self.robot)
        self.status_pub = self.create_publisher(String, f"/{self.robot}/hospital_nav/status", 10)
        self.pose = None
        self.state, self.mission_id, self.dest, self.priority = "IDLE", None, None, 1
        self.reason, self.total = "", None  # total = first path length Nav2 reported
        self.create_subscription(PoseWithCovarianceStamped, f"/{self.robot}/amcl_pose",
                                 self._pose, AMCL_QOS)
        self.create_timer(0.2, self._tick)
        self.create_timer(1.0, self._report)

    @locked
    def _pose(self, msg):
        p = msg.pose.pose.position
        self.pose = float(p.x), float(p.y)

    # ------------------------------------------------------------- commands
    @locked
    def start(self, destination, *, mission_id=None, priority=1):
        if self.state not in DONE or self.nav.is_busy():
            self.get_logger().warning(f"진행 중인 임무가 있어 MOVE 거절 (state={self.state})")
            return False
        if priority not in (1, 2, 3):
            self.get_logger().warning("응급도는 1~3")
            return False
        try:
            dest = str(destination).strip().upper()
            get_goal(dest)
        except ValueError as exc:
            self.get_logger().error(str(exc))
            return False
        self.mission_id = str(mission_id or uuid.uuid4().hex[:12])
        self.dest, self.priority, self.reason, self.total = dest, priority, "", None
        self.emit("MISSION_ACCEPTED", destination=dest, priority=priority)
        return self._go()

    @locked
    def pause(self, reason="관제 PAUSE"):
        if self.state != "MOVING":
            return False
        self.reason = reason
        self.state = "STOPPING_PAUSE"
        self.nav.cancel()
        return True

    @locked
    def resume(self):
        if self.state != "PAUSED":
            return False
        self.emit("RESUMED")
        return self._go()

    @locked
    def cancel_mission(self):
        if self.state in DONE:
            return False
        self.reason = "운영자 취소"
        if self.nav.is_busy():
            self.state = "STOPPING_CANCELED"
            self.nav.cancel()
        else:
            self._finish("CANCELED")
        return True

    def shutdown(self, timeout=10.0):
        """Cancel any mission and wait for Nav2 to confirm (executor must spin)."""
        self.cancel_mission()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._mission_lock:
                if not self.state.startswith("STOPPING_") and not self.nav.is_busy():
                    return True
            time.sleep(0.1)
        return False

    # ---------------------------------------------------------- state machine
    def _go(self):
        if not self.nav.send_pose(get_goal(self.dest), label=self.dest):
            self.reason = "Nav2 Goal 전송 실패"
            self._finish("FAILED")
            return False
        self.state = "MOVING"
        self.emit("NAVIGATING", destination=self.dest)
        return True

    @locked
    def _tick(self):
        if self.state.startswith("STOPPING_"):
            if not self.nav.is_busy():
                outcome = self.state.removeprefix("STOPPING_")
                if outcome == "PAUSE":
                    self.state = "PAUSED"
                    self.emit("PAUSED", reason=self.reason, **self._progress())
                else:
                    self._finish(outcome)
            return
        if self.state != "MOVING":
            return
        nav_state, _ = self.nav.status()
        if nav_state == "SUCCEEDED":
            self.state = "COMPLETE"
            goal = get_goal(self.dest)
            error = round(math.dist(self.pose, (goal.x, goal.y)), 3) if self.pose else None
            self.emit("ARRIVED", destination=self.dest, percent=100.0,
                      pose=self._pose_dict(), goal_error_m=error)
        elif nav_state in NAV_FAILURES:
            self.reason = "Nav2: " + nav_state
            self._finish("FAILED")

    def _finish(self, outcome):
        self.state = outcome
        self.emit("MISSION_" + outcome, reason=self.reason, **self._progress())

    def _progress(self):
        """Remaining path distance and % done, relative to the first path length."""
        remaining = self.nav.remaining()
        if remaining is None:
            return {"remaining_m": None, "percent": None}
        if self.total is None or remaining > self.total:
            self.total = remaining  # first feedback (or a longer replanned path)
        percent = 100.0 * (1.0 - remaining / self.total) if self.total > 0.05 else 0.0
        return {"remaining_m": round(remaining, 2), "percent": round(max(0.0, percent), 1)}

    @locked
    def _report(self):
        if self.state == "MOVING":
            progress = self._progress()
            if progress["remaining_m"] is not None:
                self.emit("PROGRESS", destination=self.dest, **progress)
        self.publish({"type": "STATE", **self.snapshot()})

    # --------------------------------------------------------------- status
    def _pose_dict(self):
        return {"x": round(self.pose[0], 3), "y": round(self.pose[1], 3)} if self.pose else None

    def publish(self, payload):
        self.status_pub.publish(String(data=json.dumps(payload, ensure_ascii=False)))

    def emit(self, event, **fields):
        self.publish({"type": "STATUS", "event": event, "robot_id": self.robot,
                      "mission_id": self.mission_id, **fields})
        if event == "PROGRESS":
            self.get_logger().info(f"[PROGRESS] {self.dest} 남은 거리 {fields['remaining_m']:.2f} m "
                                   f"({fields['percent']:.1f}%)")
        else:
            self.get_logger().info(f"[{event}] {fields}")

    @locked
    def snapshot(self):
        return {"robot_id": self.robot, "mission_id": self.mission_id, "state": self.state,
                "destination": self.dest, "priority": self.priority, "reason": self.reason,
                "pose": self._pose_dict(), **(self._progress() if self.state == "MOVING"
                                              else {"remaining_m": None, "percent": None})}
