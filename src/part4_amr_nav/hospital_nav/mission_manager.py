"""Mission controller for one TurtleBot4: (auto UNDOCK) -> MOVE -> ARRIVED -> DOCK.

The robot checks dock_status itself. A MOVE while docked (e.g. console 'A A 2')
undocks first, then waits for Nav2 to be active and a fresh AMCL pose
(LOCALIZING; with power saver the lidar is off on the dock) before driving.
DOCK while docked is ignored. UNDOCK alone is still available.
Whenever the robot sits on its dock (at startup and after DOCKED), the dock
pose is sent as the AMCL initial pose, overriding whatever AMCL believed. READY (shown in the status) means: off
the dock, Nav2 active and an AMCL pose received. While moving, the robot
reports the remaining path distance and progress (%) every second.
There is no traffic control: watch the robots when two share the corridor.
"""
import json
import math
import threading
import time
import uuid
from functools import wraps

from geometry_msgs.msg import PoseWithCovarianceStamped
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import BatteryState
from std_msgs.msg import String

from .dock_manager import DockManager
from .goal_manager import DOCKS, get_goal, predock
from .navigation_manager import NavigationManager

# AMCL publishes amcl_pose latched and only when its filter updates, so a
# volatile subscriber never sees the pose of a robot that is standing still.
AMCL_QOS = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                      durability=DurabilityPolicy.TRANSIENT_LOCAL)
NAV_FAILURES = {"ABORTED", "FAILED", "REJECTED", "SERVER_UNAVAILABLE", "CANCELED"}
DONE = {"IDLE", "COMPLETE", "FAILED", "CANCELED"}
CHARGE_STATUS = {BatteryState.POWER_SUPPLY_STATUS_CHARGING: "CHARGING",
                 BatteryState.POWER_SUPPLY_STATUS_DISCHARGING: "DISCHARGING",
                 BatteryState.POWER_SUPPLY_STATUS_NOT_CHARGING: "NOT_CHARGING",
                 BatteryState.POWER_SUPPLY_STATUS_FULL: "FULL"}
INIT_POSE_TRIES = 60  # x 2 s, counted only while AMCL is listening
POSE_MATCH_M = 0.15   # AMCL pose this close to the dock pose = initial pose taken
LOCALIZE_TIMEOUT = 10.0  # s to wait for a fresh AMCL pose after an auto undock
NAV2_TIMEOUT = 90.0      # s to wait for Nav2 to become active after an auto undock


def locked(fn):
    @wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._mission_lock:
            return fn(self, *args, **kwargs)
    return wrapper


