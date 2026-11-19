"""[2단계] 이미지 + Depth(+CameraInfo/TF) 이벤트를 토픽으로 받아 마스킹 이미지와 객체 위치(pose)·종류를 발행하는 노드."""

import threading

from cv_bridge import CvBridge
from message_filters import ApproximateTimeSynchronizer, Subscriber
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CameraInfo, CompressedImage, Image
from std_msgs.msg import Empty, String

from .obstacle_classifier import build_error, process_frame, resolve_default_model_path
from .ros_utils import (
    TfTargetTransformer,
    decode_compressed_bgr,
    header_to_dict,
    load_node_classifier,
    publish_outputs,
)


class RgbdEventNode(Node):
    """2번 노드: RGB + Depth 이벤트 수신(또는 Empty 트리거) -> 객체 위치(pose)·종류 + 마스킹 이미지 발행."""

    def __init__(self):
        super().__init__('rgbd_event_classifier')

        self.declare_parameter('rgb_topic', 'oakd/rgb/image_raw/compressed')
        self.declare_parameter('depth_topic', 'oakd/stereo/image_raw')
        self.declare_parameter('camera_info_topic', 'oakd/rgb/camera_info')
        self.declare_parameter('trigger_topic', 'obstacle/inspect_image')
        self.declare_parameter('use_trigger', False)
        self.declare_parameter('output_topic', 'obstacle_classifier/result')
        self.declare_parameter('image_output_topic', 'obstacle_classifier/image/compressed')
        self.declare_parameter('target_frame', 'map')
        self.declare_parameter('fallback_to_camera', True)
        self.declare_parameter(
            'method',
            'part6_obstacle_classifier.obstacle_classifier:Yolo11Classifier',
        )
        self.declare_parameter('model_path', resolve_default_model_path())
        self.declare_parameter('conf_threshold', 0.25)
        self.declare_parameter('min_depth', 0.2)
        self.declare_parameter('max_depth', 5.0)
        self.declare_parameter('sync_slop', 0.1)
        self.declare_parameter('capture_timeout_sec', 3.0)

        method = str(self.get_parameter('method').value)
        model_path = str(self.get_parameter('model_path').value)
        conf_threshold = float(self.get_parameter('conf_threshold').value)
        self.use_trigger = bool(self.get_parameter('use_trigger').value)
        self.min_depth = float(self.get_parameter('min_depth').value)
        self.max_depth = float(self.get_parameter('max_depth').value)
        self.capture_timeout_sec = float(self.get_parameter('capture_timeout_sec').value)

        self.classifier = load_node_classifier(method, model_path, conf_threshold)
        self.bridge = CvBridge()
        self.lock = threading.Lock()
        self.K = None
        self.pending_capture = not self.use_trigger
        self.timeout_timer = None

        self.tf = TfTargetTransformer(
            self,
            target_frame=str(self.get_parameter('target_frame').value),
            fallback_to_camera=bool(self.get_parameter('fallback_to_camera').value),
        )

        self.result_pub = self.create_publisher(
            String, str(self.get_parameter('output_topic').value), 10
        )
        self.image_pub = self.create_publisher(
            CompressedImage, str(self.get_parameter('image_output_topic').value), 10
        )

        self.create_subscription(
            CameraInfo,
            str(self.get_parameter('camera_info_topic').value),
            self.camera_info_callback,
            qos_profile_sensor_data,
        )

        self.rgb_sub = Subscriber(
            self,
            CompressedImage,
            str(self.get_parameter('rgb_topic').value),
            qos_profile=qos_profile_sensor_data,
        )
        self.depth_sub = Subscriber(
            self,
            Image,
            str(self.get_parameter('depth_topic').value),
            qos_profile=qos_profile_sensor_data,
        )
        self.ts = ApproximateTimeSynchronizer(
            [self.rgb_sub, self.depth_sub],
            queue_size=10,
            slop=float(self.get_parameter('sync_slop').value),
        )
        self.ts.registerCallback(self.synced_callback)

        if self.use_trigger:
            self.create_subscription(
                Empty,
                str(self.get_parameter('trigger_topic').value),
                self.trigger_callback,
                10,
            )

        self.get_logger().info(
            f'[2] RgbdEvent node ready (use_trigger={self.use_trigger}, '
            f'target_frame={self.tf.target_frame}, model={model_path})'
        )

    def camera_info_callback(self, msg: CameraInfo):
        with self.lock:
            if self.K is None:
                self.K = np.array(msg.k, dtype=np.float64).reshape(3, 3)

    def _cancel_timeout(self):
        if self.timeout_timer is not None:
            self.timeout_timer.cancel()
            self.timeout_timer = None

    def trigger_callback(self, _msg: Empty):
        with self.lock:
            self.pending_capture = True
            self._cancel_timeout()
            self.timeout_timer = self.create_timer(
                self.capture_timeout_sec, self._on_capture_timeout
            )

    def _on_capture_timeout(self):
        with self.lock:
            if not self.pending_capture:
                self._cancel_timeout()
                return
            self.pending_capture = False
            self._cancel_timeout()
        err = build_error('missing_data', 'Timed out waiting for RGB/Depth or CameraInfo')
        publish_outputs(self.result_pub, self.image_pub, err)

    def synced_callback(self, rgb_msg: CompressedImage, depth_msg: Image):
        with self.lock:
            if self.use_trigger and not self.pending_capture:
                return
            K = None if self.K is None else self.K.copy()
            if K is None:
                self.get_logger().warn('CameraInfo not ready yet; skipping pose calculation')
                return
            if self.use_trigger:
                self.pending_capture = False
                self._cancel_timeout()

        header = header_to_dict(rgb_msg.header)
        try:
            rgb_frame = decode_compressed_bgr(rgb_msg)
            depth_frame = self.bridge.imgmsg_to_cv2(depth_msg, desired_encoding='passthrough')
            camera_frame = depth_msg.header.frame_id or rgb_msg.header.frame_id
            result = process_frame(
                rgb_frame,
                self.classifier,
                header,
                depth_image=depth_frame,
                K=K,
                transform_fn=lambda x, y, z: self.tf.transform(
                    x, y, z, camera_frame, depth_msg.header.stamp
                ),
                min_depth=self.min_depth,
                max_depth=self.max_depth,
            )
            publish_outputs(self.result_pub, self.image_pub, result, rgb_frame, rgb_msg.header)
        except Exception as exc:
            self.get_logger().error(f'RGBD classification failed: {exc}')
            err = build_error('processing_error', str(exc), header)
            publish_outputs(self.result_pub, self.image_pub, err)


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = RgbdEventNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
