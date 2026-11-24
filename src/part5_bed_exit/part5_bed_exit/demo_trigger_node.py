#!/usr/bin/env python3
"""Triggerable Bed-Exit demo ROS 2 node (`ros2 run part5_bed_exit demo_trigger`).

Starts at frame 0 (SAFE) and holds until triggered by:
  - Pressing 't' / Space / Enter in the OpenCV preview window, OR
  - Pressing Enter in this terminal, OR
  - Publishing `std_msgs/msg/Empty` to `/part5_demo/trigger`
Once triggered, plays frames 0..179 (0:00~0:06) and holds at frame 179 (SAFE after exit).
Press 'r' in the preview window (or publish to `/part5_demo/reset`) to reset to frame 0.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import select
import sys

import cv2
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, Empty

from part5_bed_exit.bed_exit_method import load_bed_exit_method
from part5_bed_exit.bed_exit_state import BedExitState
from part5_bed_exit.methods.bed_exit_core import JudgmentUnavailable
from part5_bed_exit.tts_speaker import (
    DEFAULT_TTS_LANGUAGE,
    DEFAULT_TTS_MESSAGE,
    DEFAULT_TTS_REPEAT_INTERVAL,
    TtsSpeaker,
)


def _resolve_repo_relative(raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if candidate.is_file():
        return candidate.resolve()
    pkg_root = Path(__file__).resolve().parents[1]
    rel = raw_path.removeprefix('src/part5_bed_exit/')
    alt = pkg_root / rel
    return alt.resolve() if alt.is_file() else candidate.resolve()


class DemoTriggerBedExitNode(Node):
    """Triggerable demo node holding SAFE frame 0 until user/topic trigger."""

    def __init__(
        self,
        video_path: Path,
        config_path: Path,
        show: bool = True,
        device: str = 'cpu',
        tts_enabled: bool = True,
        max_frames: int = 180,
    ):
        super().__init__('bed_exit')
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise RuntimeError(f'Cannot open video: {video_path}')
        self.fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
        self.frames = []
        for _ in range(max_frames):
            ok, frame = cap.read()
            if not ok:
                break
            self.frames.append(frame)
        cap.release()
        if not self.frames:
            raise RuntimeError(f'No frames loaded from {video_path}')

        overrides = {'device': device} if device else None
        self.detector = load_bed_exit_method(None, str(config_path), overrides=overrides)
        self.state = BedExitState(window_size=5, threshold=3)
        self.tts = TtsSpeaker(
            message=DEFAULT_TTS_MESSAGE,
            enabled=tts_enabled,
            repeat_interval=DEFAULT_TTS_REPEAT_INTERVAL,
            language=DEFAULT_TTS_LANGUAGE,
        )
        self.pub = self.create_publisher(Bool, '/patient/bed_exit', 10)
        self.create_subscription(Empty, '/part5_demo/trigger', lambda _m: self.trigger(), 10)
        self.create_subscription(Empty, '/part5_demo/reset', lambda _m: self.reset_demo(), 10)

        self.show = show
        self.triggered = False
        self.frame_idx = 0
        self.timer = self.create_timer(1.0 / self.fps, self._tick)

        self.get_logger().info(
            'DemoTriggerBedExitNode ready (SAFE hold at frame 0). '
            'Press Enter in terminal, Space/t in preview, or pub /part5_demo/trigger.'
        )

    def trigger(self) -> None:
        if not self.triggered:
            self.triggered = True
            self.frame_idx = 0
            self.get_logger().info('TRIGGERED: playing demo clip -> holding at final frame')

    def reset_demo(self) -> None:
        self.triggered = False
        self.frame_idx = 0
        self.state.reset()
        self.tts.set_active(False)
        self.get_logger().info('RESET: holding at frame 0 (SAFE)')

    def _poll_stdin(self) -> None:
        try:
            if select.select([sys.stdin], [], [], 0.0)[0]:
                line = sys.stdin.readline().strip().lower()
                if line == 'r':
                    self.reset_demo()
                else:
                    self.trigger()
        except Exception:
            pass

    def _tick(self) -> None:
        self._poll_stdin()
        idx = self.frame_idx if self.triggered else 0
        frame = self.frames[idx]
        if self.triggered and self.frame_idx < len(self.frames) - 1:
            self.frame_idx += 1

        try:
            raw_bool = bool(self.detector.judge(frame))
        except JudgmentUnavailable:
            self.state.reset()
            self.tts.set_active(False)
        else:
            out_bool = bool(self.state.update(raw_bool))
            self.tts.set_active(out_bool)
            self.pub.publish(Bool(data=out_bool))

        if self.show:
            canvas = self.detector.render(frame)
            status = 'PLAY/HOLD' if self.triggered else 'WAIT (Enter/Space to trigger)'
            cv2.putText(canvas, status, (10, canvas.shape[0] - 14),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 0), 2)
            cv2.imshow('Part 5 Bed Exit Demo', canvas)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord(' '), ord('t'), 13):
                self.trigger()
            elif key == ord('r'):
                self.reset_demo()
            elif key == ord('q'):
                rclpy.shutdown()

    def close(self) -> None:
        self.tts.close()
        close_fn = getattr(self.detector, 'close', None)
        if callable(close_fn):
            close_fn()
        if self.show:
            cv2.destroyAllWindows()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Triggerable Bed-Exit Demo Runner.')
    parser.add_argument('--video', default='src/part5_bed_exit/run_data/test_bed1_demo_10m_freeze.mp4')
    parser.add_argument('--config', default='src/part5_bed_exit/run_data/test_bed1_demo_roi.json')
    parser.add_argument('--device', default='cpu')
    parser.add_argument('--no-show', action='store_true')
    parser.add_argument('--no-tts', action='store_true')
    args, _ = parser.parse_known_args(argv)

    rclpy.init(args=argv)
    node = DemoTriggerBedExitNode(
        _resolve_repo_relative(args.video),
        _resolve_repo_relative(args.config),
        show=not args.no_show,
        device=args.device,
        tts_enabled=not args.no_tts,
    )
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.close()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