class MissionManager(Node):
    def __init__(self, robot="robot3", dock_pose=None):
        self.robot = robot.strip("/")
        super().__init__("hospital_mission_manager", namespace="/" + self.robot)
        self._mission_lock = threading.RLock()
        self.nav = NavigationManager("/" + self.robot)
        self.dock = DockManager("/" + self.robot)
        self.dock_pose = dock_pose or DOCKS.get(self.robot)
        self.status_pub = self.create_publisher(String, f"/{self.robot}/hospital_nav/status", 10)
        self.pose = None
        self.state, self.mission_id, self.dest, self.priority = "IDLE", None, None, 1
        self.reason, self.total = "", None  # total = first path length Nav2 reported
        self._init_tries, self._next_init, self._pose_ok = 0, 0.0, False
        self._was_ready = False
        self._pose_seq, self._undock_seq, self._localize_start = 0, 0, 0.0
        self.create_subscription(PoseWithCovarianceStamped, f"/{self.robot}/amcl_pose",
                                 self._pose, AMCL_QOS)
        self.battery = None  # latest Create3 battery_state, sent inside every STATE
        self.create_subscription(BatteryState, f"/{self.robot}/battery_state",
                                 self._on_battery, qos_profile_sensor_data)
        self.create_timer(0.2, self._tick)
        self.create_timer(1.0, self._report)

    @locked
    def _pose(self, msg):
        p = msg.pose.pose.position
        self.pose = float(p.x), float(p.y)
        self._pose_seq += 1
        if (self._init_tries and not self._pose_ok and self.dock_pose
                and math.dist(self.pose, (self.dock_pose.x, self.dock_pose.y)) < POSE_MATCH_M):
            self._pose_ok = True
            self.get_logger().info("[INITIAL_POSE] AMCL 이 도크 위치를 받음")

    @locked
    def _on_battery(self, msg):
        pct = msg.percentage  # Create3: 0.0 ~ 1.0 (NaN if unknown)
        self.battery = {
            "percent": round(pct * 100.0, 1) if 0.0 <= pct <= 1.0 else None,
            "voltage": round(msg.voltage, 2) if math.isfinite(msg.voltage) else None,
            "status": CHARGE_STATUS.get(msg.power_supply_status, "UNKNOWN"),
        }

    # ------------------------------------------------------------- commands
    @locked
    def start(self, destination, *, mission_id=None, priority=1):
        """MOVE to A~F, or DOCK (drive in front of the dock, then dock)."""
        if self.state not in DONE or self._busy():
            self.get_logger().warning(f"진행 중인 임무가 있어 거절 (state={self.state})")
            return False
        if priority not in (1, 2, 3):
            self.get_logger().warning("응급도는 1~3")
            return False
        # On the dock Nav2 may not be active yet (power saver: no lidar -> no map TF),
        # so only require Nav2 up front when the robot is already off the dock.
        if not self.dock.is_docked and not self.nav.ready():
            self.get_logger().warning("Nav2 준비 안 됨 (navigate_to_pose 서버 없음)")
            return False
        dest = str(destination).strip().upper()
        try:
            self._goal(dest)
        except ValueError as exc:
            self.get_logger().error(str(exc))
            return False
        if dest == "DOCK" and self.dock.is_docked:
            self.get_logger().warning("이미 도킹 상태")
            return False
        self.mission_id = str(mission_id or uuid.uuid4().hex[:12])
        self.dest, self.priority, self.reason, self.total = dest, priority, "", None
        self.emit("MISSION_ACCEPTED", destination=dest, priority=priority)
        if self.dock.is_docked:  # auto undock, then LOCALIZING, then drive
            if not self.dock.undock():
                self.reason = "언독 실패"
                self._finish("FAILED")
                return False
            self.state, self._undock_seq = "UNDOCKING", self._pose_seq
            self.emit("UNDOCKING")
            return True
        return self._go()

    @locked
    def undock(self):
        """UNDOCK command: leave the dock and wait there (no navigation)."""
        if self.state not in DONE or self._busy() or not self.dock.is_docked:
            self.get_logger().warning("UNDOCK 거절 (임무 진행 중이거나 도크 위가 아님)")
            return False
        self.mission_id = uuid.uuid4().hex[:12]
        self.dest, self.reason, self.total = "UNDOCK", "", None
        if not self.dock.undock():
            self.reason = "언독 실패"
            self._finish("FAILED")
            return False
        self.state, self._undock_seq = "UNDOCKING", self._pose_seq
        self.emit("UNDOCKING")
        return True

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
        if self._busy():
            self.state = "STOPPING_CANCELED"
            self.nav.cancel()
            self.dock.cancel()
        else:
            self._finish("CANCELED")
        return True

    def shutdown(self, timeout=10.0):
        """Cancel any mission and wait for Nav2 to confirm (executor must spin)."""
        self.cancel_mission()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._mission_lock:
                if not self.state.startswith("STOPPING_") and not self._busy():
                    return True
            time.sleep(0.1)
        return False

    # ---------------------------------------------------------- state machine
    def _busy(self):
        return self.nav.is_busy() or self.dock.is_busy()

    def _ready(self):
        """Off the dock, Nav2 active and AMCL has a pose."""
        return self.dock.is_docked is False and self.pose is not None and self.nav.ready()

    def _goal(self, dest):
        """A~F room goal, or the point in front of the dock for DOCK."""
        if dest != "DOCK":
            return get_goal(dest)
        if self.dock_pose is None:
            raise ValueError(f"{self.robot} 도크 좌표 없음 (goal_manager.DOCKS 또는 --dock)")
        return predock(self.dock_pose)

    def _go(self):
        if not self.nav.send_pose(self._goal(self.dest), label=self.dest):
            self.reason = "Nav2 Goal 전송 실패"
            self._finish("FAILED")
            return False
        self.state = "MOVING"
        self.emit("NAVIGATING", destination=self.dest)
        return True

    def _auto_initial_pose(self):
        """On the dock -> send the dock pose every 2 s until AMCL reports it.

        With power saver the lidar is off on the dock, so AMCL keeps the pose
        but publishes nothing; we then keep sending until UNDOCK (harmless:
        it is the true pose while docked)."""
        if (self._pose_ok or self.dock_pose is None or self._init_tries >= INIT_POSE_TRIES
                or self.dock.is_docked is None or time.monotonic() < self._next_init):
            return
        if not self.dock.is_docked or self.state not in DONE:
            if self._init_tries == 0 and self.pose is None:
                self.get_logger().warning("도크 위가 아니라 자동 초기위치 생략: RViz 2D Pose Estimate 필요")
            self._init_tries = INIT_POSE_TRIES
            return
        self._next_init = time.monotonic() + 2.0
        if not self.dock.amcl_listening():
            return  # localization not up yet; does not use up a try
        self.dock.set_initial_pose(self.dock_pose)
        self._init_tries += 1

    def _reset_initial_pose(self):
        self._init_tries, self._next_init, self._pose_ok = 0, 0.0, False

    @locked
    def _tick(self):
        self._auto_initial_pose()
        ready = self._ready()
        if ready and not self._was_ready:
            self.emit("READY", pose=self._pose_dict())  # console: safe to send A~F now
        self._was_ready = ready
        if self.state.startswith("STOPPING_"):
            if not self._busy():
                outcome = self.state.removeprefix("STOPPING_")
                if outcome == "PAUSE":
                    self.state = "PAUSED"
                    self.emit("PAUSED", reason=self.reason, **self._progress())
                else:
                    self._finish(outcome)
            return
        if self.state in ("UNDOCKING", "DOCKING"):
            result = self.dock.state()
            if result == "RUNNING":
                return
            if result != "SUCCEEDED":
                self.reason = ("언독 " if self.state == "UNDOCKING" else "도킹 ") + result
                self._finish("FAILED")
            elif self.state == "UNDOCKING" and self.dest != "UNDOCK":
                self.emit("UNDOCKED")  # auto undock for a MOVE: wait, then drive
                self.state, self._localize_start = "LOCALIZING", time.monotonic()
                self.emit("LOCALIZING")
            else:
                event = "UNDOCKED" if self.state == "UNDOCKING" else "DOCKED"
                self.state = "COMPLETE"
                self.emit(event)
                if event == "DOCKED":
                    self._reset_initial_pose()  # re-anchor AMCL on the dock
            return
        if self.state == "LOCALIZING":
            waited = time.monotonic() - self._localize_start
            if not self.nav.ready():
                if waited > NAV2_TIMEOUT:
                    self.reason = "언독 후 Nav2 활성화 안 됨"
                    self._finish("FAILED")
                return
            if self._pose_seq > self._undock_seq:
                self._go()  # Nav2 up and AMCL produced a pose since undocking
            elif waited > LOCALIZE_TIMEOUT:
                self.get_logger().warning("언독 후 AMCL 위치 갱신 없음: 그대로 출발")
                self._go()
            return
        if self.state != "MOVING":
            return
        nav_state, _ = self.nav.status()
        if nav_state == "SUCCEEDED" and self.dest == "DOCK":
            if self.dock.dock():
                self.state = "DOCKING"
                self.emit("DOCKING")
            else:
                self.reason = "도킹 액션 실패"
                self._finish("FAILED")
        elif nav_state == "SUCCEEDED":
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
                "docked": self.dock.is_docked, "ready": self._ready(), "pose": self._pose_dict(),
                "battery": self.battery,
                **(self._progress() if self.state == "MOVING"
                   else {"remaining_m": None, "percent": None})}
