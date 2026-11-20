"""Create3 dock/undock actions, dock status and AMCL initial pose for one robot.

Non-blocking: call undock()/dock(), then poll state()
(IDLE / RUNNING / SUCCEEDED / FAILED / CANCELED).
"""
from __future__ import annotations

import math
import threading

from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseWithCovarianceStamped
from irobot_create_msgs.action import Dock, Undock
from irobot_create_msgs.msg import DockStatus
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data

from .goal_manager import GoalPoint


class DockManager(Node):
    def __init__(self, namespace: str = "/robot3") -> None:
        super().__init__("hospital_dock_manager", namespace=namespace)
        self._actions = {"UNDOCK": (ActionClient(self, Undock, "undock"), Undock),
                         "DOCK": (ActionClient(self, Dock, "dock"), Dock)}
        self._lock = threading.Lock()
        self._state, self._handle, self._cancel = "IDLE", None, False
        self.is_docked: bool | None = None
        self.create_subscription(DockStatus, "dock_status", self._on_dock,
                                 qos_profile_sensor_data)
        self._pose_pub = self.create_publisher(PoseWithCovarianceStamped, "initialpose", 10)

    def _on_dock(self, msg) -> None:
        self.is_docked = bool(msg.is_docked)

    def state(self) -> str:
        with self._lock:
            return self._state

    def is_busy(self) -> bool:
        return self.state() == "RUNNING"

    def undock(self) -> bool:
        return self._send("UNDOCK")

    def dock(self) -> bool:
        return self._send("DOCK")

    def cancel(self) -> None:
        with self._lock:
            self._cancel = True
            handle = self._handle
        if handle is not None:
            handle.cancel_goal_async()

    def _send(self, name: str) -> bool:
        client, action = self._actions[name]
        with self._lock:
            if self._state == "RUNNING":
                return False
            self._state, self._handle, self._cancel = "RUNNING", None, False
        if not client.wait_for_server(timeout_sec=5.0):
            self.get_logger().error(f"{name} 액션 서버 없음 (ros2 action list | grep dock)")
            self._set("FAILED")
            return False
        self.get_logger().info(f"[{name}] 시작")
        future = client.send_goal_async(action.Goal())
        future.add_done_callback(lambda f: self._on_accept(name, f))
        return True

    def _on_accept(self, name, future) -> None:
        handle = future.result()
        if handle is None or not handle.accepted:
            self.get_logger().error(f"{name} 거절됨")
            self._set("FAILED")
            return
        with self._lock:
            self._handle = handle
            cancel = self._cancel
        if cancel:
            handle.cancel_goal_async()
        handle.get_result_async().add_done_callback(lambda f: self._on_result(name, f))

    def _on_result(self, name, future) -> None:
        status = future.result().status
        state = {GoalStatus.STATUS_SUCCEEDED: "SUCCEEDED",
                 GoalStatus.STATUS_CANCELED: "CANCELED"}.get(status, "FAILED")
        self.get_logger().info(f"[{name}] {state}")
        self._set(state)

    def _set(self, state) -> None:
        with self._lock:
            self._state, self._handle = state, None

    def amcl_listening(self) -> bool:
        """True once AMCL has subscribed to initialpose (localization is up)."""
        return self._pose_pub.get_subscription_count() > 0

    def set_initial_pose(self, pose: GoalPoint) -> None:
        msg = PoseWithCovarianceStamped()
        msg.header.frame_id = "map"
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.pose.pose.position.x, msg.pose.pose.position.y = pose.x, pose.y
        half = math.radians(pose.yaw_deg) / 2.0
        msg.pose.pose.orientation.z, msg.pose.pose.orientation.w = math.sin(half), math.cos(half)
        cov = [0.0] * 36
        cov[0] = cov[7] = 0.25          # x, y variance (same as RViz 2D Pose Estimate)
        cov[35] = 0.0685                # yaw variance
        msg.pose.covariance = cov
        self._pose_pub.publish(msg)
        self.get_logger().info(f"[INITIAL_POSE] x={pose.x:.3f} y={pose.y:.3f} yaw={pose.yaw_deg:.0f}°")
