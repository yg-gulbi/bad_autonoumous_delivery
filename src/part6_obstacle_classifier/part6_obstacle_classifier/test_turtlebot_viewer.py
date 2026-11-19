"""실제 TurtleBot4에서 실시간으로 Compressed RGB(YOLO 마스킹)와 Depth 카메라 화면을 띄우는 테스트 뷰어."""

import threading

import cv2
from cv_bridge import CvBridge
from message_filters import ApproximateTimeSynchronizer, Subscriber
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CompressedImage, Image
from ultralytics import YOLO

from .obstacle_classifier import extract_valid_depth


def apply_yolo_mask(
    frame: np.ndarray,
    result,
    depth_image: np.ndarray | None = None,
    min_depth: float = 0.2,
    max_depth: float = 5.0,
    alpha: float = 0.45,
) -> tuple[np.ndarray, list[tuple[int, int]]]:
    """YOLO 추론 결과의 마스크·박스·거리(m)를 RGB 이미지에 표시하고 중심 픽셀 목록을 반환."""
    masked = frame.copy()
    centers: list[tuple[int, int]] = []
    boxes = result.boxes
    masks = result.masks

    if boxes is None or len(boxes) == 0:
        return masked, centers

    palette = [
        (0, 255, 0),
        (0, 165, 255),
        (255, 0, 255),
        (255, 255, 0),
        (0, 0, 255),
        (255, 128, 0),
    ]

    h, w = masked.shape[:2]
    for idx, box in enumerate(boxes):
        cls_id = int(box.cls[0])
        label = str(result.names[cls_id])
        conf = float(box.conf[0])
        color = palette[cls_id % len(palette)]
        x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
        u, v = (x1 + x2) // 2, (y1 + y2) // 2
        centers.append((u, v))

        if masks is not None and idx < len(masks.data):
            raw_mask = masks.data[idx].cpu().numpy()
            resized = cv2.resize(raw_mask, (w, h), interpolation=cv2.INTER_NEAREST) > 0.5
            overlay = masked.copy()
            overlay[resized] = color
            masked = cv2.addWeighted(overlay, alpha, masked, 1.0 - alpha, 0)
        else:
            overlay = masked.copy()
            cv2.rectangle(overlay, (x1, y1), (x2, y2), color, -1)
            masked = cv2.addWeighted(overlay, alpha * 0.5, masked, 1.0 - alpha * 0.5, 0)

        # 객체 중심점의 Depth(m)가 유효하면 텍스트에 함께 표시
        z = (
            extract_valid_depth(depth_image, u, v, min_depth=min_depth, max_depth=max_depth)
            if depth_image is not None
            else None
        )
        text = f'{label} {conf:.2f}'
        if z is not None:
            text += f' {z:.2f}m'

        cv2.rectangle(masked, (x1, y1), (x2, y2), color, 2)
        cv2.circle(masked, (u, v), 4, color, -1)
        cv2.putText(
            masked,
            text,
            (x1, max(18, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            color,
            2,
        )
    return masked, centers


def colorize_depth(
    depth_image: np.ndarray,
    centers: list[tuple[int, int]] | None = None,
    max_depth_mm: float = 5000.0,
) -> np.ndarray:
    """Depth 이미지를 0~max_depth_mm 범위로 정규화해 COLORMAP_JET 컬러 영상으로 변환."""
    depth_float = depth_image.astype(np.float32)
    # 실수형(m 단위)인 경우 mm 단위로 맞춰 동일한 스케일로 시각화
    if not np.issubdtype(depth_image.dtype, np.integer):
        depth_float = depth_float * 1000.0
    depth_clipped = np.clip(np.nan_to_num(depth_float, nan=0.0), 0.0, max_depth_mm)
    normalized = (depth_clipped / max_depth_mm * 255.0).astype(np.uint8)
    colored = cv2.applyColorMap(normalized, cv2.COLORMAP_JET)

    for u, v in centers or []:
        if 0 <= u < colored.shape[1] and 0 <= v < colored.shape[0]:
            cv2.circle(colored, (u, v), 5, (255, 255, 255), -1)
    return colored


class TurtleBotRealtimeViewer(Node):
    """실제 TurtleBot4의 RGB(YOLO 마스킹) + Depth 카메라 영상을 실시간으로 확인하는 뷰어 노드."""

    def __init__(self):
        # 기본 네임스페이스를 '/robot3'으로 두고, 실행 시 -r __ns:=/robot2 로 변경 가능
        super().__init__('test_turtlebot_viewer', namespace='/robot3')

        self.declare_parameter('rgb_topic', 'oakd/rgb/image_raw/compressed')
        self.declare_parameter('depth_topic', 'oakd/stereo/image_raw')
        self.declare_parameter('model_path', 'yolo11n-seg.pt')
        self.declare_parameter('conf_threshold', 0.25)
        self.declare_parameter('min_depth', 0.2)
        self.declare_parameter('max_depth', 5.0)
        self.declare_parameter('sync_slop', 0.1)

        rgb_topic = str(self.get_parameter('rgb_topic').value)
        depth_topic = str(self.get_parameter('depth_topic').value)
        model_path = str(self.get_parameter('model_path').value)
        self.conf_threshold = float(self.get_parameter('conf_threshold').value)
        self.min_depth = float(self.get_parameter('min_depth').value)
        self.max_depth = float(self.get_parameter('max_depth').value)
        sync_slop = float(self.get_parameter('sync_slop').value)

        self.model = YOLO(model_path)
        self.bridge = CvBridge()
        self.lock = threading.Lock()
        self.latest_rgb = None
        self.latest_depth = None
        self.logged_first_frame = False

        # RGB(CompressedImage)와 Depth(Image)를 타임스탬프 근사 동기화해 수신
        self.rgb_sub = Subscriber(
            self, CompressedImage, rgb_topic, qos_profile=qos_profile_sensor_data
        )
        self.depth_sub = Subscriber(
            self, Image, depth_topic, qos_profile=qos_profile_sensor_data
        )
        self.ts = ApproximateTimeSynchronizer(
            [self.rgb_sub, self.depth_sub], queue_size=10, slop=sync_slop
        )
        self.ts.registerCallback(self.synced_callback)
        self.get_logger().info(
            f'TurtleBot viewer started in {self.get_namespace()} '
            f'(RGB: {rgb_topic}, Depth: {depth_topic}, Model: {model_path})'
        )

    def synced_callback(self, rgb_msg: CompressedImage, depth_msg: Image):
        """동기화된 RGB와 Depth를 디코딩해 최신 프레임만 저장."""
        np_arr = np.frombuffer(rgb_msg.data, np.uint8)
        rgb_frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        depth_frame = self.bridge.imgmsg_to_cv2(depth_msg, desired_encoding='passthrough')
        if rgb_frame is None or depth_frame is None:
            return

        if not self.logged_first_frame:
            self.get_logger().info(
                f'First frame received: rgb={rgb_frame.shape}, '
                f'depth={depth_frame.shape} ({depth_frame.dtype})'
            )
            self.logged_first_frame = True

        with self.lock:
            self.latest_rgb = rgb_frame
            self.latest_depth = depth_frame

    def pop_latest_frames(self) -> tuple[np.ndarray | None, np.ndarray | None]:
        """화면에 표시할 최신 RGB·Depth 1쌍을 꺼내고 비워 중복 추론을 방지."""
        with self.lock:
            rgb, depth = self.latest_rgb, self.latest_depth
            self.latest_rgb = None
            self.latest_depth = None
        return rgb, depth


def main(args=None):
    rclpy.init(args=args)
    node = TurtleBotRealtimeViewer()

    rgb_win = 'TurtleBot RGB (YOLO Mask)'
    depth_win = 'TurtleBot Depth (ColorMap)'
    cv2.namedWindow(rgb_win, cv2.WINDOW_NORMAL)
    cv2.namedWindow(depth_win, cv2.WINDOW_NORMAL)

    try:
        while rclpy.ok():
            # ROS 콜백 처리와 OpenCV GUI 루프를 메인 스레드에서 함께 회전
            rclpy.spin_once(node, timeout_sec=0.01)
            rgb, depth = node.pop_latest_frames()
            if rgb is not None and depth is not None:
                results = node.model(rgb, conf=node.conf_threshold, verbose=False)
                if results:
                    masked_rgb, centers = apply_yolo_mask(
                        rgb,
                        results[0],
                        depth_image=depth,
                        min_depth=node.min_depth,
                        max_depth=node.max_depth,
                    )
                else:
                    masked_rgb, centers = rgb, []

                colored_depth = colorize_depth(
                    depth, centers=centers, max_depth_mm=node.max_depth * 1000.0
                )
                cv2.imshow(rgb_win, masked_rgb)
                cv2.imshow(depth_win, colored_depth)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord('q'), 27):  # q 또는 ESC로 종료
                break
    except KeyboardInterrupt:
        pass
    finally:
        cv2.destroyAllWindows()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
