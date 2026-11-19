"""PART 06 stand-in for one mock robot.

report mode (4J): judges the PART 04 photo on /<ns>/hospital_nav/obstacle_image like obstacle_classifier
(step 1, no pose) and republishes that photo as the marked image. inspect mode: one shot per Empty request.
"""
from collections import deque
import json

from geometry_msgs.msg import PoseWithCovarianceStamped
import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import Empty, String

from .core.config import load_config
from .core.schemas import dumps
from .fleet_manager_node import default_config
from .robot_link import AMCL_QOS


class MockObstacleClassifier(Node):
    def __init__(self):
        super().__init__("mock_obstacle_classifier")
        cfg = load_config(self.declare_parameter("config_file", default_config()).value)
        ns = self.get_namespace().strip("/")
        self.robot = next(key for key, unit in cfg.robots.items() if unit.ns == ns)
        self.kind = "none"
        self.pose = None
        self.pending = deque()
        # PART 06 step 1 keeps its last result and marked photo (TRANSIENT_LOCAL, depth 1).
        latched = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.output = self.create_publisher(String, cfg.obstacle.result_topic.format(ns=ns), latched)
        self.marked = self.create_publisher(CompressedImage, cfg.obstacle.marked_image_topic.format(ns=ns), latched)
        self.create_subscription(Empty, cfg.obstacle.inspect_topic.format(ns=ns), self.inspect, 10)
        self.create_subscription(CompressedImage, cfg.obstacle.image_topic.format(ns=ns), self.photo,
                                 qos_profile_sensor_data)
        self.create_subscription(String, "/mock/control", self.control, 10)
        self.create_subscription(PoseWithCovarianceStamped, cfg.robot_link.amcl_pose_topic.format(ns=ns),
                                 self.location, AMCL_QOS)
        self.create_timer(.05, self.publish_due)

    def control(self, msg):
        try:
            data = json.loads(msg.data)
        except (ValueError, RecursionError):
            return
        if (isinstance(data, dict) and data.get("robot") == self.robot and data.get("action") == "obstacle"
                and data.get("kind") in ("medical_box", "person", "object", "none", "error", "silent")):
            self.kind = data["kind"]

    def location(self, msg):
        self.pose = msg.pose.pose

    def photo(self, msg):
        """Judge one PART 04 photo after 1 s. medical_box 0.93 or nothing, as /mock/control set it."""
        if self.kind == "silent":
            return
        now = self.get_clock().now().nanoseconds
        header = {"stamp": {"sec": msg.header.stamp.sec, "nanosec": msg.header.stamp.nanosec},
                  "frame_id": msg.header.frame_id}
        result = {"status": "ok", "header": header, "obstacle_present": False, "detections": []}
        if self.kind == "error":
            result.update(status="processing_error", obstacle_present=None, error="mock")
        elif self.kind != "none":
            label = {"person": "person", "object": "chair"}.get(self.kind, "medical_box")
            result.update(obstacle_present=True,
                          detections=[{"label": label, "confidence": .93, "bbox": [236, 168, 420, 316]}])
        marked = CompressedImage(header=msg.header, format=msg.format, data=bytes(msg.data))
        self.pending.append((now + 1_000_000_000, result, marked))

    def inspect(self, msg):
        if self.kind == "silent":
            return
        now = self.get_clock().now().nanoseconds
        result = {"status": "ok", "obstacle_present": False, "detections": [],
                  "header": {"stamp": now / 1e9, "frame_id": "oakd_rgb_camera_optical_frame"}}
        if self.kind == "error":
            result.update(status="processing_error", obstacle_present=None)
        elif self.kind in ("person", "object", "medical_box"):
            detection = {"label": {"person": "person", "object": "chair"}.get(self.kind, "medical_box"),
                         "confidence": .82, "bbox": [210, 80, 400, 470]}
            if self.pose:
                p, q = self.pose.position, self.pose.orientation
                detection["pose"] = {"x": p.x + 1 - 2*(q.y*q.y + q.z*q.z),
                                     "y": p.y + 2*(q.x*q.y + q.w*q.z), "z": 0.0}
            result.update(obstacle_present=True, detections=[detection])
        self.pending.append((now + 1_000_000_000, result, None))

    def publish_due(self):
        now = self.get_clock().now().nanoseconds
        while self.pending and self.pending[0][0] <= now:
            _, result, marked = self.pending.popleft()
            if marked is not None:
                self.marked.publish(marked)  # step 1 publishes the drawn photo before the JSON
            self.output.publish(String(data=dumps(result)))


def main(args=None):
    rclpy.init(args=args)
    node = MockObstacleClassifier()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
