"""Webcam capture, replaceable frame judgment, and per-frame ROS output."""

from importlib import import_module

import cv2
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool

from part5_bed_exit.bed_exit_state import BedExitState


class BedExitNode(Node):
    """ROS 2 node publishing five-frame voted bed-exit state as std_msgs/msg/Bool."""

    def __init__(self):
        super().__init__('bed_exit')
        self.capture = None
        self.declare_parameter('method', '')
        self.declare_parameter('output_topic', '/patient/bed_exit')
        self.declare_parameter('camera_index', 0)
        self.declare_parameter('capture_fps', 30.0)
        method_path = self.get_parameter('method').value
        topic = self.get_parameter('output_topic').value
        fps = self.get_parameter('capture_fps').value
        if not topic or not method_path or fps <= 0:
            raise ValueError('Set method=module:function, output_topic, and capture_fps > 0')
        module, separator, name = method_path.partition(':')
        if not separator or not module or not name:
            raise ValueError('method must have the form module:function')
        self.judge = getattr(import_module(module), name)
        if not callable(self.judge):
            raise TypeError('method must refer to a callable')
        self.state = BedExitState()
        self.publisher = self.create_publisher(Bool, topic, 10)
        self.capture = cv2.VideoCapture(self.get_parameter('camera_index').value)
        if not self.capture.isOpened():
            self.capture.release()
            raise RuntimeError('Cannot open webcam')
        self.timer = self.create_timer(1.0 / fps, self.process_frame)

    def process_frame(self):
        ok, frame = self.capture.read()
        if not ok:
            self.get_logger().error('Webcam read failed; no frame decision published')
            self.state = BedExitState()
            return
        try:
            unsafe = self.state.update(self.judge(frame))
        except Exception as exc:
            # Do not invent a safe/unsafe judgment for a failed method.
            self.state = BedExitState()
            self.get_logger().error(f'Judgment failed; history reset: {exc}')
            return
        self.publisher.publish(Bool(data=unsafe))

    def destroy_node(self):
        if self.capture is not None:
            self.capture.release()
        return super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = BedExitNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
