"""Benchmark four Part 5 methods on one MP4 while recording their ROS topics."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import signal
import subprocess
import time

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool

from part5_bed_exit.bed_exit_state import BedExitState
from part5_bed_exit.methods.bed_exit_core import JudgmentUnavailable, MODEL_FILES
from part5_bed_exit.pretrained import create_method
from research.datasets.downloads import save_json, sha256


POSE_LOSS_REASONS = {
    'no_person',
    'patient_unassociated',
    'missing_keypoints',
    'insufficient_torso_keypoints',
    'incomplete_body_coverage',
    'incomplete_torso_coverage',
}


def inspect_mp4(path):
    """Validate a constant-frame-rate MP4 using the base OpenCV dependency."""
    path = Path(path).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != '.mp4':
        raise ValueError(f'Provide an existing MP4 file: {path}')
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise ValueError(f'Cannot open MP4: {path}')
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if not math.isfinite(fps) or fps <= 0 or frames <= 0 or width <= 0 or height <= 0:
            raise ValueError(f'Invalid MP4 metadata: {path}')
        ok, first = capture.read()
        if not ok or first is None:
            raise ValueError(f'Cannot decode the first MP4 frame: {path}')
        return {
            'path': str(path), 'width': width, 'height': height, 'fps': fps,
            'duration_s': frames / fps, 'reported_frames': frames,
            'bytes': path.stat().st_size,
            'timing': 'constant frame rate: media_s = frame_index / source_fps',
        }
    finally:
        capture.release()


def video_frames(path, fps):
    """Yield every BGR frame once without requiring the optional PyAV package."""
    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise RuntimeError(f'Cannot open MP4: {path}')
        index = 0
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            yield index, index / fps, frame
            index += 1
    finally:
        capture.release()


class PoseLossAfterTrack:
    """Convert pose loss to EXIT only after one successful patient observation."""

    def __init__(self, detector):
        self.detector = detector
        self.tracked = False
        self.last_reason = 'not_started'

    def judge(self, frame):
        try:
            decision = self.detector.judge(frame)
        except JudgmentUnavailable as exc:
            reason = str(exc)
            self.last_reason = reason
            if self.tracked and reason in POSE_LOSS_REASONS:
                self.last_reason = f'tracked_pose_loss_exit:{reason}'
                return True
            raise
        self.tracked = True
        self.last_reason = getattr(getattr(self.detector, 'last', None), 'reason', 'ok')
        return decision

    def close(self):
        close = getattr(self.detector, 'close', None)
        if callable(close):
            close()


def topic_for(base_topic, method):
    return f"{base_topic.rstrip('/')}/{method}"


class FourMethodPublisher(Node):
    def __init__(self, methods, base_topic, final_method=None):
        super().__init__('part5_four_method_mp4_benchmark')
        self.method_publishers = {
            name: self.create_publisher(Bool, topic_for(base_topic, name), 10)
            for name in methods
        }
        self.final_method = final_method
        self.final_publisher = (
            self.create_publisher(Bool, '/patient/bed_exit', 10)
            if final_method else None
        )

    @property
    def recorded_topics(self):
        topics = [publisher.topic_name for publisher in self.method_publishers.values()]
        if self.final_publisher is not None:
            topics.append(self.final_publisher.topic_name)
        return topics

    def publish_state(self, method, value):
        message = Bool(data=value)
        self.method_publishers[method].publish(message)
        if method == self.final_method:
            self.final_publisher.publish(message)


def _start_recorder(node, bag_dir, timeout_s):
    if bag_dir.exists():
        raise FileExistsError(f'Bag output already exists: {bag_dir}')
    command = ['ros2', 'bag', 'record', '-o', str(bag_dir), '--topics', *node.recorded_topics]
    process = subprocess.Popen(command)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'ros2 bag record exited early with code {process.returncode}')
        rclpy.spin_once(node, timeout_sec=0.1)
        if all(node.count_subscribers(topic) > 0 for topic in node.recorded_topics):
            return process
    _stop_recorder(process)
    raise TimeoutError(f'ros2 bag did not subscribe to all topics within {timeout_s}s')


def _stop_recorder(process):
    if process is None or process.poll() is not None:
        return
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait(timeout=5)


def _panel(frame, method, row, width):
    height = max(2, round(frame.shape[0] * width / frame.shape[1]))
    image = cv2.resize(frame, (width, height))
    header = np.full((76, width, 3), 24, np.uint8)
    if not row['valid']:
        color, state = (0, 165, 255), 'NO DECISION'
    elif row['output_bool']:
        color, state = (0, 0, 255), 'EXIT'
    else:
        color, state = (0, 220, 0), 'SAFE'
    lines = [
        f'{method}: {state} raw={row["raw_bool"]}',
        f'frame={row["frame_index"]} {row["judge_ms"]:.1f}ms {row["reason"]}',
    ]
    for index, line in enumerate(lines):
        cv2.putText(header, line[:64], (8, 27 + index * 28),
                    cv2.FONT_HERSHEY_SIMPLEX, .52, color, 1, cv2.LINE_AA)
    return np.vstack([header, image])


def _grid(panels):
    top = np.hstack(panels[:2])
    bottom = np.hstack(panels[2:4])
    return np.vstack([top, bottom])


FRAME_FIELDS = [
    'frame_index', 'media_s', 'method', 'valid', 'raw_bool', 'output_bool',
    'history_size', 'judge_ms', 'reason', 'error',
]

SUMMARY_FIELDS = [
    'method', 'valid_frames', 'invalid_frames', 'valid_rate', 'raw_true',
    'voted_exit_frames', 'judge_p95_ms', 'processing_fps',
]


def run(video, config, output_dir, methods=None, final_method='yolo_pose',
        base_topic='/patient/bed_exit', panel_width=320, show=False,
        pose_loss_after_track=True, bag_ready_timeout=20.0, max_frames=None):
    methods = list(MODEL_FILES) if methods is None else list(methods)
    if len(methods) != 4 or set(methods) != set(MODEL_FILES):
        raise ValueError(f'Exactly these four methods are required: {tuple(MODEL_FILES)}')
    if final_method is not None and final_method not in methods:
        raise ValueError('final_method must be one of the four methods or none')
    if panel_width < 240:
        raise ValueError('panel_width must be at least 240')
    if max_frames is not None and (type(max_frames) is not int or max_frames <= 0):
        raise ValueError('max_frames must be a positive integer or none')

    info = inspect_mp4(video)
    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=False)
    bag_dir = out / 'rosbag'
    record = {
        'status': 'running',
        'source': info,
        'source_sha256': sha256(info['path']),
        'config': str(Path(config).expanduser().resolve()),
        'methods': methods,
        'topics': {name: topic_for(base_topic, name) for name in methods},
        'final_method': final_method,
        'final_topic': '/patient/bed_exit' if final_method else None,
        'pose_loss_after_track': pose_loss_after_track,
        'max_frames': max_frames,
        'bag': str(bag_dir),
    }
    save_json(out / 'run.json', record)

    node = FourMethodPublisher(methods, base_topic, final_method)
    detectors, states, counts = {}, {}, {}
    recorder = writer = None
    rows = []
    frames = 0
    started = time.perf_counter()
    status = 'complete'
    try:
        for name in methods:
            detector = create_method(name, config)
            if pose_loss_after_track and name in ('yolo_pose', 'mediapipe_pose'):
                detector = PoseLossAfterTrack(detector)
            detectors[name] = detector
            states[name] = BedExitState()
            counts[name] = {
                'valid_frames': 0, 'invalid_frames': 0, 'raw_true': 0,
                'voted_exit_frames': 0, 'judge_ms': [],
            }

        recorder = _start_recorder(node, bag_dir, bag_ready_timeout)
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        first_wall = time.perf_counter()
        with (out / 'frames.csv').open('w', newline='', encoding='utf-8') as stream:
            csv_writer = csv.DictWriter(stream, fieldnames=FRAME_FIELDS)
            csv_writer.writeheader()
            for frame_index, media_s, frame in video_frames(info['path'], info['fps']):
                if max_frames is not None and frames >= max_frames:
                    break
                target = first_wall + media_s
                remaining = target - time.perf_counter()
                if remaining > 0:
                    time.sleep(remaining)
                panels = []
                for name in methods:
                    before = time.perf_counter()
                    raw = voted = None
                    error = None
                    try:
                        raw = detectors[name].judge(frame.copy())
                        voted = states[name].update(raw)
                        node.publish_state(name, voted)
                        reason = getattr(detectors[name], 'last_reason', None)
                        if reason is None:
                            reason = getattr(getattr(detectors[name], 'last', None), 'reason', 'ok')
                        valid = True
                    except Exception as exc:
                        states[name] = BedExitState()
                        valid = False
                        reason = str(exc)
                        error = f'{type(exc).__name__}: {exc}'
                    elapsed = (time.perf_counter() - before) * 1000
                    row = {
                        'frame_index': frame_index, 'media_s': media_s, 'method': name,
                        'valid': valid, 'raw_bool': raw, 'output_bool': voted,
                        'history_size': len(states[name].history), 'judge_ms': elapsed,
                        'reason': reason, 'error': error,
                    }
                    csv_writer.writerow(row)
                    rows.append(row)
                    count = counts[name]
                    count['valid_frames'] += int(valid)
                    count['invalid_frames'] += int(not valid)
                    count['raw_true'] += int(raw is True)
                    count['voted_exit_frames'] += int(voted is True)
                    count['judge_ms'].append(elapsed)
                    panels.append(_panel(frame, name, row, panel_width))
                canvas = _grid(panels)
                if writer is None:
                    writer = cv2.VideoWriter(
                        str(out / 'comparison.mp4'), fourcc, info['fps'],
                        (canvas.shape[1], canvas.shape[0]))
                    if not writer.isOpened():
                        raise RuntimeError('Cannot open comparison.mp4 writer')
                writer.write(canvas)
                if show:
                    cv2.imshow('Part 5 Four-Method MP4 Benchmark', canvas)
                    if cv2.waitKey(1) & 0xFF in (ord('q'), 27):
                        status = 'interrupted_by_user'
                        break
                rclpy.spin_once(node, timeout_sec=0)
                frames += 1
    except KeyboardInterrupt:
        status = 'interrupted'
    except Exception as exc:
        status = 'failed'
        record['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        if writer is not None:
            writer.release()
        if show:
            cv2.destroyAllWindows()
        for detector in detectors.values():
            close = getattr(detector, 'close', None)
            if callable(close):
                close()
        _stop_recorder(recorder)
        wall_s = time.perf_counter() - started
        summaries = {}
        with (out / 'comparison.csv').open('w', newline='', encoding='utf-8') as stream:
            csv_writer = csv.DictWriter(stream, fieldnames=SUMMARY_FIELDS)
            csv_writer.writeheader()
            for name, count in counts.items():
                timings = count.pop('judge_ms')
                total = count['valid_frames'] + count['invalid_frames']
                summary = {
                    'method': name, **count,
                    'valid_rate': count['valid_frames'] / total if total else None,
                    'judge_p95_ms': float(np.percentile(timings, 95)) if timings else None,
                    'processing_fps': frames / wall_s if wall_s else None,
                }
                summaries[name] = summary
                csv_writer.writerow(summary)
        record.update(
            status=status, frames=frames, wall_s=wall_s, summary=summaries,
            artifacts={
                'frames': str(out / 'frames.csv'),
                'comparison': str(out / 'comparison.csv'),
                'video': str(out / 'comparison.mp4'),
                'bag': str(bag_dir),
                'run': str(out / 'run.json'),
            },
        )
        save_json(out / 'run.json', record)
        node.destroy_node()
    return record


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description='Run all four methods on one MP4, publish per-method topics, and record one bag.')
    parser.add_argument('--video', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--methods', nargs=4, choices=tuple(MODEL_FILES),
                        default=list(MODEL_FILES))
    parser.add_argument('--final-method', choices=tuple(MODEL_FILES), default='yolo_pose')
    parser.add_argument('--base-topic', default='/patient/bed_exit')
    parser.add_argument('--panel-width', type=int, default=320)
    parser.add_argument('--show', action='store_true')
    parser.add_argument('--pose-loss-after-track', action=argparse.BooleanOptionalAction,
                        default=True)
    parser.add_argument('--bag-ready-timeout', type=float, default=20.0)
    parser.add_argument('--max-frames', type=int, default=None,
                        help='Optional smoke-test frame limit; omit for the full MP4')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    rclpy.init(args=[])
    try:
        record = run(
            args.video, args.config, args.output_dir, methods=args.methods,
            final_method=args.final_method, base_topic=args.base_topic,
            panel_width=args.panel_width, show=args.show,
            pose_loss_after_track=args.pose_loss_after_track,
            bag_ready_timeout=args.bag_ready_timeout,
            max_frames=args.max_frames,
        )
        print(f"status={record['status']} frames={record['frames']} bag={record['bag']}")
        print(record['artifacts']['comparison'])
    finally:
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
