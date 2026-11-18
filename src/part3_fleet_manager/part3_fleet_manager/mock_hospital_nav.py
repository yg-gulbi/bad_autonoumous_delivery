"""One simulated PART 04 robot, using hospital_nav text commands and status JSON."""
import json
from itertools import pairwise
from math import dist, isfinite
from uuid import uuid4

from ament_index_python.packages import get_package_share_path
from geometry_msgs.msg import PoseWithCovarianceStamped
import rclpy
from rcl_interfaces.msg import ParameterDescriptor, SetParametersResult
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import BatteryState
from std_msgs.msg import String

from .core.config import load_config
from .core.route_planner import waypoints
from .core.robot_protocol import DONE
from .core.schemas import dumps
from .robot_link import AMCL_QOS


def parse_command(text, supports_id):
    words = text.strip().split()
    tag = None
    if supports_id and words and words[-1].lower().startswith("id="):
        tag = words.pop()[3:] or None
    words = [w.upper() for w in words]
    if len(words) == 1 and words[0] in ("PAUSE", "RESUME", "CANCEL"):
        return words[0], None, None, tag
    if words[:1] == ["MOVE"]:
        words = words[1:]
    if len(words) not in (1, 2) or words[0] not in set("ABCDEF"):
        raise ValueError("구역 A~F와 선택 응급도 1~3이 필요합니다.")
    if len(words) == 2 and words[1] not in ("1", "2", "3"):
        raise ValueError("응급도는 1~3 정수입니다.")
    return "MOVE", words[0], int(words[1]) if len(words) == 2 else 1, tag


