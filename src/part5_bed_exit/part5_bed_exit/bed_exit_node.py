"""Webcam capture, replaceable detector object, and per-frame ROS output."""

import math
import os

import cv2
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool

from part5_bed_exit.bed_exit_method import (
    load_bed_exit_method,
    validate_bed_exit_method,
)
from part5_bed_exit.bed_exit_state import BedExitState
from part5_bed_exit.methods.common import default_camera_index

DEFAULT_METHOD = (
    'part5_bed_exit.methods.segmentation_method:SegmentationBedExitMethod'
)
WINDOW_TITLE = 'Part 5 Bed Exit (/patient/bed_exit)'


class BedExitNode(Node):
    """ROS 2 node publishing five-frame voted bed-exit state as std_msgs/msg/Bool."""

    def __init__(self, detector=None, video_path: str | None = None):
        super().__init__('bed_exit')
        # 초기화 도중 예외가 나더라도 destroy_node()/예외 정리가 안전하도록 먼저 None으로 둔다.
        self.capture = None
        self.detector = None
        self.show = False
        self.is_video_file = False
        self.loop_video = True

        # 기본 Segmentation 실행을 제공하면서 다른 클래스/내장 프리셋도 선택할 수 있게 한다.
        self.declare_parameter('method', DEFAULT_METHOD)
        self.declare_parameter('method_config', '')
        self.declare_parameter('output_topic', '/patient/bed_exit')
        self.declare_parameter('camera_index', default_camera_index())
        self.declare_parameter('video_path', '' if video_path is None else str(video_path))
        self.declare_parameter('loop_video', True)
        self.declare_parameter('capture_fps', 30.0)
        self.declare_parameter(
            'show',
            bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')),
        )
        method_path = self.get_parameter('method').value
        topic = self.get_parameter('output_topic').value
        camera_index = self.get_parameter('camera_index').value
        resolved_video = str(self.get_parameter('video_path').value).strip()
        self.loop_video = bool(self.get_parameter('loop_video').value)
        fps = self.get_parameter('capture_fps').value
        self.show = bool(self.get_parameter('show').value)

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

        # detector 생성 이후 카메라/영상·타이머 초기화에 실패하면 확보한 리소스를 즉시 정리한다.
        try:
            self.state = BedExitState()
            self.publisher = self.create_publisher(Bool, topic, 10)
            self.is_video_file = bool(resolved_video)
            source = resolved_video if self.is_video_file else camera_index
            source_desc = (
                f'video_path={resolved_video}'
                if self.is_video_file
                else f'camera_index={camera_index}'
            )
            self.capture = cv2.VideoCapture(source)
            if not self.capture.isOpened():
                raise RuntimeError(f'Cannot open video source ({source_desc})')
            self.timer = self.create_timer(1.0 / fps, self.process_frame)
            self.get_logger().info(
                f'BedExitNode started: {source_desc}, topic={topic}, '
                f'method={method_path}, show={self.show}'
            )
        except Exception:
            self._release_capture()
            self._close_detector()
            raise

    def _render_preview(self, frame, voted_unsafe):
        if not self.show:
            return
        render = getattr(self.detector, 'render', None)
        canvas = render(frame) if callable(render) else frame.copy()
        h, w = canvas.shape[:2]
        true_count = sum(self.state.history)
        win_len = len(self.state.history)
        if voted_unsafe is None:
            vote_text = f'5-frame vote: RESET ({true_count}/{win_len})'
            color = (0, 165, 255)
        elif voted_unsafe:
            vote_text = f'5-frame vote: UNSAFE ({true_count}/{win_len}) -> True'
            color = (0, 0, 255)
        else:
            vote_text = f'5-frame vote: SAFE ({true_count}/{win_len}) -> False'
            color = (0, 220, 0)
        cv2.rectangle(canvas, (0, h - 36), (min(w, 460), h), (20, 20, 20), -1)
        cv2.putText(
            canvas,
            vote_text,
            (10, h - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            color,
            2,
            cv2.LINE_AA,
        )
        try:
            cv2.imshow(WINDOW_TITLE, canvas)
            cv2.waitKey(1)
        except cv2.error:
            self.show = False

    def process_frame(self):
        # 프레임 수신 실패 시 거짓 정상(False) 발행을 막기 위해 이력을 초기화하고 발행을 건너뛴다.
        ok, frame = self.capture.read()
        if not ok and self.is_video_file and self.loop_video:
            self.capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            self.state = BedExitState()
            reset = getattr(self.detector, 'reset', None)
            if callable(reset):
                reset()
            ok, frame = self.capture.read()
        if not ok:
            self.state = BedExitState()
            if self.is_video_file:
                self.get_logger().info('Video playback finished; stopping timer')
                self.timer.cancel()
            else:
                self.get_logger().error('Webcam read failed; no frame decision published')
            return
        try:
            unsafe = self.state.update(self.detector.judge(frame))
        except Exception as exc:
            # Do not invent a safe/unsafe judgment for a failed method.
            # 판단 메서드 예외 또는 비-bool 반환 시에도 동일하게 5프레임 이력을 초기화한다.
            self.state = BedExitState()
            self.get_logger().error(f'Judgment failed; history reset: {exc}')
            render_preview = getattr(self, '_render_preview', None)
            if callable(render_preview):
                render_preview(frame, None)
            return
        self.publisher.publish(Bool(data=unsafe))
        render_preview = getattr(self, '_render_preview', None)
        if callable(render_preview):
            render_preview(frame, unsafe)

    def _release_capture(self):
        if self.capture is not None:
            self.capture.release()
            self.capture = None
        if self.show:
            try:
                cv2.destroyAllWindows()
            except cv2.error:
                pass

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
    cli_args = rclpy.utilities.remove_ros_args(args=args)[1:]
    cli_video = cli_args[0] if cli_args else None
    node = None
    try:
        node = BedExitNode(video_path=cli_video)
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
