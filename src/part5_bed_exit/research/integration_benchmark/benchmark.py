"""MP4로 개별 모델 또는 4모델 전체 통합 경로를 계측하는 벤치마크.

입력 프레임, 임시 참조 라벨, 5프레임 투표, ROS Bool 발행을 동일하게 사용한다.
출력은 프레임 CSV, 모델 요약 CSV, 비교 영상, run.json, rosbag이다.
"""

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

from part5_bed_exit.bed_exit_method import load_bed_exit_method
from part5_bed_exit.bed_exit_state import BedExitState
from part5_bed_exit.methods.bed_exit_core import JudgmentUnavailable, MODEL_FILES
from research.dataset_download_label.downloads import save_json, sha256
from research.integration_benchmark.metrics import load_timeline, selection_metrics


POSE_LOSS_REASONS = {
    'no_person',
    'patient_unassociated',
    'missing_keypoints',
    'insufficient_torso_keypoints',
    'incomplete_body_coverage',
    'incomplete_torso_coverage',
}


def inspect_mp4(path):
    """OpenCV로 고정 프레임률 MP4의 존재·크기·FPS·프레임 수를 검증한다."""
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
    """선택적 PyAV 없이 OpenCV로 모든 BGR 프레임과 계산 시간을 한 번씩 반환한다."""
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
    """한 번 환자를 본 뒤 발생한 Pose 손실만 이탈로 변환하는 실험 래퍼."""

    def __init__(self, detector):
        """원본 Pose 판단 객체와 추적 성공 여부를 초기화한다."""
        self.detector = detector
        self.tracked = False
        self.last_reason = 'not_started'

    def judge(self, frame):
        """원본 판정을 실행하고 추적 이후의 특정 판단 불가를 이탈로 바꾼다."""
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
        """내부 판단 객체가 제공하는 종료 메서드를 호출한다."""
        close = getattr(self.detector, 'close', None)
        if callable(close):
            close()

    def render(self, frame):
        """실험 래퍼 안의 실제 Pose 메서드가 만든 고유 시각화를 그대로 반환한다."""
        render = getattr(self.detector, 'render', None)
        return render(frame) if callable(render) else frame.copy()


def topic_for(base_topic, method):
    """기본 토픽 뒤에 메서드 이름을 붙인 개별 결과 토픽을 만든다."""
    return f"{base_topic.rstrip('/')}/{method}"


class BenchmarkPublisher(Node):
    """선택된 메서드별 토픽과 최종 결과 토픽을 발행하는 ROS 2 노드."""

    def __init__(self, methods, base_topic, final_method=None):
        """메서드별 발행자와 선택적 최종 발행자를 생성한다."""
        super().__init__('part5_integration_benchmark')
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
        """rosbag에 기록해야 할 모든 실제 토픽 이름을 반환한다."""
        topics = [publisher.topic_name for publisher in self.method_publishers.values()]
        if self.final_publisher is not None:
            topics.append(self.final_publisher.topic_name)
        return topics

    def publish_state(self, method, value):
        """메서드 개별 상태와 필요 시 같은 값을 최종 토픽에 발행한다."""
        message = Bool(data=value)
        self.method_publishers[method].publish(message)
        if method == self.final_method:
            self.final_publisher.publish(message)


def _start_recorder(node, bag_dir, timeout_s):
    """ros2 bag record 프로세스를 시작하고 토픽 구독 준비를 기다린다."""
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
    """실행 중인 rosbag 프로세스에 SIGINT를 보내 정상 종료시킨다."""
    if process is None or process.poll() is not None:
        return
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait(timeout=5)


def _panel(frame, method, row, width):
    """메서드 하나의 영상·판정·처리 시간을 표시하는 패널을 만든다."""
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
    """개별 패널 또는 네 메서드 패널을 영상 한 장으로 결합한다."""
    if len(panels) == 1:
        return panels[0]
    if len(panels) != 4:
        raise ValueError('Comparison video supports one or four panels')
    top = np.hstack(panels[:2])
    bottom = np.hstack(panels[2:4])
    return np.vstack([top, bottom])


FRAME_FIELDS = [
    'frame_index', 'media_s', 'method', 'expected_bool', 'valid', 'raw_bool', 'output_bool',
    'history_size', 'judge_ms', 'reason', 'error',
]

SUMMARY_FIELDS = [
    'method', 'valid_frames', 'invalid_frames', 'valid_rate', 'raw_true',
    'voted_exit_frames', 'recall', 'safe_fpr', 'precision', 'f1', 'coverage',
    'tp', 'fp', 'fn', 'event_detected', 'event_total', 'event_detection_rate',
    'event_latency_mean_s', 'event_latency_p95_s', 'judge_p95_ms', 'processing_fps',
]


