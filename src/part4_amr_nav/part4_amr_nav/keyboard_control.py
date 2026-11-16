#!/usr/bin/env python3
"""Keyboard test UI for the hospital TurtleBot4 Nav2 navigation manager.

Run with localization + Nav2 already active:
    python3 keyboard_control.py --robot robot3

Commands: A-F = room goals, S = status, X = cancel current goal, Q = quit.
"""
from __future__ import annotations

import argparse
import threading

import rclpy
from rclpy.executors import SingleThreadedExecutor

from .goal_manager import format_goals
from .navigation_manager import NavigationManager


def main() -> None:
    parser = argparse.ArgumentParser(description="Hospital A-F Nav2 keyboard test")
    parser.add_argument("--robot", default="robot3", help="ROS namespace (e.g. robot3)")
    args = parser.parse_args()

    rclpy.init()
    navigator = NavigationManager(namespace="/" + args.robot.strip("/"))
    executor = SingleThreadedExecutor()
    executor.add_node(navigator)
    spinner = threading.Thread(target=executor.spin, daemon=True)
    spinner.start()

    print("\n=== Hospital Nav2 Keyboard Test ===")
    print(f"Robot namespace: /{args.robot.strip('/')}")
    print("임시 Goal 좌표 (실제 방 위치 확인 후 수정 필요):")
    print(format_goals())
    print("\nA~F: 해당 방 이동 | S: 상태 | X: 현재 이동 취소 | Q: 종료")
    print("주의: 관제 허가 기능이 없는 단일 로봇 테스트용 코드입니다.\n")

    try:
        while rclpy.ok():
            try:
                command = input("목적지/명령 > ").strip().upper()
            except EOFError:
                command = "Q"

            if command in ("A", "B", "C", "D", "E", "F"):
                navigator.send_room(command)
            elif command == "S":
                state, room = navigator.status()
                print(f"상태: {state} / 목적지: {room or '-'}")
            elif command == "X":
                navigator.cancel()
            elif command == "Q":
                if navigator.is_busy():
                    print("활성 Nav2 Goal을 취소하고 종료합니다...")
                    navigator.cancel()
                    if not navigator.wait_until_finished(timeout=10.0):
                        print(
                            "[경고] 취소 완료가 확인되지 않았습니다. "
                            "로봇을 직접 확인하고 필요하면 E-stop을 사용하세요."
                        )
                break
            elif command:
                print("A~F / S(상태) / X(취소) / Q(종료) 중 입력하세요.")
    except KeyboardInterrupt:
        print("\nCtrl+C: 주행 취소를 요청합니다.")
        if navigator.is_busy():
            navigator.cancel()
            if not navigator.wait_until_finished(timeout=10.0):
                print("[경고] 취소 완료 미확인. 실제 로봇 상태를 확인하세요.")
    finally:
        executor.shutdown()
        navigator.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        spinner.join(timeout=2.0)


if __name__ == "__main__":
    main()
