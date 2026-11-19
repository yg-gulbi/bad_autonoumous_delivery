"""트리거 이벤트 수신 시에만 RGB(Compressed)+Depth 1쌍을 처리해 JSON 및 시각화 이미지를 발행하는 ROS 2 노드."""

import json
import threading

import cv2
from cv_bridge import CvBridge
from geometry_msgs.msg import PointStamped
from message_filters import ApproximateTimeSynchronizer, Subscriber
import numpy as np
import rclpy
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo, CompressedImage, Image
from std_msgs.msg import Empty, String
from tf2_ros import Buffer, TransformListener
import tf2_geometry_msgs  # noqa: F401  # PointStamped에 대한 tf2_buffer.transform 지원 등록

from .obstacle_classifier import build_error, draw_detections, load_classifier, process_frame


class ObstacleClassifierNode(Node):
    """이벤트 기반 장애물 탐지 및 map 좌표 변환 ROS 2 노드."""

    def __init__(self):
        super().__init__('obstacle_classifier')

        # 토픽명·모델·깊이 유효 범위·동기화 허용 오차 등을 ROS 2 파라미터로 선언 (하드코딩 방지)
        self.declare_parameter('rgb_topic', 'oakd/rgb/image_raw/compressed')
        self.declare_parameter('depth_topic', 'oakd/stereo/image_raw')
        self.declare_parameter('camera_info_topic', 'oakd/rgb/camera_info')
        self.declare_parameter('trigger_topic', 'obstacle/inspect_image')
        self.declare_parameter('output_topic', 'obstacle_classifier/result')
        self.declare_parameter('image_output_topic', 'obstacle_classifier/image/compressed')
        self.declare_parameter('target_frame', 'map')
        self.declare_parameter(
            'method',
            'part6_obstacle_classifier.obstacle_classifier:Yolo11Classifier',
        )
        self.declare_parameter('model_path', 'yolo11n.pt')
        self.declare_parameter('conf_threshold', 0.25)
        self.declare_parameter('min_depth', 0.2)
        self.declare_parameter('max_depth', 5.0)
        self.declare_parameter('sync_slop', 0.1)
        self.declare_parameter('capture_timeout_sec', 3.0)

        method = str(self.get_parameter('method').value)
        model_path = str(self.get_parameter('model_path').value)
        conf_threshold = float(self.get_parameter('conf_threshold').value)
        self.min_depth = float(self.get_parameter('min_depth').value)
        self.max_depth = float(self.get_parameter('max_depth').value)
        self.target_frame = str(self.get_parameter('target_frame').value)
        sync_slop = float(self.get_parameter('sync_slop').value)
        self.capture_timeout_sec = float(self.get_parameter('capture_timeout_sec').value)

        # 선택된 분류기 어댑터 로드 (기본값은 YOLO11n)
        if method.endswith(':Yolo11Classifier'):
            self.classifier = load_classifier(
                method, model_path=model_path, conf_threshold=conf_threshold
            )
        else:
            self.classifier = load_classifier(method)

        self.bridge = CvBridge()
        self.lock = threading.Lock()
        # 평소에는 이미지 처리를 하지 않고 대기 (이벤트 수신 시에만 True로 전환)
        self.pending_capture = False
        self.timeout_timer = None
        self.K = None

        # /tf, /tf_static을 백그라운드 버퍼에 유지해 depth stamp 시점 변환을 지원
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)

        # 결과 JSON 및 바운딩 박스 시각화 이미지 퍼블리셔 생성
        self.publisher = self.create_publisher(
            String, str(self.get_parameter('output_topic').value), 10
        )
        self.image_publisher = self.create_publisher(
            CompressedImage, str(self.get_parameter('image_output_topic').value), 10
        )

        # 카메라 내부 파라미터(fx, fy, cx, cy) 수신용 구독자
        self.create_subscription(
            CameraInfo,
            str(self.get_parameter('camera_info_topic').value),
            self.camera_info_callback,
            qos_profile_sensor_data,
        )

        # RGB(CompressedImage)와 Depth(Image)의 타임스탬프를 근사 동기화해 수신
        self.rgb_sub = Subscriber(
            self, CompressedImage, str(self.get_parameter('rgb_topic').value),
            qos_profile=qos_profile_sensor_data,
        )
        self.depth_sub = Subscriber(
            self, Image, str(self.get_parameter('depth_topic').value),
            qos_profile=qos_profile_sensor_data,
        )
        self.ts = ApproximateTimeSynchronizer(
            [self.rgb_sub, self.depth_sub], queue_size=10, slop=sync_slop
        )
        self.ts.registerCallback(self.synced_callback)

        # 외부 검사 요청 트리거 구독자 (이벤트 발생 시 1회 분석 시작)
        self.create_subscription(
            Empty,
            str(self.get_parameter('trigger_topic').value),
            self.trigger_callback,
            10,
        )

    def camera_info_callback(self, msg: CameraInfo):
        """카메라 내부 파라미터 행렬 K(3x3)를 최초 1회 저장."""
        with self.lock:
            if self.K is None:
                self.K = np.array(msg.k, dtype=np.float64).reshape(3, 3)

    def _cancel_timeout_timer(self):
        if self.timeout_timer is not None:
            self.timeout_timer.cancel()
            self.timeout_timer = None

    def trigger_callback(self, _msg: Empty):
        """이벤트 수신 시 1회 이미지 수신 활성화 및 타임아웃 타이머 시작."""
        with self.lock:
            self.pending_capture = True
            self._cancel_timeout_timer()
            self.timeout_timer = self.create_timer(
                self.capture_timeout_sec, self._on_capture_timeout
            )

    def _on_capture_timeout(self):
        """지정 시간 내에 RGB·Depth·CameraInfo가 준비되지 않으면 오류 JSON 발행."""
        with self.lock:
            if not self.pending_capture:
                self._cancel_timeout_timer()
                return
            self.pending_capture = False
            self._cancel_timeout_timer()
        self.publish_result(
            build_error('missing_data', 'Timed out waiting for RGB/Depth or CameraInfo')
        )

    def _transform_to_target(self, x: float, y: float, z: float, frame_id: str, stamp):
        """depth_msg.header.stamp 시점의 TF를 조회해 카메라 3D 좌표를 target_frame(map)으로 변환."""
        pt = PointStamped()
        pt.header.frame_id = frame_id
        pt.header.stamp = stamp
        pt.point.x = float(x)
        pt.point.y = float(y)
        pt.point.z = float(z)
        try:
            transformed = self.tf_buffer.transform(
                pt, self.target_frame, timeout=Duration(seconds=0.5)
            )
            return {
                'x': float(transformed.point.x),
                'y': float(transformed.point.y),
                'z': float(transformed.point.z),
            }
        except Exception as exc:
            self.get_logger().warn(f'TF transform to {self.target_frame} failed: {exc}')
            return None

    def synced_callback(self, rgb_msg: CompressedImage, depth_msg: Image):
        """동기화된 RGB+Depth 수신 콜백. pending_capture가 켜져 있을 때만 1회 추론 및 발행을 수행."""
        # 대기 상태이거나 CameraInfo 미수신 시 즉시 반환해 리소스 낭비 방지
        with self.lock:
            if not self.pending_capture or self.K is None:
                return
            self.pending_capture = False
            self._cancel_timeout_timer()
            K = self.K.copy()

        header = {
            'stamp': {
                'sec': rgb_msg.header.stamp.sec,
                'nanosec': rgb_msg.header.stamp.nanosec,
            },
            'frame_id': rgb_msg.header.frame_id,
        }

        try:
            # 1) CompressedImage(JPEG 등) -> OpenCV BGR 이미지로 디코딩
            np_arr = np.frombuffer(rgb_msg.data, np.uint8)
            rgb_frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            # 2) Depth Image -> 원본 인코딩 유지(16UC1/32FC1)한 2D 배열로 변환
            depth_frame = self.bridge.imgmsg_to_cv2(depth_msg, desired_encoding='passthrough')
            camera_frame = depth_msg.header.frame_id or rgb_msg.header.frame_id

            # 3) 객체 탐지 및 유효 Depth 기반 map 좌표 변환 실행
            result = process_frame(
                rgb_frame,
                self.classifier,
                header,
                depth_image=depth_frame,
                K=K,
                transform_fn=lambda x, y, z: self._transform_to_target(
                    x, y, z, camera_frame, depth_msg.header.stamp
                ),
                min_depth=self.min_depth,
                max_depth=self.max_depth,
            )
            # 4) 바운딩 박스/좌표가 그려진 시각화 이미지 발행
            self.publish_annotated_image(rgb_frame, result['detections'], rgb_msg.header)
        except Exception as exc:
            self.get_logger().error(f'Classification failed: {exc}')
            result = build_error('processing_error', str(exc), header)

        # 5) 최종 JSON 결과 발행
        self.publish_result(result)

    def publish_annotated_image(self, frame: np.ndarray, detections: list[dict], header):
        """탐지 결과가 그려진 이미지를 JPEG로 압축해 CompressedImage 토픽으로 발행."""
        annotated = draw_detections(frame, detections)
        ok, encoded = cv2.imencode('.jpg', annotated)
        if not ok:
            return
        msg = CompressedImage()
        msg.header = header
        msg.format = 'jpeg'
        msg.data = encoded.tobytes()
        self.image_publisher.publish(msg)

    def publish_result(self, result: dict):
        """탐지 및 좌표 계산 결과를 JSON 문자열로 발행."""
        msg = String()
        msg.data = json.dumps(result, allow_nan=False)
        self.publisher.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = ObstacleClassifierNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
