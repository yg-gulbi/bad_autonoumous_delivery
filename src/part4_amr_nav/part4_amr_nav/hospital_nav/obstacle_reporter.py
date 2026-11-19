"""One-shot obstacle report when Nav2 aborts a mission.

capture(context) is called when Nav2 starts its FIRST recovery (the robot has
just stopped and still faces the blocked path). It subscribes ONCE to the RGB
image, the depth image and the lidar scan, keeps the first message of each and
unsubscribes after at most CAPTURE_TIMEOUT seconds. The result is HELD:
release() publishes it (photo + on_report) only if the mission then aborts,
discard() drops it if the robot got through. The report contains:
  depth_m   - median depth in the centre of the camera view
  lidar_m   - nearest lidar return in front of the robot (+-45 deg, else any)
  bearing_deg, map_x, map_y - where that lidar return is
No camera / depth / scan traffic exists outside a capture.
"""
from __future__ import annotations

import math
import threading
import time

import cv2
import numpy as np
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, qos_profile_sensor_data
from sensor_msgs.msg import CompressedImage, LaserScan
from tf2_msgs.msg import TFMessage

CAPTURE_TIMEOUT = 3.0  # s; report with whatever arrived by then
FRONT_DEG = 45.0       # lidar sector treated as "in front"
# rplidar_link in base_link (x, y, yaw) on TurtleBot4; replaced by tf_static if seen
LIDAR_IN_BASE = (-0.04, 0.0, math.pi / 2)


def _yaw(q):
    return math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))