def stall_point(start, end, target, radius):
    """Closest point on this tick's travelled segment to the configured obstruction."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    norm = dx * dx + dy * dy
    t = ((target[0] - start[0]) * dx + (target[1] - start[1]) * dy) / norm if norm else 0
    t = min(1.0, max(0.0, t))
    point = start[0] + dx * t, start[1] + dy * t
    return point if dist(point, target) <= radius else None


class MockHospitalNav(Node):
    def __init__(self):
        super().__init__("mock_hospital_nav")
        static = ParameterDescriptor(read_only=True)
        default = str(get_package_share_path("part3_fleet_manager") / "config" / "fleet.yaml")
        self.config = load_config(self.declare_parameter("config_file", default, static).value)
        self.robot = self.get_namespace().strip("/")
        unit = next((u for u in self.config.robots.values() if u.ns == self.robot), None)
        if unit is None:
            raise ValueError(f"fleet.yaml에 없는 mock namespace: /{self.robot}")
        self.logical_id = next(k for k, u in self.config.robots.items() if u.ns == self.robot)
        self.drop_until = 0.0
        defaults = self.config.mock
        self.declare_parameter("supports_request_id", defaults.supports_request_id)
        params = [self.declare_parameter("speed_mps", defaults.speed_mps),
                  self.declare_parameter("battery_pct", 80.0 if self.robot == "robot2" else 60.0),
                  self.declare_parameter("stall_at", [0.0, 0.0, 0.0]),
                  self.declare_parameter("stall_radius_m", defaults.stall_radius_m)]
        check = self.validate(params)
        if not check.successful:
            raise ValueError(check.reason)
        for name in ("reject_next", "fail_next", "drop_link"):
            self.declare_parameter(name, False)
        self.add_on_set_parameters_callback(self.validate)
        self.pose = self.config.zones[unit.start_zone]
        self.state, self.mid, self.dest, self.level, self.reason = "IDLE", None, None, 1, ""
        self.path, self.total, self.stalled_until, self.fail = [], 0.0, 0.0, False
        self.last_tick = self.now()
        link = self.config.robot_link
        self.status = self.create_publisher(String, link.status_topic.format(ns=self.robot), 10)
        self.battery_pub = self.create_publisher(BatteryState, link.battery_topic.format(ns=self.robot),
                                                 qos_profile_sensor_data)
        self.pose_pub = self.create_publisher(PoseWithCovarianceStamped, link.amcl_pose_topic.format(ns=self.robot),
                                              AMCL_QOS)
        self.create_subscription(String, link.command_topic.format(ns=self.robot), self.command, 10)
        self.create_subscription(String, "/mock/control", self.control, 10)
        self.create_timer(1.0 / self.config.publish.tick_hz, self.tick)
        self.create_timer(1.0, self.report)
        self.publish_pose()

    def control(self, msg):
        try:
            data = json.loads(msg.data)
            if not isinstance(data, dict) or data.get("robot") != self.logical_id:
                return
            action = data.get("action")
            if action in ("fail_next", "reject_next"):
                self.set_parameters([Parameter(action, value=True)])
                return
            key = "value" if action == "battery" else "seconds"
            value = data.get(key)
            if type(value) not in (int, float) or not isfinite(value) or value < 0:
                raise ValueError("유효한 수치가 필요합니다")
            if action == "battery" and value <= 100:
                self.set_parameters([Parameter("battery_pct", value=float(value))])
            elif action == "stall":
                self.stalled_until = self.now() + value
            elif action == "drop_link":
                self.drop_until = self.now() + value
            else:
                raise ValueError("시험 동작 또는 값 오류")
        except (ValueError, OverflowError) as exc:
            self.get_logger().warning(str(exc))

    @staticmethod
    def validate(params):
        for p in params:
            if p.name == "battery_pct" and not 0 <= p.value <= 100:
                return SetParametersResult(successful=False, reason="battery_pct requires 0..100")
            if p.name in ("speed_mps", "stall_radius_m") and (not isfinite(p.value) or p.value <= 0):
                return SetParametersResult(successful=False, reason=f"{p.name} must be finite and positive")
            if p.name == "stall_at" and (len(p.value) != 3 or not all(map(isfinite, p.value)) or p.value[2] < 0):
                return SetParametersResult(successful=False, reason="stall_at requires [x, y, duration_s >= 0]")
        return SetParametersResult(successful=True)

    def now(self):
        return self.get_clock().now().nanoseconds / 1e9

    def value(self, name):
        return self.get_parameter(name).value

    def consume(self, name):
        enabled = self.value(name)
        if enabled:
            self.set_parameters([Parameter(name, value=False)])
        return enabled

    def publish(self, kind, **fields):
        self.status.publish(String(data=dumps({"type": kind, "robot_id": self.robot,
                                               "mission_id": self.mid, **fields})))

    def emit(self, event, **fields):
        self.publish("STATUS", event=event, **fields)

    def position(self):
        return {"x": round(self.pose[0], 3), "y": round(self.pose[1], 3)}

    def publish_pose(self):
        msg = PoseWithCovarianceStamped()
        msg.header.frame_id = "map"
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.pose.pose.position.x, msg.pose.pose.position.y = self.pose
        msg.pose.pose.orientation.w = 1.0
        self.pose_pub.publish(msg)

    def progress(self):
        remaining = sum(dist(a, b) for a, b in pairwise([self.pose, *self.path]))
        percent = 100 * (1 - remaining / self.total) if self.total else 0.0
        return {"remaining_m": round(remaining, 2), "percent": round(max(0.0, percent), 1)}

    def report(self):
        battery = BatteryState()
        battery.header.stamp = self.get_clock().now().to_msg()
        battery.present = True
        battery.percentage = self.value("battery_pct") / 100.0
        self.battery_pub.publish(battery)
        progress = {"remaining_m": None, "percent": None}
        if self.state == "MOVING":
            progress = self.progress()
            self.emit("PROGRESS", destination=self.dest, **progress)
        if not self.value("drop_link") and self.now() >= self.drop_until:
            self.publish("STATE", state=self.state, destination=self.dest, priority=self.level,
                         reason=self.reason, pose=self.position(), **progress)

    def reject(self, text, tag, reason):
        self.get_logger().warning(f"{text!r}: {reason}")
        if self.value("supports_request_id"):
            self.emit("COMMAND_REJECTED", command=text, request_id=tag, state=self.state, reason=reason)

    def command(self, msg):
        text = msg.data.strip()
        try:
            action, dest, level, tag = parse_command(text, self.value("supports_request_id"))
        except ValueError as exc:
            self.reject(text, None, str(exc))
            return
        if action == "MOVE" and self.consume("reject_next"):
            self.get_logger().warning(f"reject_next: {text!r} 무시")
            return
        if action == "MOVE" and self.state in DONE:
            self.dest, self.level, self.mid, self.reason = dest, level, tag or uuid4().hex[:12], ""
            self.path = waypoints(self.config, self.pose, dest)
            self.total = sum(dist(a, b) for a, b in pairwise([self.pose, *self.path]))
            self.fail, self.stalled_until = self.consume("fail_next"), 0.0
            self.emit("MISSION_ACCEPTED", destination=dest, priority=level)
        elif action == "RESUME" and self.state == "PAUSED":
            self.emit("RESUMED")
        elif action == "PAUSE" and self.state == "MOVING":
            self.reason, self.state = "관제 요청", "STOPPING_PAUSE"
            return
        elif action == "CANCEL" and self.state in DONE:
            self.get_logger().info(f"{self.state}: 끝난 구간의 CANCEL 무시")
            return
        elif action == "CANCEL":
            self.reason, self.state = "운영자 취소", "STOPPING_CANCELED"
            return
        else:
            self.reject(text, tag, f"{action} 불가 상태")
            return
        self.last_tick, self.state = self.now(), "MOVING"
        self.emit("NAVIGATING", destination=self.dest)

    def tick(self):
        now = self.now()
        elapsed, self.last_tick = max(0.0, now - self.last_tick), now
        if self.state in ("STOPPING_PAUSE", "STOPPING_CANCELED"):
            self.state = "PAUSED" if self.state == "STOPPING_PAUSE" else "CANCELED"
            self.emit("PAUSED" if self.state == "PAUSED" else "MISSION_CANCELED", reason=self.reason, **self.progress())
            return
        if self.state != "MOVING" or now < self.stalled_until:
            return
        if self.fail:
            self.state, self.reason, self.fail = "FAILED", "Nav2: ABORTED", False
            self.emit("MISSION_FAILED", reason=self.reason, **self.progress())
            return
        budget = self.value("speed_mps") * elapsed
        initial = self.pose
        target_x, target_y, duration = self.value("stall_at")
        while self.path and budget > 0:
            goal = self.path[0]
            length = dist(self.pose, goal)
            step = min(length, budget)
            end = goal if step == length else tuple(a + (b - a) * step / length for a, b in zip(self.pose, goal))
            if duration:
                stop = stall_point(self.pose, end, (target_x, target_y), self.value("stall_radius_m"))
                if stop is not None:
                    self.pose, self.stalled_until = stop, now + duration
                    self.set_parameters([Parameter("stall_at", value=[0.0, 0.0, 0.0])])
                    break
            self.pose = end
            budget -= step
            if step == length:
                self.path.pop(0)
        if self.pose != initial:
            self.publish_pose()
        if not self.path:
            self.state = "COMPLETE"
            self.emit("ARRIVED", destination=self.dest, pose=self.position(), percent=100.0, goal_error_m=0.0)


def main(args=None):
    rclpy.init(args=args)
    node = MockHospitalNav()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
