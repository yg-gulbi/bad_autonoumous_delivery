#!/usr/bin/env python3
"""Reset Gazebo robot poses via set_pose, publish AMCL /initialpose, and clear Nav2 costmaps."""
import argparse
import math
import subprocess
import time

import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped
from nav2_msgs.srv import ClearEntireCostmap
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from tf2_msgs.msg import TFMessage


AMCL_QOS = QoSProfile(
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
)


def gz_set_pose(world: str, model: str, x: float, y: float, z: float = 0.01, yaw: float = 0.0) -> bool:
    qz = math.sin(yaw / 2.0)
    qw = math.cos(yaw / 2.0)
    req = (
        f'name: "{model}", '
        f'position: {{x: {x:.4f}, y: {y:.4f}, z: {z:.4f}}}, '
        f'orientation: {{x: 0.0, y: 0.0, z: {qz:.6f}, w: {qw:.6f}}}'
    )
    cmd = [
        "gz", "service", "-s", f"/world/{world}/set_pose",
        "--reqtype", "gz.msgs.Pose",
        "--reptype", "gz.msgs.Boolean",
        "--timeout", "5000",
        "--req", req,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    ok = res.returncode == 0 and "data: true" in res.stdout.lower()
    if not ok:
        print(f"[sim_reset] set_pose {model} FAILED rc={res.returncode} out={res.stdout.strip()} err={res.stderr.strip()}", flush=True)
    return ok


class _ResetNode(Node):
    def __init__(self, world: str):
        super().__init__("sim_trial_reset")
        self.world = world
        self.gt = {}
        self.amcl = {}
        self._init_pubs = {}
        self._clear_clients = {}
        for ns in ("robot3", "robot2"):
            self._init_pubs[ns] = self.create_publisher(PoseWithCovarianceStamped, f"/{ns}/initialpose", 10)
            self.create_subscription(
                TFMessage, f"/{ns}/_internal/sim_ground_truth_pose",
                lambda m, r=ns: self._on_gt(r, m), 10,
            )
            self.create_subscription(
                PoseWithCovarianceStamped, f"/{ns}/amcl_pose",
                lambda m, r=ns: self._on_amcl(r, m), AMCL_QOS,
            )
            self._clear_clients[(ns, "local")] = self.create_client(
                ClearEntireCostmap, f"/{ns}/local_costmap/clear_entirely_local_costmap"
            )
            self._clear_clients[(ns, "global")] = self.create_client(
                ClearEntireCostmap, f"/{ns}/global_costmap/clear_entirely_global_costmap"
            )

    def _on_gt(self, ns: str, msg: TFMessage):
        target = f"{ns}/turtlebot4"
        for tf in msg.transforms:
            if tf.child_frame_id == target or tf.header.frame_id == self.world:
                t = tf.transform.translation
                self.gt[ns] = (float(t.x), float(t.y))
                return

    def _on_amcl(self, ns: str, msg: PoseWithCovarianceStamped):
        p = msg.pose.pose.position
        self.amcl[ns] = (float(p.x), float(p.y))

    def spin_for(self, seconds: float):
        t0 = time.monotonic()
        while time.monotonic() - t0 < seconds:
            rclpy.spin_once(self, timeout_sec=0.05)

    def publish_initialpose(self, ns: str, x: float, y: float, yaw: float = 0.0):
        msg = PoseWithCovarianceStamped()
        msg.header.frame_id = "map"
        # stamp = 0 tells AMCL / TF2 to use the latest available transform in sim time
        msg.header.stamp.sec = 0
        msg.header.stamp.nanosec = 0
        msg.pose.pose.position.x = float(x)
        msg.pose.pose.position.y = float(y)
        msg.pose.pose.orientation.z = math.sin(yaw / 2.0)
        msg.pose.pose.orientation.w = math.cos(yaw / 2.0)
        cov = [0.0] * 36
        cov[0] = cov[7] = 0.04
        cov[35] = 0.02
        msg.pose.covariance = cov
        self._init_pubs[ns].publish(msg)

    def clear_costmaps(self, timeout_s: float = 5.0) -> dict:
        results = {}
        futs = {}
        for key, cli in self._clear_clients.items():
            if cli.wait_for_service(timeout_sec=timeout_s):
                futs[key] = cli.call_async(ClearEntireCostmap.Request())
            else:
                results[key] = False
        t0 = time.monotonic()
        while futs and time.monotonic() - t0 < timeout_s:
            rclpy.spin_once(self, timeout_sec=0.05)
            for key in list(futs):
                if futs[key].done():
                    results[key] = futs.pop(key).result() is not None
        for key in futs:
            results[key] = False
        return results


def teleport_and_reset(
    r3_pose: tuple[float, float, float],
    r2_pose: tuple[float, float, float],
    world: str = "main_maps",
) -> dict:
    """Teleport both robots in Gazebo (with GT verification & retry), publish /initialpose, clear costmaps."""
    rclpy.init()
    node = _ResetNode(world)
    try:
        # Wait briefly for discovery of AMCL /initialpose subscribers and GT pose topics
        t0 = time.monotonic()
        while time.monotonic() - t0 < 5.0:
            rclpy.spin_once(node, timeout_sec=0.05)
            if (node._init_pubs["robot3"].get_subscription_count() > 0
                    and node._init_pubs["robot2"].get_subscription_count() > 0
                    and "robot3" in node.gt and "robot2" in node.gt):
                break

        ok3 = ok2 = False
        gt_synced = False
        for attempt in range(3):
            ok3 = gz_set_pose(world, "robot3/turtlebot4", r3_pose[0], r3_pose[1], 0.02, r3_pose[2])
            ok2 = gz_set_pose(world, "robot2/turtlebot4", r2_pose[0], r2_pose[1], 0.02, r2_pose[2])
            node.spin_for(0.8)
            g3, g2 = node.gt.get("robot3"), node.gt.get("robot2")
            if (ok3 and ok2 and g3 and g2
                    and math.hypot(g3[0] - r3_pose[0], g3[1] - r3_pose[1]) < 0.15
                    and math.hypot(g2[0] - r2_pose[0], g2[1] - r2_pose[1]) < 0.15):
                gt_synced = True
                break
            print(f"[sim_reset] teleport verify attempt {attempt+1} mismatch: gt3={g3} want={r3_pose[:2]}, gt2={g2} want={r2_pose[:2]}", flush=True)

        if not gt_synced:
            return {
                "ok": False, "set_pose_r3": ok3, "set_pose_r2": ok2,
                "gt_synced": False, "gt": dict(node.gt), "amcl": dict(node.amcl),
            }

        # Publish initialpose multiple times to ensure AMCL snaps cleanly after teleport
        for _ in range(3):
            node.publish_initialpose("robot3", *r3_pose)
            node.publish_initialpose("robot2", *r2_pose)
            node.spin_for(0.4)

        cleared = node.clear_costmaps(timeout_s=5.0)
        node.spin_for(1.0)
        return {
            "ok": ok3 and ok2 and gt_synced and all(cleared.values()),
            "set_pose_r3": ok3,
            "set_pose_r2": ok2,
            "gt_synced": gt_synced,
            "cleared": {f"{k[0]}_{k[1]}": v for k, v in cleared.items()},
            "gt": dict(node.gt),
            "amcl": dict(node.amcl),
        }
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


def read_gt_poses(world: str = "main_maps", timeout_s: float = 5.0) -> dict:
    rclpy.init()
    node = _ResetNode(world)
    try:
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout_s:
            rclpy.spin_once(node, timeout_sec=0.05)
            if "robot3" in node.gt and "robot2" in node.gt:
                break
        return {"gt": dict(node.gt), "amcl": dict(node.amcl)}
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


def main():
    p = argparse.ArgumentParser(description="Teleport robot3 & robot2, publish initialpose, clear costmaps")
    p.add_argument("--r3", nargs=3, type=float, required=True, metavar=("X", "Y", "YAW"))
    p.add_argument("--r2", nargs=3, type=float, required=True, metavar=("X", "Y", "YAW"))
    p.add_argument("--world", default="main_maps")
    args = p.parse_args()
    res = teleport_and_reset(tuple(args.r3), tuple(args.r2), args.world)
    print(res, flush=True)
    raise SystemExit(0 if res.get("ok") else 1)


if __name__ == "__main__":
    main()
