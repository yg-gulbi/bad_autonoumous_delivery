"""Real-time obstacle detection and map-pose publisher for testing on /robot3."""

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
from std_msgs.msg import String
from tf2_ros import Buffer, TransformListener
import tf2_geometry_msgs  # noqa: F401

from .obstacle_classifier import build_error, draw_detections, load_classifier, process_frame


class RealtimeObstacleTestNode(Node):
    def __init__(self):
        super().__init__('test_realtime_obstacle', namespace='/robot3')

        self.declare_parameter('rgb_topic', 'oakd/rgb/image_raw/compressed')
        self.declare_parameter('depth_topic', 'oakd/stereo/image_raw')
        self.declare_parameter('camera_info_topic', 'oakd/rgb/camera_info')
        self.declare_parameter('output_topic', 'obstacle_classifier/result')
        self.declare_parameter('image_output_topic', 'obstacle_classifier/image/compressed')
        self.declare_parameter('target_frame', 'map')
        self.declare_parameter('model_path', 'yolo11n.pt')
        self.declare_parameter('conf_threshold', 0.25)
        self.declare_parameter('min_depth', 0.2)
        self.declare_parameter('max_depth', 5.0)
        self.declare_parameter('sync_slop', 0.1)

        model_path = str(self.get_parameter('model_path').value)
        conf_threshold = float(self.get_parameter('conf_threshold').value)
        self.min_depth = float(self.get_parameter('min_depth').value)
        self.max_depth = float(self.get_parameter('max_depth').value)
        self.target_frame = str(self.get_parameter('target_frame').value)
        sync_slop = float(self.get_parameter('sync_slop').value)

        self.classifier = load_classifier(
            'part6_obstacle_classifier.obstacle_classifier:Yolo11Classifier',
            model_path=model_path,
            conf_threshold=conf_threshold,
        )

        self.bridge = CvBridge()
        self.lock = threading.Lock()
        self.K = None
        self.logged_compressed_ok = False

        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)

        self.publisher = self.create_publisher(
            String, str(self.get_parameter('output_topic').value), 10
        )
        self.image_publisher = self.create_publisher(
            CompressedImage, str(self.get_parameter('image_output_topic').value), 10
        )

        self.create_subscription(
            CameraInfo,
            str(self.get_parameter('camera_info_topic').value),
            self.camera_info_callback,
            qos_profile_sensor_data,
        )

        rgb_topic = str(self.get_parameter('rgb_topic').value)
        depth_topic = str(self.get_parameter('depth_topic').value)
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
            f'Realtime test node started in {self.get_namespace()} '
            f'(RGB compressed: {rgb_topic}, Depth: {depth_topic})'
        )

    def camera_info_callback(self, msg: CameraInfo):
        with self.lock:
            if self.K is None:
                self.K = np.array(msg.k, dtype=np.float64).reshape(3, 3)
                self.get_logger().info('CameraInfo received.')

    def _transform_to_target(self, x: float, y: float, z: float, frame_id: str, stamp):
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
        with self.lock:
            if self.K is None:
                return
            K = self.K.copy()

        header = {
            'stamp': {
                'sec': rgb_msg.header.stamp.sec,
                'nanosec': rgb_msg.header.stamp.nanosec,
            },
            'frame_id': rgb_msg.header.frame_id,
        }

        try:
            np_arr = np.frombuffer(rgb_msg.data, np.uint8)
            rgb_frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            if not self.logged_compressed_ok and rgb_frame is not None:
                self.get_logger().info(
                    f'CompressedImage received & decoded: format={rgb_msg.format}, '
                    f'shape={rgb_frame.shape}'
                )
                self.logged_compressed_ok = True

            depth_frame = self.bridge.imgmsg_to_cv2(depth_msg, desired_encoding='passthrough')
            camera_frame = depth_msg.header.frame_id or rgb_msg.header.frame_id
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

            annotated = draw_detections(rgb_frame, result['detections'])
            ok, encoded = cv2.imencode('.jpg', annotated)
            if ok:
                img_msg = CompressedImage()
                img_msg.header = rgb_msg.header
                img_msg.format = 'jpeg'
                img_msg.data = encoded.tobytes()
                self.image_publisher.publish(img_msg)
        except Exception as exc:
            self.get_logger().error(f'Realtime classification failed: {exc}')
            result = build_error('processing_error', str(exc), header)

        msg = String()
        msg.data = json.dumps(result, allow_nan=False)
        self.publisher.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = RealtimeObstacleTestNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
