#!/usr/bin/env python3
"""Record Gazebo ground-truth robot poses, center-to-center distance, AMCL poses, and RTF at 10 Hz.

Writes a CSV with wall_ts (Unix epoch matching bad_sim.db events.ts), wall_iso, sim_ts, rtf,
robot3 (A) and robot2 (B) ground-truth (x, y, yaw_deg), ground-truth center distance (dist_m),
instantaneous GT speeds (r3_speed_mps, r2_speed_mps), and AMCL poses/distance.
Prints summary (sample count, min GT distance, timestamp/positions at min distance, RTF stats) on exit.
"""
import argparse
import csv
from datetime import datetime
import math
from pathlib import Path
import signal
import sys
import time

import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from rosgraph_msgs.msg import Clock
from tf2_msgs.msg import TFMessage


AMCL_QOS = QoSProfile(
    depth=1,
    reliability=ReliabilityPolicy.RELIABLE,
    durability=DurabilityPolicy.TRANSIENT_LOCAL,
)


def _yaw_deg(q) -> float:
    return math.degrees(
        math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))
    )


class GroundTruthLogger(Node):
    def __init__(self, csv_path: Path, world_frame: str, rate_hz: float, label: str):
        super().__init__("sim_gt_distance_logger")
        self.csv_path = csv_path
        self.world_frame = world_frame
        self.label = label
        self.gt = {"robot3": None, "robot2": None}
        self.amcl = {"robot3": None, "robot2": None}
        self.sim_ts: float | None = None
        self.prev_sample = None  # (wall_ts, sim_ts, r3_xy, r2_xy)
        self.rows = 0
        self.min_dist = float("inf")
        self.min_row = None
        self.rtf_samples: list[float] = []
        self.first_wall: float | None = None
        self.first_sim: float | None = None
        self.last_wall: float | None = None
        self.last_sim: float | None = None

        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = self.csv_path.open("w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._fp)
        self._writer.writerow([
            "wall_ts", "wall_iso", "sim_ts", "rtf",
            "r3_x", "r3_y", "r3_yaw_deg",
            "r2_x", "r2_y", "r2_yaw_deg",
            "dist_m", "r3_speed_mps", "r2_speed_mps",
            "amcl_r3_x", "amcl_r3_y", "amcl_r2_x", "amcl_r2_y", "amcl_dist_m",
            "label",
        ])
        self._fp.flush()

        self.create_subscription(Clock, "/clock", self._on_clock, qos_profile_sensor_data)
        for ns in ("robot3", "robot2"):
            self.create_subscription(
                TFMessage, f"/{ns}/_internal/sim_ground_truth_pose",
                lambda m, r=ns: self._on_gt(r, m), 10,
            )
            self.create_subscription(
                PoseWithCovarianceStamped, f"/{ns}/amcl_pose",
                lambda m, r=ns: self._on_amcl(r, m), AMCL_QOS,
            )
        self.create_timer(1.0 / rate_hz, self._tick)

    def _on_clock(self, msg: Clock):
        self.sim_ts = msg.clock.sec + msg.clock.nanosec * 1e-9

    def _on_gt(self, ns: str, msg: TFMessage):
        target_child = f"{ns}/turtlebot4"
        for tf in msg.transforms:
            if tf.child_frame_id == target_child or tf.header.frame_id == self.world_frame:
                t = tf.transform.translation
                q = tf.transform.rotation
                self.gt[ns] = (float(t.x), float(t.y), _yaw_deg(q))
                return

    def _on_amcl(self, ns: str, msg: PoseWithCovarianceStamped):
        p = msg.pose.pose.position
        self.amcl[ns] = (float(p.x), float(p.y))

    def _tick(self):
        r3, r2 = self.gt["robot3"], self.gt["robot2"]
        if r3 is None or r2 is None:
            return
        wall_ts = time.time()
        wall_iso = datetime.fromtimestamp(wall_ts).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        sim_ts = self.sim_ts
        dist_m = math.hypot(r3[0] - r2[0], r3[1] - r2[1])

        rtf = None
        r3_v = r2_v = None
        if self.prev_sample is not None:
            pw, ps, pr3, pr2 = self.prev_sample
            dw = wall_ts - pw
            if dw > 1e-6:
                if sim_ts is not None and ps is not None:
                    rtf = (sim_ts - ps) / dw
                    if rtf >= 0:
                        self.rtf_samples.append(rtf)
                dt_motion = (sim_ts - ps) if (sim_ts is not None and ps is not None and sim_ts > ps) else dw
                r3_v = math.hypot(r3[0] - pr3[0], r3[1] - pr3[1]) / dt_motion
                r2_v = math.hypot(r2[0] - pr2[0], r2[1] - pr2[1]) / dt_motion
        self.prev_sample = (wall_ts, sim_ts, (r3[0], r3[1]), (r2[0], r2[1]))

        if self.first_wall is None and sim_ts is not None:
            self.first_wall, self.first_sim = wall_ts, sim_ts
        if sim_ts is not None:
            self.last_wall, self.last_sim = wall_ts, sim_ts

        a3, a2 = self.amcl["robot3"], self.amcl["robot2"]
        amcl_d = math.hypot(a3[0] - a2[0], a3[1] - a2[1]) if (a3 and a2) else None

        row = [
            f"{wall_ts:.3f}",
            wall_iso,
            "" if sim_ts is None else f"{sim_ts:.3f}",
            "" if rtf is None else f"{rtf:.3f}",
            f"{r3[0]:.4f}", f"{r3[1]:.4f}", f"{r3[2]:.1f}",
            f"{r2[0]:.4f}", f"{r2[1]:.4f}", f"{r2[2]:.1f}",
            f"{dist_m:.4f}",
            "" if r3_v is None else f"{r3_v:.3f}",
            "" if r2_v is None else f"{r2_v:.3f}",
            "" if a3 is None else f"{a3[0]:.4f}",
            "" if a3 is None else f"{a3[1]:.4f}",
            "" if a2 is None else f"{a2[0]:.4f}",
            "" if a2 is None else f"{a2[1]:.4f}",
            "" if amcl_d is None else f"{amcl_d:.4f}",
            self.label,
        ]
        self._writer.writerow(row)
        self.rows += 1
        if self.rows % 10 == 0:
            self._fp.flush()
        if dist_m < self.min_dist:
            self.min_dist = dist_m
            self.min_row = (wall_iso, sim_ts, r3, r2, dist_m)

    def close(self):
        self._fp.flush()
        self._fp.close()
        avg_rtf = None
        if (
            self.first_wall is not None
            and self.last_wall is not None
            and self.last_wall > self.first_wall
        ):
            avg_rtf = (self.last_sim - self.first_sim) / (self.last_wall - self.first_wall)
        print(f"\n[gt_distance_logger] saved {self.rows} rows -> {self.csv_path}")
        if self.min_row:
            iso, sim_ts, r3, r2, d = self.min_row
            print(
                f"  MIN GT DISTANCE: {d:.4f} m at {iso} (sim={sim_ts:.2f}s) "
                f"robot3=({r3[0]:.3f},{r3[1]:.3f}) robot2=({r2[0]:.3f},{r2[1]:.3f})"
            )
        if avg_rtf is not None:
            print(f"  AVG RTF: {avg_rtf:.3f} (n={len(self.rtf_samples)})")


def main():
    parser = argparse.ArgumentParser(description="10 Hz Gazebo ground-truth distance & RTF logger")
    parser.add_argument("--out", required=True, help="Output CSV path")
    parser.add_argument("--world", default="main_maps", help="World frame id in sim_ground_truth_pose")
    parser.add_argument("--rate", type=float, default=10.0, help="Logging rate Hz (default 10)")
    parser.add_argument("--label", default="", help="Optional scenario label column")
    parser.add_argument("--duration", type=float, default=0.0, help="Optional auto-stop seconds (0=until SIGINT)")
    args = parser.parse_args()

    rclpy.init()
    node = GroundTruthLogger(Path(args.out), args.world, args.rate, args.label)
    stop = False

    def _sig(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    t0 = time.monotonic()
    try:
        while rclpy.ok() and not stop:
            rclpy.spin_once(node, timeout_sec=0.05)
            if args.duration > 0 and time.monotonic() - t0 >= args.duration:
                break
    finally:
        node.close()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
