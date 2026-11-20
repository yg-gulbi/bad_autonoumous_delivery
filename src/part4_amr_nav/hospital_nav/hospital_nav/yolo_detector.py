"""Real-time YOLO obstacle detection on the control PC while the robot drives.

While active (robot MOVING) it subscribes to the OAK-D RGB *compressed* stream
(512x288 JPEG) and runs the YOLO model on the newest frame every `period` s.
Depth is NOT subscribed then. On a detection (the robot keeps driving; Nav2
decides whether to go around) it does one short burst
(<= BURST_S): RGB + compressedDepth together, picks the RGB/depth pair with
the closest stamps (<= SYNC_SLOP, both stamped by the robot clock), runs YOLO
once more on that RGB so the box matches the depth, measures the box distance
and calls on_report(report, jpeg) once. Then depth is unsubscribed again.

Same depth decoding / box-depth method as the mini project
(amr_ab_controller_patrol._decode_compressed_depth, approach_core.robust_depth_m).
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque

import cv2
import numpy as np
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from rclpy.time import Time
from sensor_msgs.msg import CameraInfo, CompressedImage

BURST_S = 1.0          # s of RGB+depth after a detection to find a matching pair
SYNC_SLOP = 0.2        # s; max RGB/depth stamp gap (mini project sync_slop)
CAMERA_IN_BASE = (0.06, 0.0)  # OAK-D RGB optical centre in base_link (x fwd, y left), approx.


def decode_compressed_depth(msg):
    """compressedDepth ('16UC1; compressedDepth png' = 12-byte header + PNG) -> uint16 mm."""
    data = bytes(msg.data)
    start = data.find(b"\x89PNG")
    if start < 0:
        return None
    depth = cv2.imdecode(np.frombuffer(data[start:], np.uint8), cv2.IMREAD_UNCHANGED)
    return depth if depth is not None and depth.ndim == 2 and depth.dtype == np.uint16 else None


def robust_depth_m(depth, bbox):
    """Median depth (m) of the inner 60 % of the box; ignores 0 and out-of-range pixels.
    (A tiny centre window often had too few valid stereo pixels on small boxes.)"""
    h, w = depth.shape[:2]
    x1, y1, x2, y2 = bbox
    mx, my = (x2 - x1) * 0.2, (y2 - y1) * 0.2
    c1, c2 = max(0, int(x1 + mx)), min(w, int(round(x2 - mx)) + 1)
    r1, r2 = max(0, int(y1 + my)), min(h, int(round(y2 - my)) + 1)
    if c1 >= c2 or r1 >= r2:
        return None
    roi = depth[r1:r2, c1:c2] / 1000.0
    valid = roi[(roi > 0.30) & (roi < 5.0)]
    return round(float(np.median(valid)), 3) if valid.size >= 6 else None


def stamp_s(msg):
    return Time.from_msg(msg.header.stamp).nanoseconds / 1e9


class YoloDetector(Node):
    def __init__(self, robot, model_path, on_report, *, conf=0.5, period=2.0,
                 classes=None, cooldown=10.0, max_dist=3.0):
        super().__init__("hospital_yolo_detector", namespace="/" + robot)
        from ultralytics import YOLO  # heavy import only when the feature is used
        self.robot, self.on_report = robot, on_report
        self.conf, self.period, self.cooldown, self.max_dist = conf, period, cooldown, max_dist
        self.classes = {c.strip().lower() for c in classes} if classes else None
        self.model = YOLO(model_path)
        self.model.predict(np.zeros((288, 512, 3), np.uint8), verbose=False)  # warm up (GPU)
        self.names = self.model.names
        self.get_logger().info(f"[YOLO] 모델 {model_path} 로드, 클래스 {len(self.names)}개, "
                               f"대상={sorted(self.classes) if self.classes else '전부'}")
        self._lock = threading.Lock()
        self._want = False                  # robot is MOVING
        self._rgb_sub = self._depth_sub = None
        self._rgb_buf, self._depth_buf = deque(maxlen=10), deque(maxlen=10)
        self._next_infer, self._burst_until, self._last_report = 0.0, 0.0, -1e9
        self._burst_ctx, self._ctx = None, {}
        self._fx = self._cx = None
        self.create_subscription(CameraInfo, f"/{robot}/oakd/rgb/camera_info",
                                 self._on_info, qos_profile_sensor_data)
        self.create_timer(0.1, self._tick, callback_group=MutuallyExclusiveCallbackGroup())

    # ------------------------------------------------------------------ api
    def set_active(self, active: bool, context: dict | None = None) -> None:
        """Called every mission tick: detect only while the robot is MOVING."""
        with self._lock:
            self._want = active
            self._ctx = context or {}

    # ------------------------------------------------------------ internals
    def _on_info(self, msg):
        if self._fx is None and msg.k[0] > 0:
            self._fx, self._cx = msg.k[0], msg.k[2]
            self.get_logger().info(f"[YOLO] camera_info {msg.width}x{msg.height} fx={self._fx:.1f}")

    def _keep(self, buf, msg):
        with self._lock:
            buf.append(msg)

    def _tick(self):
        now = time.monotonic()
        with self._lock:
            want = self._want
        # RGB subscription only while moving (created/destroyed per mission, not per frame)
        if want and self._rgb_sub is None:
            self._rgb_sub = self.create_subscription(
                CompressedImage, f"/{self.robot}/oakd/rgb/image_raw/compressed",
                lambda m: self._keep(self._rgb_buf, m), qos_profile_sensor_data)
        elif not want and self._rgb_sub is not None and self._burst_ctx is None:
            self.destroy_subscription(self._rgb_sub)
            self._rgb_sub = None
            with self._lock:
                self._rgb_buf.clear()
        if self._burst_ctx is not None:
            self._burst_step(now)
            return
        if not want or now < self._next_infer or now - self._last_report < self.cooldown:
            return
        with self._lock:
            frame = self._rgb_buf[-1] if self._rgb_buf else None
        if frame is None:
            return
        self._next_infer = now + self.period
        det = self._detect(frame)
        if det is not None:  # report first (robot keeps going), via an RGB+depth burst
            self._burst_ctx = {"first": det, "t_end": now + BURST_S, "ctx": dict(self._ctx)}
            with self._lock:
                self._depth_buf.clear()
            self._depth_sub = self.create_subscription(
                CompressedImage, f"/{self.robot}/oakd/stereo/image_raw/compressedDepth",
                lambda m: self._keep(self._depth_buf, m), qos_profile_sensor_data)
            self.get_logger().info(f"[YOLO] 감지 {det['class']} {det['conf']:.2f} → RGB+뎁스 1초 수집")

    def _detect(self, rgb_msg):
        img = cv2.imdecode(np.frombuffer(bytes(rgb_msg.data), np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return None
        res = self.model.predict(img, conf=self.conf, verbose=False)[0]
        best = None
        for box in res.boxes:
            name = str(self.names[int(box.cls[0])])
            if self.classes and name.lower() not in self.classes:
                continue
            c = float(box.conf[0])
            if best is None or c > best["conf"]:
                best = {"class": name, "conf": round(c, 3),
                        "bbox": [round(float(v), 1) for v in box.xyxy[0].tolist()],
                        "img_shape": img.shape[:2], "msg": rgb_msg}
        return best

    def _burst_step(self, now):
        b = self._burst_ctx
        with self._lock:
            rgbs, depths = list(self._rgb_buf), list(self._depth_buf)
        pair = None
        for r in reversed(rgbs):  # newest RGB with a depth within SYNC_SLOP
            if not depths:
                break
            d = min(depths, key=lambda m: abs(stamp_s(m) - stamp_s(r)))
            if abs(stamp_s(d) - stamp_s(r)) <= SYNC_SLOP:
                pair = (r, d)
                break
        if pair is None and now < b["t_end"]:
            return  # keep collecting
        self.destroy_subscription(self._depth_sub)
        self._depth_sub, self._burst_ctx = None, None
        det, depth_m, gap = b["first"], None, None
        if pair is not None:
            again = self._detect(pair[0])  # box on the RGB that matches the depth
            if again is not None:
                det = again
            depth = decode_compressed_depth(pair[1])
            if depth is not None and det["msg"] is pair[0]:
                h, w = det["img_shape"]
                sx, sy = depth.shape[1] / w, depth.shape[0] / h  # depth may differ in size
                x1, y1, x2, y2 = det["bbox"]
                depth_m = robust_depth_m(depth, (x1 * sx, y1 * sy, x2 * sx, y2 * sy))
            gap = round(abs(stamp_s(pair[0]) - stamp_s(pair[1])), 3)
        if depth_m is not None and depth_m > self.max_dist:
            self.get_logger().info(f"[YOLO] {det['class']} {depth_m:.2f} m: 멀어서 보고 안 함")
            return
        self._last_report = now
        self.on_report(self._report(det, depth_m, gap, b["ctx"]), self._boxed_jpeg(det))

    @staticmethod
    def _boxed_jpeg(det):
        """The detection frame with only the bounding box drawn (numbers go in the JSON)."""
        raw = bytes(det["msg"].data)
        img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return raw
        x1, y1, x2, y2 = map(int, det["bbox"])
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), 2)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
        return buf.tobytes() if ok else raw

    def _report(self, det, depth_m, gap, ctx):
        x1, _, x2, _ = det["bbox"]
        bearing = None
        if self._fx:  # left positive, like the lidar bearing
            bearing = round(-math.degrees(math.atan2((x1 + x2) / 2 - self._cx, self._fx)), 1)
        rep = {**ctx, "source": "yolo", "class": det["class"], "conf": det["conf"],
               "bbox": det["bbox"], "image_stamp": round(stamp_s(det["msg"]), 3),
               "depth_m": depth_m, "rgb_depth_gap_s": gap, "bearing_deg": bearing,
               "map_x": None, "map_y": None}
        pose = ctx.get("robot_pose")
        if depth_m is not None and bearing is not None and pose and pose.get("yaw_deg") is not None:
            a, yaw = math.radians(bearing), math.radians(pose["yaw_deg"])
            bx = CAMERA_IN_BASE[0] + depth_m * math.cos(a)
            by = CAMERA_IN_BASE[1] + depth_m * math.sin(a)
            rep["map_x"] = round(pose["x"] + math.cos(yaw) * bx - math.sin(yaw) * by, 3)
            rep["map_y"] = round(pose["y"] + math.sin(yaw) * bx + math.cos(yaw) * by, 3)
        return rep