class ObstacleReporter(Node):
    def __init__(self, robot: str, on_report) -> None:
        super().__init__("hospital_obstacle_reporter", namespace="/" + robot)
        self.robot, self.on_report = robot, on_report
        self.image_pub = self.create_publisher(
            CompressedImage, f"/{robot}/hospital_nav/obstacle_image", 10)
        self._lock = threading.Lock()
        self._active, self._subs, self._got, self._ctx, self._t0 = False, [], {}, None, 0.0
        self._held = None          # (report, CompressedImage | None) waiting for release()
        self._release_ctx = None   # release() came while still capturing
        self._static = {}  # child -> (parent, x, y, yaw) from tf_static (latched, tiny)
        self.create_subscription(TFMessage, f"/{robot}/tf_static", self._on_static,
                                 QoSProfile(depth=50, durability=DurabilityPolicy.TRANSIENT_LOCAL))
        self.create_timer(0.1, self._check)

    # ------------------------------------------------------------------ api
    def capture(self, context: dict) -> bool:
        """Start one capture (result is held); False if one is already running."""
        with self._lock:
            if self._active:
                return False
            self._active, self._got, self._ctx, self._t0 = True, {}, context, time.monotonic()
            self._held, self._release_ctx = None, None
        q = qos_profile_sensor_data  # BEST_EFFORT: matches reliable and best-effort publishers
        self._subs = [
            self.create_subscription(CompressedImage, f"/{self.robot}/oakd/rgb/image_raw/compressed",
                                     lambda m: self._keep("rgb", m), q),
            self.create_subscription(CompressedImage,
                                     f"/{self.robot}/oakd/stereo/image_raw/compressedDepth",
                                     lambda m: self._keep("depth", m), q),
            self.create_subscription(LaserScan, f"/{self.robot}/scan",
                                     lambda m: self._keep("scan", m), q),
        ]
        self.get_logger().info("[OBSTACLE] 사진 1장 / 뎁스 1장 / 스캔 1개 캡처 시작")
        return True

    def busy_or_held(self) -> bool:
        with self._lock:
            return self._active or self._held is not None

    def release(self, extra: dict) -> None:
        """Mission aborted: publish the held capture (or as soon as it finishes)."""
        with self._lock:
            held, self._held = self._held, None
            if held is None and self._active:
                self._release_ctx = extra  # publish when the capture completes
                return
        if held is not None:
            self._publish(held, extra)

    def discard(self) -> None:
        """Robot got through (or a new goal): drop whatever was captured."""
        with self._lock:
            self._held, self._release_ctx = None, None
            if self._active:
                self._release_ctx = "DISCARD"

    # ------------------------------------------------------------ internals
    def _keep(self, key, msg):
        with self._lock:
            if self._active and key not in self._got:
                self._got[key] = msg  # first message only

    def _check(self):
        with self._lock:
            if not self._active:
                return
            done = len(self._got) == 3 or time.monotonic() - self._t0 > CAPTURE_TIMEOUT
            if not done:
                return
            got, ctx, subs = self._got, self._ctx, self._subs
            self._active, self._subs = False, []
        for s in subs:  # unsubscribe right away: no more camera traffic
            self.destroy_subscription(s)
        self._finish(ctx, got)

    def _finish(self, ctx, got):
        report = dict(ctx)
        report["captured"] = sorted(got)
        img = None
        if "rgb" in got:
            img = CompressedImage(header=got["rgb"].header, format=got["rgb"].format,
                                  data=bytes(got["rgb"].data))
            report["image_topic"] = f"/{self.robot}/hospital_nav/obstacle_image"
        report["depth_m"] = self._depth_center(got.get("depth"))
        report.update(self._lidar_nearest(got.get("scan"), ctx.get("robot_pose")))
        with self._lock:
            pending, self._release_ctx = self._release_ctx, None
            if pending is None:
                self._held = (report, img)  # wait for release() / discard()
                self.get_logger().info("[OBSTACLE] 캡처 보관 (ABORTED 되면 전송)")
                return
        if pending != "DISCARD":
            self._publish((report, img), pending)

    def _publish(self, held, extra):
        report, img = held
        report = {**report, **extra}
        if img is not None:
            self.image_pub.publish(img)
        self.on_report(report, None if img is None else bytes(img.data))

    @staticmethod
    def _depth_center(msg):
        """Median depth (m) of the centre 20% of a 16UC1 compressedDepth image."""
        if msg is None:
            return None
        data = bytes(msg.data)
        start = data.find(b"\x89PNG")  # compressedDepth = 12-byte header + PNG
        if start < 0:
            return None
        depth = cv2.imdecode(np.frombuffer(data[start:], np.uint8), cv2.IMREAD_UNCHANGED)
        if depth is None or depth.dtype != np.uint16:
            return None
        h, w = depth.shape[:2]
        roi = depth[int(h * 0.4):int(h * 0.6), int(w * 0.4):int(w * 0.6)]
        valid = roi[roi > 0]
        return round(float(np.median(valid)) / 1000.0, 3) if valid.size else None

    def _on_static(self, msg):
        for t in msg.transforms:
            tr = t.transform
            self._static[t.child_frame_id] = (t.header.frame_id, tr.translation.x,
                                              tr.translation.y, _yaw(tr.rotation))

    def _laser_in_base(self, frame):
        """Compose tf_static from the laser frame up to base_link (2D)."""
        x, y, th, cur = 0.0, 0.0, 0.0, frame
        for _ in range(5):
            if cur == "base_link":
                return x, y, th
            if cur not in self._static:
                break
            parent, px, py, pth = self._static[cur]
            x, y = px + math.cos(pth) * x - math.sin(pth) * y, py + math.sin(pth) * x + math.cos(pth) * y
            th, cur = th + pth, parent
        return LIDAR_IN_BASE

    def _lidar_nearest(self, scan, pose):
        out = {"lidar_m": None, "bearing_deg": None, "map_x": None, "map_y": None}
        if scan is None:
            return out
        lx, ly, lth = self._laser_in_base(scan.header.frame_id)
        best = None  # (in_front, range, base_x, base_y, bearing)
        for i, r in enumerate(scan.ranges):
            if not (math.isfinite(r) and scan.range_min < r < scan.range_max):
                continue
            a = scan.angle_min + i * scan.angle_increment + lth
            bx, by = lx + r * math.cos(a), ly + r * math.sin(a)
            bearing = math.degrees(math.atan2(by, bx))
            key = (abs(bearing) <= FRONT_DEG, -math.hypot(bx, by))
            if best is None or key > best[0]:
                best = (key, bx, by, bearing)
        if best is None:
            return out
        _, bx, by, bearing = best
        out.update(lidar_m=round(math.hypot(bx, by), 3), bearing_deg=round(bearing, 1))
        if pose and pose.get("yaw_deg") is not None:
            yaw = math.radians(pose["yaw_deg"])
            out["map_x"] = round(pose["x"] + math.cos(yaw) * bx - math.sin(yaw) * by, 3)
            out["map_y"] = round(pose["y"] + math.sin(yaw) * bx + math.cos(yaw) * by, 3)
        return out
