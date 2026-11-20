#!/usr/bin/env python3
"""Control console: send destinations, watch each robot's progress.

Each robot runs `command_receiver`. This console publishes to
/<robot>/hospital_nav/command and prints what the robots report on
/<robot>/hospital_nav/status (remaining distance and progress every second).

    ros2 run hospital_nav keyboard_control --robots robot2 robot3

The robots are called A, B, ... in the order given (robot2 = A, robot3 = B).

    A F | A F 2 | A MOVE F 2                  -> robot A goes to room F (priority 1~3)
                                              (free slot F1 or F2 is picked; A F2 forces one)
    A UNDOCK | A DOCK                         -> leave the dock / return to the dock
    B PAUSE | RESUME | CANCEL                 -> control robot B
    S                                         -> status table
    Q                                         -> quit console only (robots keep going)
"""
import argparse
import json
import os
import threading
import time

import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import String

from .command_receiver import parse_command
from .goal_manager import format_goals

# Robot states that mean "going to, or standing at, its destination slot".
OCCUPYING = {"UNDOCKING", "LOCALIZING", "MOVING", "STOPPING_PAUSE", "PAUSED", "COMPLETE"}
CLAIM_FRESH_S = 3.0  # trust our own last command until the robot's STATE catches up


class ControlConsole(Node):
    def __init__(self, robots):
        super().__init__("hospital_control_console")
        self.robots, self.cmd_pub, self.latest = robots, {}, {}
        self.claims = {}  # robot -> (room, slot, time) of the last MOVE this console sent
        self.alias = {chr(ord("A") + i): r for i, r in enumerate(robots)}  # A -> robot2
        self.label = {r: a for a, r in self.alias.items()}
        self.lock = threading.Lock()
        for r in robots:
            self.cmd_pub[r] = self.create_publisher(String, f"/{r}/hospital_nav/command", 10)
            self.create_subscription(String, f"/{r}/hospital_nav/status",
                                     lambda m, r=r: self._on_status(r, m), 10)
            self.create_subscription(CompressedImage, f"/{r}/hospital_nav/obstacle_image",
                                     lambda m, r=r: self._on_obstacle_image(r, m), 10)

    def _on_obstacle_image(self, robot, msg):
        """Save the one photo a robot sends when Nav2 aborts."""
        folder = os.path.expanduser("~/hospital_nav_captures")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{robot}_{time.strftime('%Y%m%d_%H%M%S')}.jpg")
        with open(path, "wb") as f:
            f.write(bytes(msg.data))
        print(f"\n[로봇 {self.label[robot]}] 장애물 사진 저장: {path}", flush=True)

    def _on_status(self, robot, msg):
        d = json.loads(msg.data)
        if d.get("type") == "STATE":
            with self.lock:
                self.latest[robot] = {**d, "_rx": time.monotonic()}
            return
        event = d.get("event")
        if event == "PROGRESS":
            line = f"{d['destination']} 남은 거리 {d['remaining_m']:.2f} m ({d['percent']:.1f}%)"
        elif event == "OBSTACLE":
            def m(v):
                return "-" if v is None else f"{v:.2f} m"
            rp = d.get("robot_pose") or {}
            where = (f"로봇위치=({rp.get('x')}, {rp.get('y')}, {rp.get('yaw_deg')}°) "
                     f"방향={d.get('bearing_deg')}° 장애물좌표=({d.get('map_x')}, {d.get('map_y')})")
            if d.get("source") == "yolo":  # real-time detection: robot paused itself
                line = (f"!!! 장애물 감지 (YOLO {d.get('class')} {d.get('conf')}, 목적지 "
                        f"{d.get('destination')}, 계속 주행) 거리={m(d.get('depth_m'))} {where}")
            else:  # Nav2 gave up
                line = (f"!!! 장애물 발견 (목적지 {d.get('destination')} 이동 중단) "
                        f"뎁스={m(d.get('depth_m'))} 라이다={m(d.get('lidar_m'))} {where} "
                        f"수신={d.get('captured')}")
        else:
            extra = {k: v for k, v in d.items()
                     if k not in ("type", "event", "robot_id", "mission_id") and v not in (None, "")}
            line = f"{event} {extra or ''}"
        print(f"\n[로봇 {self.label[robot]}] {line}", flush=True)

    def command(self, robot, text):
        action, room, priority, slot = parse_command(text)  # ValueError on bad syntax
        if action == "MOVE" and room != "DOCK":
            taken = self._taken_slots(room, robot)
            if slot is None:  # pick the free slot: inner (1) first, then entrance side (2)
                slot = 1 if 1 not in taken else 2
                if {1, 2} <= taken:
                    print(f"[경고] {room} 두 자리 모두 사용 중 → {room}1 로 보냄")
                    slot = 1
            elif slot in taken:
                print(f"[경고] {room}{slot} 은 다른 로봇이 사용 중")
            text = f"MOVE {room}{slot} {priority}"
            print(f"  → {self.label[robot]}({robot}) 에 {room}{slot} 자리 배정")
        with self.lock:
            self.claims[robot] = (room, slot, time.monotonic()) if action == "MOVE" else None
        self.cmd_pub[robot].publish(String(data=text))

    def _taken_slots(self, room, me):
        """Slots of `room` that other robots are heading to or standing at."""
        taken, now = set(), time.monotonic()
        with self.lock:
            for r in self.robots:
                if r == me:
                    continue
                claim, d = self.claims.get(r), self.latest.get(r)
                if claim and now - claim[2] < CLAIM_FRESH_S:  # STATE may not show it yet
                    if claim[0] == room:
                        taken.add(claim[1])
                elif d and d.get("state") in OCCUPYING and d.get("destination") == room:
                    taken.add(d.get("slot") or 1)
        return taken

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
                print(f"  {name:12} {d['state']:10} {dock} {ready} {batt} dest={(d['destination'] or '-') + str(d.get('slot') or '')} "
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
