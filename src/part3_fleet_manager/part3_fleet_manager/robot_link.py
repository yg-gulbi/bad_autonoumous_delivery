"""ROS transport for one hospital_nav namespace. FleetCore owns the protocol session."""

from geometry_msgs.msg import PoseWithCovarianceStamped
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import BatteryState
from std_msgs.msg import Empty, String

try:
    from irobot_create_msgs.msg import DockStatus, HazardDetection, HazardDetectionVector
except ImportError:
    DockStatus = HazardDetection = HazardDetectionVector = None


AMCL_QOS = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                      durability=DurabilityPolicy.TRANSIENT_LOCAL)


class RobotLink:
    def __init__(self, node, robot, ns, config, enqueue, now, obstacle, *, use_hazard_topic=False):
        def topic(template):
            return template.format(ns=ns)

        def listen(msg_type, template, qos, key, extract):
            def receive(msg):
                enqueue(key, extract(msg), now(), robot=robot)
            node.create_subscription(msg_type, topic(template), receive, qos)

        if obstacle.enabled:
            # report mode never asks for a photo; PART 04 sends one when Nav2 gives up (BRIEF 5.5).
            if obstacle.mode == "inspect":
                self.inspect = node.create_publisher(Empty, topic(obstacle.inspect_topic), 10)
            listen(String, obstacle.result_topic, 10, "obstacle_result", lambda msg: msg.data)
        self.command = node.create_publisher(String, topic(config.command_topic), 10)
        listen(String, config.status_topic, 10, "status", lambda msg: msg.data)
        listen(PoseWithCovarianceStamped, config.amcl_pose_topic, AMCL_QOS, "pose",
               lambda msg: {"x": msg.pose.pose.position.x, "y": msg.pose.pose.position.y})
        listen(BatteryState, config.battery_topic, qos_profile_sensor_data, "battery",
               lambda msg: round(100.0 * msg.percentage, 5) if 0 <= msg.percentage <= 1 else None)
        if HazardDetectionVector is None:
            topics = "dock_status·hazard_detection" if use_hazard_topic else "dock_status"
            node.get_logger().warning(f"{ns}: irobot_create_msgs 미설치로 {topics} 구독 생략. 도킹 여부는 STATE에서 받습니다.")
        else:
            if use_hazard_topic:
                listen(HazardDetectionVector, config.hazard_topic, qos_profile_sensor_data, "hazards",
                       lambda msg: ["BUMP" for d in msg.detections if d.type == HazardDetection.BUMP])
            listen(DockStatus, config.dock_status_topic, qos_profile_sensor_data, "docked", lambda msg: msg.is_docked)

    def request_inspection(self):
        self.inspect.publish(Empty())

    def send(self, command):
        self.command.publish(String(data=command))
