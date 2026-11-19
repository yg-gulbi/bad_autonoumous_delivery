"""[1단계] 이미지 이벤트 1장을 토픽으로 받아 마스킹 이미지와 객체 종류 JSON(pose 없음)을 발행하는 노드."""

from datetime import datetime
import json
from pathlib import Path

import cv2
from cv_bridge import CvBridge
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import CompressedImage, Image
from std_msgs.msg import String

from .obstacle_classifier import (
    build_error,
    draw_detections,
    process_frame,
    resolve_default_model_path,
)
from .ros_utils import (
    decode_compressed_bgr,
    decode_raw_bgr,
    header_to_dict,
    load_node_classifier,
)

DEFAULT_TEST_SAVE_DIR = '/home/mu-02/rokey_ws/part6_assets/topic_captures'


class ObstacleClassifierNode(Node):
    """1번 노드: 단일 이미지 이벤트 수신 -> 객체 종류 JSON + 마스킹 이미지 발행 (+ 테스트용 저장)."""

    def __init__(self):
        super().__init__('obstacle_classifier')

        self.declare_parameter('input_topic', 'hospital_nav/obstacle_image')
        self.declare_parameter('use_compressed', True)
        self.declare_parameter('output_topic', 'obstacle_classifier/result')
        self.declare_parameter('image_output_topic', 'obstacle_classifier/image/compressed')
        self.declare_parameter(
            'method',
            'part6_obstacle_classifier.obstacle_classifier:Yolo11Classifier',
        )
        self.declare_parameter('model_path', resolve_default_model_path())
        self.declare_parameter('conf_threshold', 0.25)
        # 테스트용 저장 경로 (비우면 '' 디스크 저장 안 함) 및 마지막 토픽 1건 유지 여부
        self.declare_parameter('save_dir', DEFAULT_TEST_SAVE_DIR)
        self.declare_parameter('transient_local', True)

        input_topic = str(self.get_parameter('input_topic').value)
        self.use_compressed = bool(self.get_parameter('use_compressed').value)
        method = str(self.get_parameter('method').value)
        model_path = str(self.get_parameter('model_path').value)
        conf_threshold = float(self.get_parameter('conf_threshold').value)
        save_dir_str = str(self.get_parameter('save_dir').value).strip()
        self.save_dir = Path(save_dir_str) if save_dir_str else None
        if self.save_dir is not None:
            self.save_dir.mkdir(parents=True, exist_ok=True)
        self.save_seq = 0

        self.classifier = load_node_classifier(method, model_path, conf_threshold)
        self.bridge = CvBridge()

        # transient_local=True 시 노드가 켜져 있는 동안 마지막 발행 1건이 토픽에 보관되어 뒤늦은 echo/rqt도 수신 가능
        use_tl = bool(self.get_parameter('transient_local').value)
        pub_qos = (
            QoSProfile(
                depth=1,
                reliability=ReliabilityPolicy.RELIABLE,
                durability=DurabilityPolicy.TRANSIENT_LOCAL,
            )
            if use_tl
            else 10
        )

        self.result_pub = self.create_publisher(
            String, str(self.get_parameter('output_topic').value), pub_qos
        )
        self.image_pub = self.create_publisher(
            CompressedImage, str(self.get_parameter('image_output_topic').value), pub_qos
        )

        if self.use_compressed:
            self.create_subscription(
                CompressedImage, input_topic, self.compressed_image_callback, qos_profile_sensor_data
            )
        else:
            self.create_subscription(
                Image, input_topic, self.raw_image_callback, qos_profile_sensor_data
            )

        self.get_logger().info(
            f'[1] ImageEvent node ready (input={input_topic}, compressed={self.use_compressed}, '
            f'model={model_path}, save_dir={self.save_dir}, transient_local={use_tl})'
        )

    def compressed_image_callback(self, msg: CompressedImage):
        self._decode_and_handle(lambda: decode_compressed_bgr(msg), msg.header)

    def raw_image_callback(self, msg: Image):
        self._decode_and_handle(lambda: decode_raw_bgr(msg, self.bridge), msg.header)

    def _save_capture(self, raw_frame: np.ndarray | None, masked_frame: np.ndarray | None, result: dict):
        if self.save_dir is None:
            return
        self.save_seq += 1
        prefix = f'{datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]}_{self.save_seq:04d}'
        if raw_frame is not None:
            cv2.imwrite(str(self.save_dir / f'{prefix}_input.jpg'), raw_frame)
        if masked_frame is not None:
            cv2.imwrite(str(self.save_dir / f'{prefix}_masked.jpg'), masked_frame)
        (self.save_dir / f'{prefix}_result.json').write_text(
            json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + '\n',
            encoding='utf-8',
        )

    def _decode_and_handle(self, decode_fn, ros_header):
        header = header_to_dict(ros_header)
        frame: np.ndarray | None = None
        masked: np.ndarray | None = None
        try:
            frame = decode_fn()
            # Depth 없이 순수 이미지 기반 객체 정보(label, confidence, bbox)만 생성
            result = process_frame(frame, self.classifier, header)
            masked = draw_detections(frame, result.get('detections', []))
            ok, encoded = cv2.imencode('.jpg', masked)
            if ok:
                img_msg = CompressedImage()
                img_msg.header = ros_header
                img_msg.format = 'jpeg'
                img_msg.data = encoded.tobytes()
                self.image_pub.publish(img_msg)
        except Exception as exc:
            self.get_logger().error(f'Image classification failed: {exc}')
            result = build_error('processing_error', str(exc), header)

        self._save_capture(frame, masked, result)

        res_msg = String()
        res_msg.data = json.dumps(result, allow_nan=False)
        self.result_pub.publish(res_msg)


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
