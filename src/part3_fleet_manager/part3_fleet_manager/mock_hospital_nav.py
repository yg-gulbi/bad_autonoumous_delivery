"""One simulated PART 04 robot, using hospital_nav text commands and status JSON."""
import json
from itertools import pairwise
from dataclasses import replace
from math import atan2, cos, degrees, dist, isfinite, sin
from uuid import uuid4

from ament_index_python.packages import get_package_share_path
from geometry_msgs.msg import PoseWithCovarianceStamped
import rclpy
from rcl_interfaces.msg import ParameterDescriptor, SetParametersResult
from rclpy.action import ActionServer, GoalResponse
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import BatteryState, CompressedImage
from std_msgs.msg import String

from .core.config import load_config
from .core.route_planner import waypoints
from .core.robot_protocol import DONE
from .core.schemas import dumps
from .robot_link import AMCL_QOS, DockStatus, HazardDetectionVector

try:
    from nav2_msgs.action import NavigateToPose
except ImportError:
    NavigateToPose = None


def parse_command(text, supports_id, supports_slots=True):
    """-> (action, zone, level, tag, slot). 4J PART 04 takes "MOVE F2 3" (slot 2); no number = slot 1."""
    words = text.strip().split()
    tag = None
    if supports_id and words and words[-1].lower().startswith("id="):
        tag = words.pop()[3:] or None
    words = [w.upper() for w in words]
    if len(words) == 1 and words[0] in ("PAUSE", "RESUME", "CANCEL", "DOCK", "UNDOCK"):
        return words[0], None, None, tag, None
    if words[:1] == ["MOVE"]:
        words = words[1:]
    room = words[0] if words else ""
    slotted = supports_slots and len(room) == 2 and room[1] in "12"
    if len(words) not in (1, 2) or room[:1] not in set("ABCDEF") or len(room) != 1 + slotted:
        raise ValueError("구역 A~F(4J: A1/A2 …)와 선택 응급도 1~3이 필요합니다.")
    if len(words) == 2 and words[1] not in ("1", "2", "3"):
        raise ValueError("응급도는 1~3 정수입니다.")
    return "MOVE", room[0], int(words[1]) if len(words) == 2 else 1, tag, int(room[1]) if slotted else 1


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
        self.declare_parameter("report_rejections", defaults.report_rejections)
        # false = the PART 04 before 4J: no slot in STATE, "MOVE F2" is a syntax error, no crossroad leg.
        self.declare_parameter("supports_slots", True)
        self.declare_parameter("abort_after_s", defaults.abort_after_s)
        params = [self.declare_parameter("localizing_s", defaults.localizing_s),
                  self.declare_parameter("speed_mps", defaults.speed_mps),
                  self.declare_parameter("battery_pct", 80.0 if self.robot == "robot2" else 60.0),
                  self.declare_parameter("stall_at", [0.0, 0.0, 0.0]),
                  self.declare_parameter("stall_radius_m", defaults.stall_radius_m)]
        check = self.validate(params)
        if not check.successful:
            raise ValueError(check.reason)
        for name in ("reject_next", "fail_next", "drop_link"):
            self.declare_parameter(name, False)
        # 4K bench check: true = every PAUSE is lost (the robot keeps MOVING), to see the unconfirmed-stop card.
        self.declare_parameter("ignore_pause", False)
        self.add_on_set_parameters_callback(self.validate)
        self.yaw = 0.0
        self.predock, self.dock_point = unit.dock.path_end() if unit.dock else (None, None)
        # ZONE-mode scenarios start at the existing virtual home docks.
        self.pose = self.config.zones[unit.start_zone]
        if unit.return_to == "DOCK" and self.dock_point is not None:
            self.pose = self.dock_point
        self.docked = True
        self.phase_until = 0.0
        self.state, self.mid, self.dest, self.level, self.reason = "IDLE", None, None, 1, ""
        self.path, self.total, self.stalled_until, self.fail = [], 0.0, 0.0, False
        self.slot, self.via = None, None  # via: the crossroad point still ahead on this path
        self.block_armed, self.blocked_since = False, None  # /mock/control obstacle (report mode)
        sample = get_package_share_path("part3_fleet_manager") / "mock" / "obstacle_sample.jpg"
        self.sample = sample.read_bytes() if sample.is_file() else b""
        self.last_tick = self.now()
        link = self.config.robot_link
        self.status = self.create_publisher(String, link.status_topic.format(ns=self.robot), 10)
        self.battery_pub = self.create_publisher(BatteryState, link.battery_topic.format(ns=self.robot),
                                                 qos_profile_sensor_data)
        if DockStatus is not None:
            self.dock_pub = self.create_publisher(DockStatus, link.dock_status_topic.format(ns=self.robot),
                                                  qos_profile_sensor_data)
            self.hazard_pub = self.create_publisher(HazardDetectionVector, link.hazard_topic.format(ns=self.robot),
                                                    qos_profile_sensor_data)
        # Expose Nav2 discovery for bench checks. Only hospital_nav commands drive this simulator.
        if NavigateToPose is not None:
            self.nav_server = ActionServer(
                self, NavigateToPose, "navigate_to_pose", execute_callback=lambda _: NavigateToPose.Result(),
                goal_callback=lambda _: GoalResponse.REJECT)
        self.pose_pub = self.create_publisher(PoseWithCovarianceStamped, link.amcl_pose_topic.format(ns=self.robot),
                                              AMCL_QOS)
        self.image_pub = self.create_publisher(CompressedImage, self.config.obstacle.image_topic.format(ns=self.robot), 10)
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
            if action == "obstacle":
                # report mode: stop in front of the obstacle; Nav2 gives up after abort_after_s.
                if self.config.obstacle.mode == "report":
                    self.block_armed = True
                    self.get_logger().info("시험 장애물: 다음 복도 주행에서 멈춤" if self.state != "MOVING"
                                           else "시험 장애물: 지금 멈춤")
                return
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
            if p.name == "localizing_s" and (not isfinite(p.value) or p.value < 0):
                return SetParametersResult(successful=False, reason="localizing_s must be finite and nonnegative")
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

    def slots(self):
        return self.value("supports_slots")

    def emit(self, event, **fields):
        self.publish("STATUS", event=event, **fields)

    def position(self):
        return {"x": round(self.pose[0], 3), "y": round(self.pose[1], 3)}

    def publish_pose(self):
        msg = PoseWithCovarianceStamped()
        msg.header.frame_id = "map"
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.pose.pose.position.x, msg.pose.pose.position.y = self.pose
        msg.pose.pose.orientation.z = sin(self.yaw / 2)
        msg.pose.pose.orientation.w = cos(self.yaw / 2)
        self.pose_pub.publish(msg)

    def progress(self):
        remaining = sum(dist(a, b) for a, b in pairwise([self.pose, *self.path]))
        percent = 100 * (1 - remaining / self.total) if self.total else 0.0
        return {"remaining_m": round(remaining, 2), "percent": round(max(0.0, percent), 1)}

    def report(self):
        battery = BatteryState()
        battery.header.stamp = self.get_clock().now().to_msg()
        battery.present = True
        battery.voltage = 15.0
        battery.power_supply_status = (BatteryState.POWER_SUPPLY_STATUS_CHARGING if self.docked
                                       else BatteryState.POWER_SUPPLY_STATUS_DISCHARGING)
        battery.percentage = self.value("battery_pct") / 100.0
        self.battery_pub.publish(battery)
        if DockStatus is not None:
            self.dock_pub.publish(DockStatus(header=battery.header, is_docked=self.docked))
            self.hazard_pub.publish(HazardDetectionVector(header=battery.header, detections=[]))
        progress = {"remaining_m": None, "percent": None}
        if self.state == "MOVING":
            progress = self.progress()
            self.emit("PROGRESS", destination=self.dest, **progress)
        if not self.value("drop_link") and self.now() >= self.drop_until:
            extra = {"slot": self.slot} if self.slots() else {}
            self.publish("STATE", state=self.state, destination=self.dest, priority=self.level,
                         reason=self.reason, pose=self.position(), docked=self.docked,
                         ready=not self.docked and self.state != "LOCALIZING",
                         battery={"percent": self.value("battery_pct"), "voltage": float(battery.voltage),
                                  "status": "CHARGING" if self.docked else "DISCHARGING"}, **extra, **progress)

    def reject(self, text, tag, reason):
        self.get_logger().warning(f"{text!r}: {reason}")
        if self.value("report_rejections"):
            self.emit("COMMAND_REJECTED", command=text, request_id=tag, state=self.state, reason=reason)

    def command(self, msg):
        text = msg.data.strip()
        try:
            action, dest, level, tag, slot = parse_command(text, self.value("supports_request_id"), self.slots())
        except ValueError as exc:
            self.reject(text, None, str(exc))
            return
        if action in ("MOVE", "DOCK") and self.consume("reject_next"):
            self.reject(text, tag, "시험용 명령 거절")
            return
        if action in ("MOVE", "DOCK", "UNDOCK") and self.state in DONE:
            if action == "DOCK" and self.dock_point is None:
                self.reject(text, tag, "도크 좌표가 없습니다.")
                return
            if (action == "DOCK" and self.docked) or (action == "UNDOCK" and not self.docked):
                self.reject(text, tag, "현재 도킹 상태에서 실행할 수 없습니다.")
                return
            self.dest = dest if action == "MOVE" else action
            self.level, self.mid, self.reason = level or 1, tag or uuid4().hex[:12], ""
            self.slot = slot if action == "MOVE" and self.slots() else None
            self.via, self.blocked_since = None, None
            if action == "DOCK":
                anchor = min(self.config.zones, key=lambda z: dist(self.config.zones[z], self.predock))
                self.path = [*waypoints(self.config, self.pose, anchor)[:-1], self.predock]
            elif action == "MOVE" and self.slots():
                self.path = waypoints(self.config, self.pose, dest, slot or 1)
                if self.config.corridor.crossroad in self.path[:-1]:
                    self.via = self.config.corridor.crossroad  # into or out of B·E: crossroad first (PART 04)
            elif action == "MOVE":
                # The PART 04 before 4J drives through the door mouth to the single zone point.
                rooms = replace(self.config, corridor=replace(self.config.corridor, room_mode="ROOM"))
                self.path = waypoints(rooms, self.pose, dest)
            else:
                self.path = []
            self.total = sum(dist(a, b) for a, b in pairwise([self.pose, *self.path]))
            self.fail, self.stalled_until = self.consume("fail_next"), 0.0
            if action != "UNDOCK":
                extra = {"slot": self.slot} if self.slots() else {}
                self.emit("MISSION_ACCEPTED", destination=self.dest, priority=self.level, **extra)
            if self.docked:
                self.state, self.phase_until = "UNDOCKING", self.now() + 1.0
                self.emit("UNDOCKING")
                return
        elif action == "RESUME" and self.state == "PAUSED":
            self.emit("RESUMED")
        elif action == "PAUSE" and self.state == "MOVING":
            if self.value("ignore_pause"):
                self.get_logger().info("시험: PAUSE 무시 (ignore_pause)")
                return
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
        self.navigate()

    def navigate(self):
        self.last_tick, self.state = self.now(), "MOVING"
        if self.blocked_since is not None:
            self.blocked_since = self.now()  # RESUME in front of the obstacle: Nav2 tries again
        extra = {"via": "교차로" if self.via else None} if self.slots() else {}
        self.emit("NAVIGATING", destination=self.dest, **extra)

    def give_up(self):
        """Nav2 ABORTED after recoveries: MISSION_FAILED, then the photo and OBSTACLE (PART 04 order)."""
        self.state, self.reason, self.blocked_since = "FAILED", "Nav2: ABORTED", None
        self.emit("MISSION_FAILED", reason=self.reason, **self.progress())
        topic = self.config.obstacle.image_topic.format(ns=self.robot)
        if self.sample:
            image = CompressedImage(format="jpeg", data=self.sample)
            image.header.stamp = self.get_clock().now().to_msg()
            image.header.frame_id = "oakd_rgb_camera_optical_frame"
            self.image_pub.publish(image)
        ahead = self.path[0] if self.path else self.pose
        bearing = atan2(ahead[1] - self.pose[1], ahead[0] - self.pose[0])
        lidar = 0.42
        self.emit("OBSTACLE", destination=self.dest,
                  robot_pose={**self.position(), "yaw_deg": round(degrees(bearing), 1)},
                  captured=["depth", "rgb", "scan"] if self.sample else ["depth", "scan"],
                  **({"image_topic": topic} if self.sample else {}), depth_m=0.45, lidar_m=lidar, bearing_deg=0.0,
                  map_x=round(self.pose[0] + lidar * cos(bearing), 3), map_y=round(self.pose[1] + lidar * sin(bearing), 3),
                  reason=self.reason)

    def tick(self):
        now = self.now()
        elapsed, self.last_tick = max(0.0, now - self.last_tick), now
        if self.state in ("STOPPING_PAUSE", "STOPPING_CANCELED"):
            self.state = "PAUSED" if self.state == "STOPPING_PAUSE" else "CANCELED"
            self.emit("PAUSED" if self.state == "PAUSED" else "MISSION_CANCELED", reason=self.reason, **self.progress())
            return
        if self.state in ("UNDOCKING", "LOCALIZING", "DOCKING"):
            if now < self.phase_until:
                return
            if self.state == "DOCKING":
                self.state, self.docked, self.pose = "COMPLETE", True, self.dock_point
                self.publish_pose()
                self.emit("DOCKED")
            elif self.state == "UNDOCKING":
                self.docked = False
                self.emit("UNDOCKED")
                if self.dest == "UNDOCK":
                    self.state = "COMPLETE"
                else:
                    self.state, self.phase_until = "LOCALIZING", now + self.value("localizing_s")
                    self.emit("LOCALIZING")
            else:
                self.emit("READY", pose=self.position())
                self.navigate()
            return
        if self.state != "MOVING" or now < self.stalled_until:
            return
        if self.blocked_since is not None:
            if now - self.blocked_since >= self.value("abort_after_s"):
                self.give_up()
            return
        if self.block_armed and self.config.corridor.region_of(self.pose) == "CORRIDOR":
            self.block_armed, self.blocked_since = False, now
            self.get_logger().info("시험 장애물 앞에서 정지 (Nav2 회복 시도 중)")
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
                if self.via is not None and goal == self.via:
                    self.via = None
                    self.emit("VIA_PASSED", destination=self.dest)
                    self.emit("NAVIGATING", destination=self.dest, via=None)
        if self.pose != initial:
            self.yaw = atan2(self.pose[1] - initial[1], self.pose[0] - initial[0])
            self.publish_pose()
        if not self.path:
            if self.dest == "DOCK":
                self.state, self.phase_until = "DOCKING", now + 1.0
                self.emit("DOCKING")
                return
            self.state = "COMPLETE"
            extra = {"slot": self.slot} if self.slots() else {}
            self.emit("ARRIVED", destination=self.dest, pose=self.position(), percent=100.0, goal_error_m=0.0, **extra)


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
