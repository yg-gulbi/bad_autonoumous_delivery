"""Webcam capture, replaceable detector object, and per-frame ROS output."""

import math

import cv2
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool

from part5_bed_exit.bed_exit_method import (
    load_bed_exit_method,
    validate_bed_exit_method,
)
from part5_bed_exit.bed_exit_state import BedExitState


class BedExitNode(Node):
    """ROS 2 node publishing five-frame voted bed-exit state as std_msgs/msg/Bool."""

    def __init__(self, detector=None):
        super().__init__('bed_exit')
        # 초기화 도중 예외가 나더라도 destroy_node()/예외 정리가 안전하도록 먼저 None으로 둔다.
        self.capture = None
        self.detector = None

        # ROS 2 실행 파라미터 선언 (output_topic은 ICD 확정안 /patient/bed_exit 사용)
        self.declare_parameter('method', '')
        self.declare_parameter('method_config', '')
        self.declare_parameter('output_topic', '/patient/bed_exit')
        self.declare_parameter('camera_index', 0)
        self.declare_parameter('capture_fps', 30.0)
        method_path = self.get_parameter('method').value
        topic = self.get_parameter('output_topic').value
        fps = self.get_parameter('capture_fps').value

        # 직접 detector 객체를 전달받지 않은 경우에만 method 파라미터 경로를 필수로 요구한다.
        if not topic or not math.isfinite(fps) or fps <= 0 or (detector is None and not method_path):
            raise ValueError(
                'Set method=module:ClassOrObject, output_topic, and capture_fps > 0'
            )

        # 클래스/객체 경로 로딩 또는 코드 직접 주입 객체를 검증해 self.detector로 고정한다.
        self.detector = (
            load_bed_exit_method(method_path, self.get_parameter('method_config').value)
            if detector is None
            else validate_bed_exit_method(detector)
        )

        # detector 생성 이후 카메라/타이머 초기화에 실패하면 확보한 리소스를 즉시 정리한다.
        try:
            self.state = BedExitState()
            self.publisher = self.create_publisher(Bool, topic, 10)
            self.capture = cv2.VideoCapture(self.get_parameter('camera_index').value)
            if not self.capture.isOpened():
                raise RuntimeError('Cannot open webcam')
            self.timer = self.create_timer(1.0 / fps, self.process_frame)
        except Exception:
            self._release_capture()
            self._close_detector()
            raise

    def process_frame(self):
        # 프레임 수신 실패 시 거짓 정상(False) 발행을 막기 위해 이력을 초기화하고 발행을 건너뛴다.
        try:
            ok, frame = self.capture.read()
            if not ok or frame is None:
                raise RuntimeError('Webcam read failed')
        except Exception as exc:
            self.get_logger().error(f'Webcam read failed; no frame decision published: {exc}')
            self.state = BedExitState()
            return
        try:
            unsafe = self.state.update(self.detector.judge(frame))
        except Exception as exc:
            # Do not invent a safe/unsafe judgment for a failed method.
            # 판단 메서드 예외 또는 비-bool 반환 시에도 동일하게 5프레임 이력을 초기화한다.
            self.state = BedExitState()
            self.get_logger().error(f'Judgment failed; history reset: {exc}')
            return
        self.publisher.publish(Bool(data=unsafe))

    def _release_capture(self):
        if self.capture is not None:
            self.capture.release()
            self.capture = None

    def _close_detector(self):
        # close()는 선택 규약이므로 메서드가 존재할 때만 호출한다.
        close = getattr(self.detector, 'close', None)
        if callable(close):
            close()
        self.detector = None

    def destroy_node(self):
        self._release_capture()
        self._close_detector()
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
