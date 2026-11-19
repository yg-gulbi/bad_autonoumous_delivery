#!/usr/bin/env python3
"""Control-console text commands -> MissionManager.

Subscribe: /<robot>/hospital_nav/command (std_msgs/String)
Examples: 'F', 'F 3', 'MOVE F 3', 'DOCK', 'UNDOCK', 'PAUSE', 'RESUME', 'CANCEL'
"""
import argparse
import signal
import threading

import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.signals import SignalHandlerOptions
from std_msgs.msg import String

from .goal_manager import GoalPoint
from .mission_manager import MissionManager

CONTROL = ("PAUSE", "RESUME", "CANCEL", "UNDOCK")


def parse_command(text):
    """-> (action, room, priority, slot). Room: A~F, or A1/A2 ... F1/F2 for a
    specific slot (slot is None when not given -> the robot uses slot 1)."""
    words = text.strip().upper().split()
    if len(words) == 1 and words[0] in CONTROL:
        return words[0], None, None, None
    if words == ["DOCK"]:
        return "MOVE", "DOCK", 1, None
    if words and words[0] == "MOVE":
        words = words[1:]
    room = words[0] if words else ""
    if (len(words) not in (1, 2) or len(room) not in (1, 2) or room[0] not in "ABCDEF"
            or (len(room) == 2 and room[1] not in "12")):
        raise ValueError("A~F 또는 A1/A2, MOVE F [1~3], DOCK, UNDOCK, PAUSE, RESUME, CANCEL 중 하나")
    if len(words) == 2 and words[1] not in ("1", "2", "3"):
        raise ValueError("응급도는 1~3 정수")
    slot = int(room[1]) if len(room) == 2 else None
    return "MOVE", room[0], int(words[1]) if len(words) == 2 else 1, slot


def execute(mission, text):
    """Parse and run one command. Returns (action, accepted)."""
    action, room, priority, slot = parse_command(text)
    if action == "MOVE":
        return action, mission.start(room, priority=priority, slot=slot or 1)
    return action, {"PAUSE": lambda: mission.pause("관제 요청"),
                    "RESUME": mission.resume,
                    "CANCEL": mission.cancel_mission,
                    "UNDOCK": mission.undock}[action]()


def init_ros():
    # Keep the ROS context alive on Ctrl+C/SIGTERM so the Nav2 cancel can
    # still be sent; rclpy's own handler would shut the context down first.
    rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
    signal.signal(signal.SIGTERM, signal.default_int_handler)


def run(mission, *extra_nodes):
    """Spin all nodes in the background; on any exit cancel and wait for Nav2."""
    nodes = (mission, mission.nav, mission.dock, mission.obstacle, *extra_nodes)
    executor = MultiThreadedExecutor(num_threads=4)
    for node in nodes:
        executor.add_node(node)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    try:
        while thread.is_alive():
            thread.join(0.5)
    except KeyboardInterrupt:
        print("\n종료 요청: 진행 중인 주행을 취소합니다...")
    finally:
        if not mission.shutdown(10.0):
            print("[경고] Nav2 취소 미확인 — 로봇 상태/비상정지 확인")
        executor.shutdown()
        for node in reversed(nodes):
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        thread.join(timeout=2)


class CommandReceiver(Node):
    def __init__(self, mission):
        super().__init__("hospital_command_receiver", namespace="/" + mission.robot)
        self.mission = mission
        self.create_subscription(String, "hospital_nav/command", self.on_command, 10)

    def on_command(self, msg):
        try:
            action, ok = execute(self.mission, msg.data)
            self.get_logger().info(f"{msg.data.strip()}: {'ACCEPTED' if ok else 'REJECTED'}")
        except ValueError as exc:
            self.get_logger().warning(f"관제 명령 무시 ({msg.data!r}): {exc}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--robot", default="robot3")
    parser.add_argument("--dock", nargs=3, type=float, metavar=("X", "Y", "YAW_DEG"),
                        help="도킹 상태 위치 (기본: goal_manager.DOCKS)")
    args = parser.parse_args()
    init_ros()
    mission = MissionManager(args.robot, GoalPoint(*args.dock) if args.dock else None)
    run(mission, CommandReceiver(mission))


if __name__ == "__main__":
    main()