def load_benchmark_methods(methods, config, pose_loss_after_track=True):
    """벤치마크 판단 객체를 운영 노드와 동일한 런타임 로더로 생성한다."""
    detectors = {}
    try:
        for name in methods:
            detector = load_bed_exit_method(name, config)
            if pose_loss_after_track and name in ('yolo_pose', 'mediapipe_pose'):
                detector = PoseLossAfterTrack(detector)
            detectors[name] = detector
    except Exception:
        for detector in detectors.values():
            close = getattr(detector, 'close', None)
            if callable(close):
                close()
        raise
    return detectors


def run(video, config, timeline, output_dir, methods=None, final_method='yolo_pose',
        base_topic='/patient/bed_exit', panel_width=320, show=False,
        pose_loss_after_track=True, bag_ready_timeout=20.0, max_frames=None):
    """한 MP4를 개별 모델 또는 네 모델에 전달하고 ROS·파일 결과를 기록한다."""
    methods = list(MODEL_FILES) if methods is None else list(methods)
    if len(methods) not in (1, 4) or not set(methods) <= set(MODEL_FILES):
        raise ValueError('Choose one method or all four supported methods')
    if len(methods) == 4 and set(methods) != set(MODEL_FILES):
        raise ValueError(f'All-mode requires these methods: {tuple(MODEL_FILES)}')
    if final_method is not None and final_method not in methods:
        raise ValueError('final_method must be one of the four methods or none')
    if panel_width < 240:
        raise ValueError('panel_width must be at least 240')
    if max_frames is not None and (type(max_frames) is not int or max_frames <= 0):
        raise ValueError('max_frames must be a positive integer or none')

    info = inspect_mp4(video)
    labels, exit_events, timeline_path = load_timeline(
        timeline, info['reported_frames'])
    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=False)
    bag_dir = out / 'rosbag'
    record = {
        'status': 'running',
        'source': info,
        'source_sha256': sha256(info['path']),
        'config': str(Path(config).expanduser().resolve()),
        'timeline': str(timeline_path),
        'ground_truth': {
            'safe_frames': labels.count(False),
            'exit_frames': labels.count(True),
            'exit_events': len(exit_events),
        },
        'methods': methods,
        'method_loader': 'part5_bed_exit.bed_exit_method.load_bed_exit_method',
        'topics': {name: topic_for(base_topic, name) for name in methods},
        'final_method': final_method,
        'final_topic': '/patient/bed_exit' if final_method else None,
        'pose_loss_after_track': pose_loss_after_track,
        'max_frames': max_frames,
        'bag': str(bag_dir),
    }
    save_json(out / 'run.json', record)

    node = BenchmarkPublisher(methods, base_topic, final_method)
    detectors, states, counts = {}, {}, {}
    recorder = writer = None
    rows = []
    frames = 0
    started = time.perf_counter()
    status = 'complete'
    try:
        detectors = load_benchmark_methods(methods, config, pose_loss_after_track)
        for name in methods:
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
                        'expected_bool': labels[frame_index],
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
                    # 방법론별 고유 근거를 비교 영상에 남긴다.
                    # Detection=BBox, Segmentation=마스크, Pose=모델별 색상의 관절 골격이다.
                    render = getattr(detectors[name], 'render', None)
                    rendered = render(frame) if callable(render) else frame.copy()
                    panels.append(_panel(rendered, name, row, panel_width))
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
                    **selection_metrics(
                        [row for row in rows if row['method'] == name],
                        exit_events, info['fps']),
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
    """개별 모델 또는 4모델 전체 벤치마크의 명령행 인자를 해석한다."""
    parser = argparse.ArgumentParser(
        description='Benchmark one method or all four methods with CSV, video, and rosbag outputs.')
    parser.add_argument('--video', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--timeline', required=True)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--mode', choices=('individual', 'all'), required=True)
    parser.add_argument('--method', choices=tuple(MODEL_FILES))
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
    """ROS를 초기화하고 벤치마크를 실행한 뒤 종료한다."""
    args = parse_args(argv)
    if args.mode == 'individual' and args.method is None:
        raise SystemExit('--method is required in individual mode')
    methods = [args.method] if args.mode == 'individual' else list(MODEL_FILES)
    final_method = args.method if args.mode == 'individual' else args.final_method
    rclpy.init(args=[])
    try:
        record = run(
            args.video, args.config, args.timeline, args.output_dir, methods=methods,
            final_method=final_method, base_topic=args.base_topic,
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
