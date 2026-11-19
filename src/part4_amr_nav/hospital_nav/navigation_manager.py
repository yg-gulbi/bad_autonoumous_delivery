"""Non-blocking Nav2 NavigateToPose client for one TurtleBot4.

MissionManager sends the goal and reads remaining() to report progress.
"""
from __future__ import annotations

import math
import threading

from action_msgs.msg import GoalStatus
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from rclpy.node import Node

from .goal_manager import GoalPoint


class NavigationManager(Node):
    _BUSY = frozenset({"SENDING", "MOVING", "CANCELING"})

    def __init__(self, namespace: str = "/robot3") -> None:
        # Relative action name resolves under this node's namespace.
        super().__init__("hospital_navigation_manager", namespace=namespace)
        self._client = ActionClient(self, NavigateToPose, "navigate_to_pose")
        self._lock = threading.RLock()
        self._handle = None
        self._room: str | None = None
        self._state = "IDLE"
        self._cancel_requested = False
        self._finished = threading.Event()
        self._finished.set()
        self._remaining: float | None = None  # latest Nav2 distance_remaining
        self._recoveries = 0  # Nav2 number_of_recoveries for the current goal

    def status(self) -> tuple[str, str | None]:
        with self._lock:
            return self._state, self._room

    def remaining(self) -> float | None:
        """Latest path distance left to the goal (m), None before first feedback."""
        with self._lock:
            return self._remaining

    def ready(self) -> bool:
        """True when Nav2's navigate_to_pose action server is up (Nav2 active)."""
        return self._client.server_is_ready()

    def recoveries(self) -> int:
        """Recovery behaviours (spin/backup/wait) Nav2 ran for the current goal."""
        with self._lock:
            return self._recoveries

    def is_busy(self) -> bool:
        with self._lock:
            return self._state in self._BUSY

    def send_pose(self, destination: GoalPoint, label: str = "WAYPOINT") -> bool:
        """Start one Nav2 action (non-blocking)."""
        if not all(math.isfinite(v) for v in
                   (destination.x, destination.y, destination.yaw_deg)):
            self.get_logger().error("잘못된 Goal 좌표")
            return False

        with self._lock:
            if self._state in self._BUSY:
                self.get_logger().warning(
                    f"현재 {self._room} 이동 중입니다. 먼저 X로 취소하세요."
                )
                return False
            self._state = "SENDING"
            self._room = label
            self._handle = None
            self._cancel_requested = False
            self._remaining = None
            self._recoveries = 0
            self._finished.clear()

        if not self._client.wait_for_server(timeout_sec=5.0):
            self.get_logger().error(
                "Nav2 액션 서버를 찾지 못했습니다. "
                "ros2 action info /<namespace>/navigate_to_pose 를 확인하세요."
            )
            self._finish("SERVER_UNAVAILABLE")
            return False

        goal = NavigateToPose.Goal()
        goal.pose.header.frame_id = "map"
        goal.pose.header.stamp = self.get_clock().now().to_msg()
        goal.pose.pose.position.x = destination.x
        goal.pose.pose.position.y = destination.y
        goal.pose.pose.position.z = 0.0

        half_yaw = math.radians(destination.yaw_deg) / 2.0
        goal.pose.pose.orientation.z = math.sin(half_yaw)
        goal.pose.pose.orientation.w = math.cos(half_yaw)

        self.get_logger().info(
            f"[GOAL] {self._room} → x={destination.x:.3f}, "
            f"y={destination.y:.3f}, yaw={destination.yaw_deg:.0f}°"
        )
        try:
            future = self._client.send_goal_async(
                goal, feedback_callback=self._feedback_callback
            )
            future.add_done_callback(self._goal_response_callback)
        except Exception as exc:
            self.get_logger().error(f"Goal 전송 실패: {exc}")
            self._finish("FAILED")
            return False
        return True

    def cancel(self) -> bool:
        """Request cancellation of the active Nav2 goal (asynchronous)."""
        with self._lock:
            if self._state not in self._BUSY:
                self.get_logger().info("취소할 활성 Goal이 없습니다.")
                return False
            if self._cancel_requested:
                return True
            self._cancel_requested = True
            self._state = "CANCELING"
            handle = self._handle

        if handle is None:
            # The goal is still waiting for acceptance; cancel when accepted.
            self.get_logger().warning("Goal 수락 대기 중입니다. 수락되면 취소합니다.")
        else:
            self._request_cancel(handle)
        return True

    def wait_until_finished(self, timeout: float = 10.0) -> bool:
        return self._finished.wait(timeout=timeout)

    def _goal_response_callback(self, future) -> None:
        try:
            handle = future.result()
            if handle is None or not handle.accepted:
                self.get_logger().error("Nav2에서 Goal을 거절했습니다.")
                self._finish("REJECTED")
                return

            with self._lock:
                self._handle = handle
                should_cancel = self._cancel_requested
                if not should_cancel:
                    self._state = "MOVING"

            result_future = handle.get_result_async()
            result_future.add_done_callback(self._result_callback)
            if should_cancel:
                self._request_cancel(handle)
            else:
                self.get_logger().info("[MOVING] 자율주행 시작")
        except Exception as exc:
            self.get_logger().error(f"Goal 응답 처리 실패: {exc}")
            self._finish("FAILED")

    def _feedback_callback(self, feedback_msg) -> None:
        with self._lock:
            if self._state == "MOVING":
                self._remaining = float(feedback_msg.feedback.distance_remaining)
                self._recoveries = int(feedback_msg.feedback.number_of_recoveries)

    def _request_cancel(self, handle) -> None:
        try:
            future = handle.cancel_goal_async()
            future.add_done_callback(self._cancel_response_callback)
            self.get_logger().warning("[CANCEL] Nav2에 Goal 취소 요청 전송")
        except Exception as exc:
            self.get_logger().error(f"Goal 취소 요청 실패: {exc}")
            # Keep the state active until the actual result arrives.

    def _cancel_response_callback(self, future) -> None:
        try:
            response = future.result()
            if not response.goals_canceling:
                self.get_logger().warning(
                    "Nav2가 취소를 승인하지 않았습니다. 실제 주행 상태를 확인하세요."
                )
        except Exception as exc:
            self.get_logger().error(f"취소 응답 확인 실패: {exc}")

    def _result_callback(self, future) -> None:
        try:
            result = future.result()
            if result.status == GoalStatus.STATUS_SUCCEEDED:
                state = "SUCCEEDED"
                self.get_logger().info(f"[ARRIVED] {self._room} 구역 도착")
            elif result.status == GoalStatus.STATUS_CANCELED:
                state = "CANCELED"
                self.get_logger().warning(f"[CANCELED] {self._room} 이동 취소됨")
            elif result.status == GoalStatus.STATUS_ABORTED:
                state = "ABORTED"
                self.get_logger().error(f"[ABORTED] {self._room} 주행 중단됨")
            else:
                state = "FAILED"
                self.get_logger().error(
                    f"[FAILED] {self._room}, action status={result.status}"
                )
            self._finish(state)
        except Exception as exc:
            self.get_logger().error(f"주행 결과 확인 실패: {exc}")
            self._finish("FAILED")

    def _finish(self, state: str) -> None:
        with self._lock:
            self._state = state
            self._handle = None
            self._cancel_requested = False
            self._finished.set()
