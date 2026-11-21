"""Observe the control room's ROS graph for ten seconds. No robot or camera commands."""
import argparse
import json
from functools import partial
from math import isfinite
import os
from pathlib import Path
import sys
from time import monotonic

from ament_index_python.packages import get_package_share_path
from geometry_msgs.msg import PoseWithCovarianceStamped
import psutil
import rclpy
from rclpy.action.graph import get_action_server_names_and_types_by_node
from rclpy.node import Node, NodeNameNonExistentError
from rclpy.qos import qos_profile_sensor_data
from rclpy.utilities import remove_ros_args
from sensor_msgs.msg import BatteryState
from std_msgs.msg import Bool, String

from .core.config import ConfigError, load_config
from .preflight_checks import (classifier_line, compare_points, discovery_ports, find_goal_file, goal_points,
                               running_mode_line, slot_line, state_rows, traffic_mode_line)
from .robot_link import AMCL_QOS, DockStatus, HazardDetectionVector

ENVIRONMENT = ("ROS_DOMAIN_ID", "ROS_DISCOVERY_SERVER", "ROS_SUPER_CLIENT", "ROS_AUTOMATIC_DISCOVERY_RANGE",
               "RMW_IMPLEMENTATION")


def inspect_environment():
    for key in ENVIRONMENT:
        print(f"{key}={os.environ.get(key) or '(미설정)'}")
    problems = []
    if os.getenv("ROS_AUTOMATIC_DISCOVERY_RANGE", "").upper() == "LOCALHOST":
        print("경고 · LOCALHOST 범위입니다. 다른 PC의 로봇을 발견할 수 없습니다.")
    server = os.getenv("ROS_DISCOVERY_SERVER", "")
    if not server.strip("; "):
        print("경고 · ROS_DISCOVERY_SERVER 미설정. 강의실 관제 PC의 발견 서버 주소를 확인하세요.")
    else:
        if os.getenv("ROS_SUPER_CLIENT", "").lower() != "true":
            print("경고 · 전체 그래프를 점검하려면 ROS_SUPER_CLIENT=True가 필요합니다.")
        try:
            checks = discovery_ports(server)
        except (OSError, psutil.Error) as exc:
            checks = [(server, False, f"UDP 소켓 조회 실패 ({exc})")]
        for endpoint, ok, message in checks:
            print(f"발견 서버 {endpoint} · {'OK' if ok else '경고'} · {message}")
        if not any(ok for _, ok, _ in checks):
            problems.append("관제 PC의 설정 포트에서 Fast DDS 발견 서버를 확인할 수 없습니다.")
    return problems


