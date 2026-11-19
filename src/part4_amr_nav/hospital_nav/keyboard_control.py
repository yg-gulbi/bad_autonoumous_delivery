#!/usr/bin/env python3
"""Control console: send destinations, watch each robot's progress.

Each robot runs `command_receiver`. This console publishes to
/<robot>/hospital_nav/command and prints what the robots report on
/<robot>/hospital_nav/status (remaining distance and progress every second).

    ros2 run hospital_nav keyboard_control --robots robot2 robot3

The robots are called A, B, ... in the order given (robot2 = A, robot3 = B).

    A F | A F 2 | A MOVE F 2                  -> robot A goes to room F (priority 1~3)
    A UNDOCK | A DOCK                         -> leave the dock / return to the dock
    B PAUSE | RESUME | CANCEL                 -> control robot B
    S                                         -> status table
    Q                                         -> quit console only (robots keep going)
"""
import argparse
import json
import threading
import time

import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import String

from .command_receiver import parse_command
from .goal_manager import format_goals


class ControlConsole(Node):
    def __init__(self, robots):
        super().__init__("hospital_control_console")
        self.robots, self.cmd_pub, self.latest = robots, {}, {}
        self.alias = {chr(ord("A") + i): r for i, r in enumerate(robots)}  # A -> robot2
        self.label = {r: a for a, r in self.alias.items()}
        self.lock = threading.Lock()
        for r in robots:
            self.cmd_pub[r] = self.create_publisher(String, f"/{r}/hospital_nav/command", 10)
            self.create_subscription(String, f"/{r}/hospital_nav/status",
                                     lambda m, r=r: self._on_status(r, m), 10)

    def _on_status(self, robot, msg):
        d = json.loads(msg.data)
        if d.get("type") == "STATE":
            with self.lock:
                self.latest[robot] = {**d, "_rx": time.monotonic()}
            return
        event = d.get("event")
        if event == "PROGRESS":
            line = f"{d['destination']} 남은 거리 {d['remaining_m']:.2f} m ({d['percent']:.1f}%)"
        else:
            extra = {k: v for k, v in d.items()
                     if k not in ("type", "event", "robot_id", "mission_id") and v not in (None, "")}
            line = f"{event} {extra or ''}"
        print(f"\n[로봇 {self.label[robot]}] {line}", flush=True)

    def command(self, robot, text):
        parse_command(text)  # raises ValueError on bad syntax
        self.cmd_pub[robot].publish(String(data=text))

    def print_status(self):
        with self.lock:
            for r in self.robots:
                d = self.latest.get(r)
                name = f"{self.label[r]}({r})"
                if not d:
                    print(f"  {name:12} OFFLINE")
                    continue
                pct = f"{d['percent']:.1f}%" if d.get("percent") is not None else "-"
                rem = f"{d['remaining_m']:.2f} m" if d.get("remaining_m") is not None else "-"
                silent = " [응답없음]" if time.monotonic() - d["_rx"] > 3.0 else ""
                dock = {True: "도크위", False: "도크밖"}.get(d.get("docked"), "도크?")
                ready = "READY" if d.get("ready") else "준비안됨"
                b = d.get("battery") or {}
                batt = (f"배터리={b['percent']:.0f}%" if b.get("percent") is not None else "배터리=?") \
                    + (" 충전중" if b.get("status") == "CHARGING" else "")
                print(f"  {name:12} {d['state']:10} {dock} {ready} {batt} dest={d['destination'] or '-'} "
                      f"남은거리={rem} 진행률={pct}{silent} {d.get('reason') or ''}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--robots", nargs="+", required=True)
    args = parser.parse_args()
    rclpy.init()
    console = ControlConsole(args.robots)
    executor = SingleThreadedExecutor()
    executor.add_node(console)
    spinner = threading.Thread(target=executor.spin, daemon=True)
    spinner.start()
    print(format_goals())
    print("로봇: " + ", ".join(f"{a}={r}" for a, r in console.alias.items()))
    print("A F [1~3] (도크 위면 자동 언독) | A DOCK | A UNDOCK | B PAUSE/RESUME/CANCEL | S 상태 | Q 종료")
    try:
        while rclpy.ok():
            try:
                words = input("관제 > ").split()
            except EOFError:
                break
            if not words:
                continue
            if words[0].upper() == "Q":
                break
            if words[0].upper() == "S":
                console.print_status()
            elif len(words) >= 2 and (words[0].upper() in console.alias or words[0] in console.robots):
                robot = console.alias.get(words[0].upper(), words[0])
                try:
                    console.command(robot, " ".join(words[1:]))
                except ValueError as exc:
                    print(exc)
            else:
                print("형식: A F 2 | B PAUSE/RESUME/CANCEL | S | Q  ("
                      + ", ".join(f"{a}={r}" for a, r in console.alias.items()) + ")")
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        spinner.join(timeout=2)
        console.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
