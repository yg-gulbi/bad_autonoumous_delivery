"""Receive input on the ROS graph and publish the results of the fleet tick."""
from dataclasses import replace
from functools import partial
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from std_msgs.msg import Bool, String
from std_srvs.srv import Trigger

from .core.config import load_config
from .core.fleet_core import FleetCore
from .core.schemas import dumps
from .robot_link import RobotLink


def default_config():
    return str(Path(get_package_share_directory("part3_fleet_manager")) / "config" / "fleet.yaml")


class FleetManagerNode(Node):
    def __init__(self):
        super().__init__("fleet_manager")
        config = load_config(self.declare_parameter("config_file", default_config()).value)
        mode = self.declare_parameter("assignment_mode", config.assignment.mode).value
        if mode not in ("IDLE_BEST_BATTERY", "ROLE"):
            raise ValueError("assignment_mode must be IDLE_BEST_BATTERY or ROLE")
        monitor = config.safety.bed_monitor
        required = self.declare_parameter("bed_monitor_require_at_start", monitor.require_at_start).value
        config = replace(config, assignment=replace(config.assignment, mode=mode),
                         safety=replace(config.safety, bed_monitor=replace(monitor, require_at_start=required)))
        db_path = self.declare_parameter("db_path", "").value
        self.core = FleetCore(replace(config, db_path=db_path) if db_path else config)
        self.links = {r: RobotLink(self, r, unit.ns, config.robot_link, self.core.enqueue, self.now, config.obstacle)
                      for r, unit in config.robots.items()}
        self.outputs = {name: self.create_publisher(String, name, 100) for name in
                        ("/fleet/state", "/fleet/decision_req", "/mission/event", "/ui/ack")}
        for topic in ("/ui/mission_req", "/ui/operator_cmd", "/ui/decision"):
            self.create_subscription(String, topic, partial(self.receive, topic), 10)
        self.create_subscription(Bool, "/patient/bed_exit", partial(self.receive, "/patient/bed_exit"), 10)
        for service in ("/robot/pause", "/robot/resume"):
            self.create_service(Trigger, service, partial(self.trigger, service))
        self.create_timer(1.0 / config.publish.tick_hz, self.tick)

    def now(self):
        return self.get_clock().now().nanoseconds / 1e9

    def receive(self, topic, msg):
        self.core.enqueue(topic, msg.data, self.now())

    def trigger(self, service, request, response):
        response.success, response.message = self.core.service(service, self.now())
        return response

    def publish_json(self, topic, payload):
        self.outputs[topic].publish(String(data=dumps(payload)))

    def tick(self):
        out = self.core.tick(self.now())
        for motion in out.commands:
            self.links[motion.robot].send(motion.command)
        for robot in out.inspections:
            self.links[robot].request_inspection()
        for event in out.events:
            self.publish_json("/mission/event", event)
            if event["type"] == "WARNING":
                self.get_logger().warning(event["detail"]["reason"])
        for card in out.cards:
            self.publish_json("/fleet/decision_req", card)
        for ack in out.acks:
            self.publish_json("/ui/ack", ack)
        if out.state is not None:
            self.publish_json("/fleet/state", out.state)
        if out.storage_error:
            self.get_logger().error(out.storage_error, throttle_duration_sec=1.0)

    def destroy_node(self):
        self.core.store.close()
        return super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = FleetManagerNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
