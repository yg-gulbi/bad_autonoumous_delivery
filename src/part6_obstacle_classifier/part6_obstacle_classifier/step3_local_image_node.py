"""[3단계] 로컬에 보유한 이미지 파일/폴더를 읽어 마스킹 이미지와 객체 종류 JSON을 ROS 2 토픽으로 발행하는 노드."""

from pathlib import Path

import cv2
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import String

from .obstacle_classifier import (
    build_error,
    crop_and_downsample,
    process_frame,
    resolve_default_model_path,
)
from .ros_utils import load_node_classifier, publish_outputs

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp'}


class LocalImagePublisherNode(Node):
    """3번 노드: 로컬 이미지 파일/폴더 순회 -> 객체 종류 JSON + 마스킹 이미지 토픽 발행."""

    def __init__(self):
        super().__init__('local_image_classifier')

        self.declare_parameter('input_path', '')
        self.declare_parameter('output_topic', 'obstacle_classifier/result')
        self.declare_parameter('image_output_topic', 'obstacle_classifier/image/compressed')
        self.declare_parameter(
            'method',
            'part6_obstacle_classifier.obstacle_classifier:Yolo11Classifier',
        )
        self.declare_parameter('model_path', resolve_default_model_path())
        self.declare_parameter('conf_threshold', 0.25)
        self.declare_parameter('publish_hz', 2.0)
        self.declare_parameter('loop', False)
        self.declare_parameter('resize_width', 512)
        self.declare_parameter('resize_height', 288)
        self.declare_parameter('crop_ratio', '')

        input_path = str(self.get_parameter('input_path').value)
        if not input_path:
            raise ValueError('input_path 파라미터에 이미지 파일 또는 폴더 경로를 지정하세요.')
        self.image_paths = self._collect_images(Path(input_path))
        if not self.image_paths:
            raise FileNotFoundError(f'처리할 이미지가 없습니다: {input_path}')

        method = str(self.get_parameter('method').value)
        model_path = str(self.get_parameter('model_path').value)
        conf_threshold = float(self.get_parameter('conf_threshold').value)
        self.classifier = load_node_classifier(method, model_path, conf_threshold)

        self.loop = bool(self.get_parameter('loop').value)
        rw = int(self.get_parameter('resize_width').value)
        rh = int(self.get_parameter('resize_height').value)
        self.target_size = (rw, rh) if rw > 0 and rh > 0 else None
        self.crop_ratio = self._parse_crop_ratio(str(self.get_parameter('crop_ratio').value))
        self.index = 0

        self.result_pub = self.create_publisher(
            String, str(self.get_parameter('output_topic').value), 10
        )
        self.image_pub = self.create_publisher(
            CompressedImage, str(self.get_parameter('image_output_topic').value), 10
        )

        hz = max(0.1, float(self.get_parameter('publish_hz').value))
        self.timer = self.create_timer(1.0 / hz, self.publish_next)
        self.get_logger().info(
            f'[3] LocalImage node ready (count={len(self.image_paths)}, hz={hz}, model={model_path})'
        )

    @staticmethod
    def _collect_images(path: Path) -> list[Path]:
        if not path.exists():
            raise FileNotFoundError(f'경로를 찾을 수 없습니다: {path}')
        if path.is_file():
            return [path]
        return sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS)

    @staticmethod
    def _parse_crop_ratio(raw: str):
        if not raw.strip():
            return None
        parts = [float(v.strip()) for v in raw.split(',')]
        if len(parts) != 4:
            raise ValueError('crop_ratio는 x1,y1,x2,y2 4개 실수여야 합니다.')
        return (parts[0], parts[1], parts[2], parts[3])

    def publish_next(self):
        if self.index >= len(self.image_paths):
            if self.loop:
                self.index = 0
            else:
                self.get_logger().info('모든 로컬 이미지 발행을 완료했습니다.')
                self.timer.cancel()
                return

        img_path = self.image_paths[self.index]
        self.index += 1

        now_msg = self.get_clock().now().to_msg()
        ros_header = CompressedImage().header
        ros_header.stamp = now_msg
        ros_header.frame_id = img_path.name
        header = {
            'stamp': {'sec': now_msg.sec, 'nanosec': now_msg.nanosec},
            'frame_id': img_path.name,
        }

        try:
            raw = cv2.imread(str(img_path))
            if raw is None:
                raise ValueError(f'이미지 로드 실패: {img_path}')
            frame = crop_and_downsample(raw, crop_ratio=self.crop_ratio, size=self.target_size)
            result = process_frame(frame, self.classifier, header)
            publish_outputs(self.result_pub, self.image_pub, result, frame, ros_header)
        except Exception as exc:
            self.get_logger().error(f'Local image failed ({img_path.name}): {exc}')
            err = build_error('processing_error', str(exc), header)
            publish_outputs(self.result_pub, self.image_pub, err)


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = LocalImagePublisherNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
