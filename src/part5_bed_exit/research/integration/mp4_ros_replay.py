"""웹캠 대신 MP4 한 개를 Part 5 판단·투표·ROS 발행 경로로 재생하는 시험 노드.

운영 메서드 로더와 ``BedExitState``를 재사용해 Bool 토픽을 발행하고 영상 끝에서
정지한다. 실제 ``BedExitNode``의 카메라 입력을 수정하지 않고 통합을 확인하기 위한 도구다.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import cv2
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool

from part5_bed_exit.bed_exit_state import BedExitState
from part5_bed_exit.pretrained import create_method


class Mp4RosReplayNode(Node):
    """운영 웹캠 노드를 수정하지 않고 MP4만 재생하는 ROS 시험 노드."""

    def __init__(self, video_path, method, method_config, topic, show=False):
        """영상·판단 객체·발행자·프레임 주기 타이머를 초기화한다."""
        super().__init__('part5_mp4_ros_replay')
        self.capture = None
        self.detector = None
        self.finished = False
        self.show = bool(show)

        path = Path(video_path).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() != '.mp4':
            raise ValueError(f'Provide an existing MP4 file: {path}')
        if not topic:
            raise ValueError('topic must not be empty')

        try:
            self.detector = create_method(method_config, method)
            self.state = BedExitState()
            self.publisher = self.create_publisher(Bool, topic, 10)
            self.capture = cv2.VideoCapture(str(path))
            if not self.capture.isOpened():
                raise RuntimeError(f'Cannot open MP4: {path}')
            fps = float(self.capture.get(cv2.CAP_PROP_FPS))
            if not math.isfinite(fps) or fps <= 0:
                raise RuntimeError(f'MP4 has invalid FPS: {fps}')
            self.timer = self.create_timer(1.0 / fps, self.process_frame)
            self.get_logger().info(
                f'MP4 replay started: video={path}, fps={fps:.3f}, '
                f'topic={topic}, method={method}'
            )
        except Exception:
            self.close_resources()
            raise

    def process_frame(self):
        """영상 프레임 하나를 판단·투표해 유효한 결과만 발행한다."""
        ok, frame = self.capture.read()
        if not ok or frame is None:
            # EOF must never be converted into a fabricated SAFE publication.
            self.state = BedExitState()
            self.finished = True
            self.timer.cancel()
            self.get_logger().info('MP4 replay complete; no EOF decision published')
            return

        try:
            unsafe = self.state.update(self.detector.judge(frame))
        except Exception as exc:
            self.state = BedExitState()
            self.get_logger().error(f'Judgment failed; history reset: {exc}')
            render = getattr(self, '_render_preview', None)
            if callable(render):
                render(frame, None, str(exc))
            return

        self.publisher.publish(Bool(data=unsafe))
        render = getattr(self, '_render_preview', None)
        if callable(render):
            render(frame, unsafe, 'ok')

    def _render_preview(self, frame, unsafe, reason):
        """판단 불가 프레임을 포함해 재생 상태를 화면에 계속 표시한다."""
        if not self.show:
            return
        canvas = frame.copy()
        if unsafe is None:
            color = (0, 165, 255)
            label = f'NO DECISION: {reason} (history reset)'
        else:
            color = (0, 0, 255) if unsafe else (0, 220, 0)
            label = f"/patient/bed_exit = {unsafe} ({sum(self.state.history)}/5)"
        cv2.putText(canvas, label, (12, 30), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, color, 2, cv2.LINE_AA)
        try:
            cv2.imshow('Part 5 MP4 ROS Replay', canvas)
            cv2.waitKey(1)
        except cv2.error:
            self.show = False

    def close_resources(self):
        """영상 캡처·판단 객체·OpenCV 창을 안전하게 정리한다."""
        if self.capture is not None:
            self.capture.release()
            self.capture = None
        close = getattr(self.detector, 'close', None)
        if callable(close):
            close()
        self.detector = None
        if self.show:
            try:
                cv2.destroyAllWindows()
            except cv2.error:
                pass

    def destroy_node(self):
        """시험 자원을 먼저 정리한 뒤 ROS 노드를 해제한다."""
        self.close_resources()
        return super().destroy_node()


def parse_args(argv=None):
    """MP4 ROS 재생 노드의 명령행 인자를 해석한다."""
    parser = argparse.ArgumentParser(
        description='Publish Part 5 bed-exit Bool states while replaying one MP4.')
    parser.add_argument('--video', required=True, help='Input MP4 path')
    parser.add_argument('--config', required=True, help='Reviewed method config JSON')
    parser.add_argument('--method', default='segmentation',
                        choices=('detection', 'segmentation', 'yolo_pose', 'mediapipe_pose'))
    parser.add_argument('--topic', default='/patient/bed_exit')
    parser.add_argument('--show', action='store_true')
    return parser.parse_args(argv)


def main(argv=None):
    """ROS를 초기화하고 MP4 재생 노드를 영상 끝까지 실행한다."""
    args = parse_args(argv)
    rclpy.init(args=[])
    node = None
    try:
        node = Mp4RosReplayNode(
            args.video, args.method, args.config, args.topic, args.show)
        while rclpy.ok() and not node.finished:
            rclpy.spin_once(node, timeout_sec=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
