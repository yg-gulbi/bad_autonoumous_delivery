"""One-shot PART 06 camera stand-in for a single mock robot."""
from collections import deque
import json

from geometry_msgs.msg import PoseWithCovarianceStamped
import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
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
        self.output = self.create_publisher(String, cfg.obstacle.result_topic.format(ns=ns), 10)
        self.create_subscription(Empty, cfg.obstacle.inspect_topic.format(ns=ns), self.inspect, 10)
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
                and data.get("kind") in ("person", "object", "none", "error", "silent")):
            self.kind = data["kind"]

    def location(self, msg):
        self.pose = msg.pose.pose

    def inspect(self, msg):
        if self.kind == "silent":
            return
        now = self.get_clock().now().nanoseconds
        result = {"status": "ok", "obstacle_present": False, "detections": [],
                  "header": {"stamp": now / 1e9, "frame_id": "oakd_rgb_camera_optical_frame"}}
        if self.kind == "error":
            result.update(status="processing_error", obstacle_present=None)
        elif self.kind in ("person", "object"):
            detection = {"label": "person" if self.kind == "person" else "chair",
                         "confidence": .82, "bbox": [210, 80, 400, 470]}
            if self.pose:
                p, q = self.pose.position, self.pose.orientation
                detection["pose"] = {"x": p.x + 1 - 2*(q.y*q.y + q.z*q.z),
                                     "y": p.y + 2*(q.x*q.y + q.w*q.z), "z": 0.0}
            result.update(obstacle_present=True, detections=[detection])
        self.pending.append((now + 1_000_000_000, result))

    def publish_due(self):
        now = self.get_clock().now().nanoseconds
        while self.pending and self.pending[0][0] <= now:
            _, result = self.pending.popleft()
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
