"""part6 노드들이 공유하는 ROS 2 메시지 디코딩, TF2 변환 및 결과 발행 헬퍼."""

import json

import cv2
from cv_bridge import CvBridge
from geometry_msgs.msg import PointStamped
import numpy as np
from rclpy.duration import Duration
from sensor_msgs.msg import CompressedImage, Image
from std_msgs.msg import String
from tf2_ros import Buffer, TransformListener
import tf2_geometry_msgs  # noqa: F401  # PointStamped에 대한 tf2_buffer.transform 지원 등록

from .obstacle_classifier import draw_detections, load_classifier


def load_node_classifier(method: str, model_path: str, conf_threshold: float):
    """노드 파라미터에 맞춰 분류기 어댑터를 로드."""
    if method.endswith(':Yolo11Classifier'):
        return load_classifier(method, model_path=model_path, conf_threshold=conf_threshold)
    return load_classifier(method)


def header_to_dict(header) -> dict:
    """ROS Header를 JSON 직렬화용 딕셔너리로 변환."""
    return {
        'stamp': {'sec': header.stamp.sec, 'nanosec': header.stamp.nanosec},
        'frame_id': header.frame_id,
    }


def decode_compressed_bgr(msg: CompressedImage) -> np.ndarray:
    """CompressedImage를 BGR numpy 배열로 디코딩."""
    np_arr = np.frombuffer(msg.data, np.uint8)
    frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError('Failed to decode CompressedImage')
    return frame


def decode_raw_bgr(msg: Image, bridge: CvBridge) -> np.ndarray:
    """sensor_msgs/Image를 BGR numpy 배열로 변환."""
    return bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')


def publish_outputs(
    result_pub,
    image_pub,
    result: dict,
    frame: np.ndarray | None = None,
    ros_header=None,
):
    """결과 JSON(String)과 마스킹된 이미지(CompressedImage)를 함께 발행."""
    if frame is not None and image_pub is not None:
        masked = draw_detections(frame, result.get('detections', []))
        ok, encoded = cv2.imencode('.jpg', masked)
        if ok:
            img_msg = CompressedImage()
            if ros_header is not None:
                img_msg.header = ros_header
            img_msg.format = 'jpeg'
            img_msg.data = encoded.tobytes()
            image_pub.publish(img_msg)

    res_msg = String()
    res_msg.data = json.dumps(result, allow_nan=False)
    result_pub.publish(res_msg)


class TfTargetTransformer:
    """카메라 3D 좌표를 지정한 target_frame(map 등)으로 변환하는 TF2 헬퍼."""

    def __init__(self, node, target_frame: str = 'map', fallback_to_camera: bool = False):
        self.node = node
        self.target_frame = target_frame
        self.fallback_to_camera = fallback_to_camera
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, node)

    def transform(self, x: float, y: float, z: float, frame_id: str, stamp) -> dict | None:
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
            self.node.get_logger().warn(f'TF transform to {self.target_frame} failed: {exc}')
            if self.fallback_to_camera:
                return {'x': float(x), 'y': float(y), 'z': float(z), 'frame': frame_id}
            return None