class Observations(Node):
    def __init__(self, config):
        super().__init__("fleet_preflight", enable_rosout=False, start_parameter_services=False)
        self.config = config
        self.bed_samples = 0
        self.bed_value = None
        self.states = {}
        self.seen = {}
        self.slots = {}  # ns -> STATE carried a "slot" key (4J PART 04)
        self.running_mode = None  # traffic.mode of a running control room (/fleet/state)
        self.create_subscription(Bool, "/patient/bed_exit", self.bed, 10)
        self.create_subscription(String, "/fleet/state", self.fleet_state, 10)
        topics = config.robot_link
        for unit in config.robots.values():
            ns = unit.ns
            self.seen[ns] = {}
            def listen(msg_type, path, qos, callback):
                self.create_subscription(msg_type, path.format(ns=ns), partial(callback, ns), qos)
            listen(String, topics.status_topic, 10, self.status)
            listen(PoseWithCovarianceStamped, topics.amcl_pose_topic, AMCL_QOS, self.pose)
            listen(BatteryState, topics.battery_topic, qos_profile_sensor_data, self.battery)
            if DockStatus is not None:
                listen(DockStatus, topics.dock_status_topic, qos_profile_sensor_data, self.dock)
                if config.safety.use_hazard_topic:
                    listen(HazardDetectionVector, topics.hazard_topic, qos_profile_sensor_data, self.hazard)

    def bed(self, msg):
        self.bed_samples += 1
        self.bed_value = msg.data

    def fleet_state(self, msg):
        try:
            state = json.loads(msg.data)
        except (ValueError, RecursionError):
            return
        mode = (state.get("traffic") or {}).get("mode") if isinstance(state, dict) else None
        if isinstance(mode, str):
            self.running_mode = mode

    def status(self, ns, msg):
        try:
            report = json.loads(msg.data)
        except (ValueError, RecursionError):
            return
        if (isinstance(report, dict) and report.get("type") == "STATE" and report.get("robot_id") == ns
                and isinstance(report.get("state"), str)):
            self.states[ns] = (report, monotonic())
            self.slots[ns] = self.slots.get(ns, False) or "slot" in report

    def pose(self, ns, msg):
        pos = msg.pose.pose.position
        if isfinite(pos.x) and isfinite(pos.y):
            self.seen[ns]["amcl_pose"] = f"({pos.x:.3f}, {pos.y:.3f})"

    def battery(self, ns, msg):
        self.seen[ns]["battery_state"] = (f"{100 * msg.percentage:.1f}%" if 0 <= msg.percentage <= 1
                                          else "잔량 미상")

    def dock(self, ns, msg):
        self.seen[ns]["dock_status"] = "도킹 중" if msg.is_docked else "도크 밖"

    def hazard(self, ns, msg):
        self.seen[ns]["hazard_detection"] = f"감지 {len(msg.detections)}건"

    def action_servers(self):
        names = set()
        for name, namespace in self.get_node_names_and_namespaces():
            try:
                actions = get_action_server_names_and_types_by_node(self, name, namespace)
            except NodeNameNonExistentError:
                continue
            names.update(action for action, types in actions if "nav2_msgs/action/NavigateToPose" in types)
        return names

    def report(self, duration):
        failures = []
        actions = self.action_servers()
        for unit in self.config.robots.values():
            ns = unit.ns
            data = self.seen[ns]
            report, last = self.states.get(ns, ({}, float("-inf")))
            fresh = monotonic() - last <= self.config.robot_link.link_timeout_s
            rows = state_rows(report if fresh else {})
            missing = [key for key, value in rows.items() if value is None]
            print(f"\n{unit.label} (/{ns}) · {'미충족' if missing else 'OK'}")
            for key, value in rows.items():
                print(f"  {key:26} {'OK · ' + value if value is not None else '없음'}")
            for key in ("amcl_pose", "battery_state", "dock_status", "hazard_detection", "navigate_to_pose"):
                value = data.get(key, "미수신")
                if key == "hazard_detection" and not self.config.safety.use_hazard_topic:
                    value = "사용 안 함"
                elif key == "navigate_to_pose":
                    value = "액션 서버 있음" if f"/{ns}/navigate_to_pose" in actions else "미발견"
                print(f"  참고 {key:23} {value}")
            failures.extend(f"/{ns} {key} 없음" for key in missing)
            command = self.config.robot_link.command_topic.format(ns=ns)
            publishers = [f"{p.node_namespace.rstrip('/')}/{p.node_name}" for p in
                          self.get_publishers_info_by_topic(command)]
            others = [p for p in publishers if p != "/fleet_manager"]
            if others or publishers.count("/fleet_manager") > 1:
                reason = f"{command} 명령 발행자 충돌 · {', '.join(publishers)}"
                print("  경고 · " + reason)
                failures.append(reason)
            else:
                print("  명령 발행자 · " + (", ".join(publishers) or "없음 (관제 실행 전 가능)"))
            print("  자리 번호(STATE.slot) · " + slot_line(self.slots.get(ns)))
            obstacle = self.config.obstacle
            image = obstacle.image_topic.format(ns=ns)
            readers = [f"{s.node_namespace.rstrip('/')}/{s.node_name}" for s in self.get_subscriptions_info_by_topic(image)]
            print(f"  {image} · " + classifier_line(readers, obstacle.mode))
            result = obstacle.result_topic.format(ns=ns)
            found = any(p.topic_type == "std_msgs/msg/String" for p in self.get_publishers_info_by_topic(result))
            fallback = "없음 — 판별 없이 사진만 표시" if obstacle.mode == "report" else "없음 — 30초 규칙으로 동작"
            print(f"  {result} · " + ("발행자 있음" if found else fallback))
        text = "없음" if self.bed_value is None else "이탈" if self.bed_value else "정상"
        print(f"\n/patient/bed_exit · {self.bed_samples/duration:.1f} Hz ({self.bed_samples}건, 마지막 값 {text})")
        print(running_mode_line(self.running_mode))
        if DockStatus is None:
            topics = "dock_status·hazard_detection" if self.config.safety.use_hazard_topic else "dock_status"
            print(f"참고 · irobot_create_msgs 미설치로 {topics} 원본 구독 생략")
        return failures


def main(args=None):
    argv = sys.argv if args is None else ["preflight", *args]
    parser = argparse.ArgumentParser(description="관제 PC의 읽기 전용 연결 점검 (10초)")
    default = get_package_share_path("part3_fleet_manager") / "config/fleet.yaml"
    parser.add_argument("--config-file", default=str(default), help="관제가 사용할 fleet.yaml")
    parser.add_argument("--goals-file", help="PART 04 goal_manager.py (기본은 워크스페이스에서 탐색)")
    options = parser.parse_args(remove_ros_args(argv)[1:])
    problems = inspect_environment()
    try:
        config = load_config(Path(options.config_file).expanduser())
    except ConfigError as exc:
        print(f"설정 오류 · {exc}\n실제 실행 안 됨")
        return 1
    print(traffic_mode_line(config))
    for warning in config.exit_warnings():
        print("경고 · " + warning)
    goals = Path(options.goals_file).expanduser() if options.goals_file else find_goal_file(options.config_file)
    try:
        if goals is None:
            raise ValueError("goal_manager.py 위치를 --goals-file로 지정하세요.")
        part4 = goal_points(goals)
        differences = compare_points(config, part4)
        layout = ("자리 2개·교차로 (SLOTS)" if part4["format"] == "SLOTS"
                  else "예전 형식 (GOALS, 자리 1개)")
        print(f"구역 대조 ({goals}) · {layout} · " + ("경고" if differences else "OK"))
        for warning in differences:
            print("  경고 · " + warning)
    except (OSError, SyntaxError, ValueError, OverflowError) as exc:
        print(f"경고 · PART 04 좌표 확인 불가 · {exc}")
    print("로봇 두 대와 침상 감시를 10초 동안 관측합니다.", flush=True)
    node = None
    try:
        rclpy.init(args=argv)
        node = Observations(config)
        start = monotonic()
        while (remaining := start + 10.0 - monotonic()) > 0:
            rclpy.spin_once(node, timeout_sec=remaining)
        problems.extend(node.report(monotonic() - start))
    except RuntimeError as exc:
        problems.append(f"ROS 그래프 관측 실패 · {exc}")
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.try_shutdown()
    for reason in problems:
        print("  안 되는 이유 · " + reason)
    print("실제 실행 안 됨" if problems else "실제 실행 가능")
    return int(bool(problems))
