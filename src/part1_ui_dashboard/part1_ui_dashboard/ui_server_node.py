"""Serve the offline monitor while a separate executor thread handles the ROS graph."""
from functools import partial
from pathlib import Path
from threading import Event, Lock, Thread

from ament_index_python.packages import get_package_share_path
import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from std_msgs.msg import Bool, String
from std_srvs.srv import Trigger
from werkzeug.serving import make_server
import yaml

from part3_fleet_manager.core import schemas
from part3_fleet_manager.core.config import read_config_yaml
from . import ui_api
from .web import Feed, create_app


class UIServer(Node):
    def __init__(self):
        super().__init__("ui_server")
        path = get_package_share_path("part3_fleet_manager") / "config" / "fleet.yaml"
        ui_path = get_package_share_path("part1_ui_dashboard") / "config" / "ui.yaml"
        options = yaml.safe_load(Path(self.declare_parameter("ui_config", str(ui_path)).value).read_text())
        fleet_path = self.declare_parameter("fleet_yaml", options.get("fleet_yaml") or str(path)).value or str(path)
        cfg = read_config_yaml(fleet_path)
        self.db_path = self.declare_parameter("db_path", "").value or cfg["db"]["path"]
        self.dev_tools = self.declare_parameter("dev_tools", options.get("dev_tools", False)).value
        self.map_yaml = self.declare_parameter("map_yaml", options["map_yaml"]).value or options["map_yaml"]
        self.host, self.port = options.get("host", "0.0.0.0"), options.get("port", 8090)
        self.feed = Feed(options.get("event_log_size", 500))
        self.geometry = {"zones": cfg["zones"], "polygon": cfg["corridor"]["polygon"],
                         "start_zones": {robot: data["start_zone"] for robot, data in cfg["robots"].items()},
                         "bed_targets": cfg["safety"]["bed_exit_pause_targets"],
                         "event_limit": options.get("event_log_size", 500)}
        self.demo = self.declare_parameter("demo", False).value
        self.detected_demo = self.demo
        self.mode = self.declare_parameter("mode_label", options.get("mode_label", "auto")).value
        if not self.demo and self.mode == "auto":
            self.create_timer(1.0, self.detect_mock)
        self.testing = self.dev_tools and self.demo and self.mode != "live"
        self.names = {z: data.get("name") or f"{z} 구역" for z, data in cfg["zones"].items()}
        self.robot_names = {robot: unit["label"] for robot, unit in cfg["robots"].items()}
        self.track = None
        map_path = Path(self.map_yaml).expanduser()
        if not map_path.is_absolute():
            roots = [Path.cwd(), *Path.cwd().parents, *Path(fleet_path).resolve().parents, *Path(__file__).resolve().parents]
            map_path = next((root / map_path for root in roots if (root / map_path).is_file()), map_path)
        plan_path = ui_path.parent.parent / options.get("floor_plan_yaml", "config/floor_plan.yaml")
        plan = yaml.safe_load(plan_path.read_text()) if plan_path.is_file() else None
        self.meta, self.image = ui_api.map_meta(map_path, options["map_rotate"],
                                               options.get("map_deskew", "auto"),
                                               options.get("map_padding_m", .25), plan)
        self.geometry.update(names=self.names, robot_names=self.robot_names,
                             event_labels={t: text.format(robot="", zone="정류장", resource="구간", kind="임무").strip(" ·")
                                           for t, text in ui_api.EVENTS.items()}, event_types=sorted(ui_api.EVENTS), states=ui_api.STATES, kinds=ui_api.KINDS, rules=ui_api.RULES,
                             dev_tools=self.testing, mode=self.mode, demo=self.demo, ack_timeout_s=options["ack_timeout_s"])
        self.outputs = {topic: self.create_publisher(String, topic, 10)
                        for topic in ("/ui/mission_req", "/ui/operator_cmd", "/ui/decision")}
        if self.testing:
            self.bed_lock = Lock()
            self.bed_mode = "off"
            self.bed_pub = self.create_publisher(Bool, "/patient/bed_exit", 10)
            self.create_timer(0.1, self.publish_fake_bed)
            self.mock_pub = self.create_publisher(String, "/mock/control", 10)
        self.triggers = {s: self.create_client(Trigger, s) for s in ("/robot/pause", "/robot/resume")}
        for topic, kind, parser in (
            ("/fleet/state", "state", schemas.parse_fleet_state),
            ("/fleet/decision_req", "card", schemas.parse_decision_req),
            ("/mission/event", "event", schemas.parse_event),
            ("/ui/ack", "ack", schemas.parse_ack),
        ):
            self.create_subscription(String, topic, partial(self.receive, kind, parser), 100)

    def detect_mock(self):
        self.detected_demo = "mock_hospital_nav" in self.get_node_names()

    def receive(self, kind, parser, msg):
        try:
            data = parser(msg.data)
            if kind == "state":
                if self.testing:
                    data["fake_bed_mode"] = self.bed_mode
                data["demo"] = self.detected_demo
                data["summary"] = ui_api.summary(data)
                self.track, checks = ui_api.resume_checks(data, self.track)
                data["resume_track"], data["resume_checks"] = self.track, checks
                if checks and all(checks):
                    self.track = None
            elif kind == "event":
                card = self.feed.cards.get(data["detail"].get("card_id"))
                data = ui_api.present_event(data, self.names, self.robot_names, card)
            self.feed.receive(kind, data)
        except ValueError as exc:
            self.get_logger().warning(str(exc), throttle_duration_sec=2.0)

    def publish_fake_bed(self):
        with self.bed_lock:
            if self.bed_mode != "off":
                self.bed_pub.publish(Bool(data=self.bed_mode == "exit"))

    def set_bed_mode(self, mode):
        with self.bed_lock:
            self.bed_mode = mode

    def publish_request(self, topic, text):
        publisher = self.mock_pub if self.testing and topic == "/mock/control" else self.outputs[topic]
        if not publisher.get_subscription_count():
            raise ConnectionError("관제 요청 구독자가 연결되지 않았습니다.")
        publisher.publish(String(data=text))

    def trigger(self, topic):
        client = self.triggers[topic]
        if not client.wait_for_service(timeout_sec=1):
            raise TimeoutError("관제 서비스에 연결할 수 없습니다.")
        done = Event()
        future = client.call_async(Trigger.Request())
        future.add_done_callback(lambda _: done.set())
        if not done.wait(3):
            client.remove_pending_request(future)
            raise TimeoutError("관제 서비스 응답 시간이 초과됐습니다.")
        reply = future.result()
        return reply.success, reply.message


def main(args=None):
    rclpy.init(args=args)
    node = UIServer()
    app = create_app(node.feed, node.geometry, node.publish_request, node.trigger,
                     meta=node.meta, image=node.image, dev_tools=node.testing, set_bed_mode=node.set_bed_mode, db_path=node.db_path)
    server = make_server(node.host, node.port, app, threaded=True)
    server.timeout = 0.25

    def spin():
        try:
            rclpy.spin(node)
        except ExternalShutdownException:
            pass

    thread = Thread(target=spin, name="monitor-ros")
    thread.start()
    node.get_logger().info(f"병원 다중 AMR 관제 http://{node.host}:{node.port}")
    try:
        while rclpy.ok():
            server.handle_request()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        rclpy.try_shutdown()
        thread.join()
        node.destroy_node()
