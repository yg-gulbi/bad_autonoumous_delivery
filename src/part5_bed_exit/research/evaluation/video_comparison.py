"""Stream one MP4 through replaceable raw judges and the shared 3/5 gate."""

from __future__ import annotations

import csv
from fractions import Fraction
import math
from pathlib import Path
import time

import av
import cv2
import numpy as np

from research.datasets.downloads import save_json, sha256
from research.evaluation.evaluation import video_frames
from part5_bed_exit.methods.bed_exit_core import MODEL_FILES
from part5_bed_exit.pretrained import create_method, read_config
from research.evaluation.monitoring import ConsecutiveExitAnalysis, EventGate


def inspect_mp4(path):
    """Read metadata and the first BGR frame; frame zero is not a test label."""
    path = Path(path).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != '.mp4':
        raise ValueError(f'실제 MP4 파일을 선택하세요: {path}')
    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError('MP4에 영상 스트림이 없습니다.')
        stream = container.streams.video[0]
        first = next(container.decode(stream), None)
        if first is None:
            raise ValueError('MP4의 첫 프레임을 읽을 수 없습니다.')
        rate = stream.average_rate or stream.guessed_rate or Fraction(30)
        if rate <= 0:
            raise ValueError('잘못된 영상 FPS입니다.')
        duration = (float(stream.duration * stream.time_base)
                    if stream.duration is not None and stream.time_base is not None else None)
        info = {'path': str(path), 'width': stream.codec_context.width,
                'height': stream.codec_context.height, 'fps': float(rate),
                'rate': str(rate), 'duration_s': duration, 'reported_frames': stream.frames,
                'bytes': path.stat().st_size}
        return info, first.to_ndarray(format='bgr24')


def _panel(frame, detector, row, roi, width):
    height = max(2, round(frame.shape[0] * width / frame.shape[1]))
    image = cv2.resize(frame, (width, height))
    cv2.polylines(image, [np.rint(roi.pixels(image.shape)).astype(np.int32)],
                  True, (0, 220, 0), 2)
    last = getattr(detector, 'last', None)
    if row['valid'] and last is not None:
        scale = np.array([width / frame.shape[1], height / frame.shape[0]])
        if last.bbox is not None:
            box = np.asarray(last.bbox).reshape(2, 2) * scale
            a, b = np.rint(box).astype(int)
            cv2.rectangle(image, tuple(a), tuple(b), (255, 200, 50), 2)
        for x, y, confidence in last.points:
            if confidence >= .5 and np.isfinite([x, y]).all():
                cv2.circle(image, tuple(np.rint(np.array([x, y]) * scale).astype(int)),
                           2, (0, 180, 255), -1)
    # A separate header keeps labels readable even on portrait or small clips.
    header = np.full((90, width, 3), 24, np.uint8)
    color = (0, 165, 255) if not row['valid'] else ((60, 70, 255) if row['output_bool'] else (60, 230, 90))
    status = row['state'] if row['valid'] else 'NO DECISION'
    if row['warmup']:
        status += f" (warmup {row['history_size']}/5)"
    lines = [f"{row['method']} | {status}",
             f"frame={row['frame_index']} t={row['media_s']:.3f}s raw={row['raw_bool']} event={row['event']}",
             f"{row['judge_ms']:.1f}ms | {row['reason']}"]
    for i, line in enumerate(lines):
        cv2.putText(header, line[:85], (10, 24 + i * 27), cv2.FONT_HERSHEY_SIMPLEX,
                    .50, color, 1, cv2.LINE_AA)
    return np.vstack([header, image])


def _grid(panels):
    if len(panels) == 1:
        canvas = panels[0]
    else:
        blank = np.zeros_like(panels[0])
        cells = panels + [blank] * (math.ceil(len(panels) / 2) * 2 - len(panels))
        canvas = np.vstack([np.hstack(cells[i:i + 2]) for i in range(0, len(cells), 2)])
    # H.264 yuv420p requires even dimensions.
    return cv2.copyMakeBorder(canvas, 0, canvas.shape[0] % 2, 0, canvas.shape[1] % 2,
                              cv2.BORDER_CONSTANT, value=(0, 0, 0))


CSV_FIELDS = ['frame_index', 'media_s', 'method', 'valid', 'raw_bool', 'output_bool',
              'state', 'warmup', 'history_size', 'event', 'three_consecutive_exit',
              'judge_ms', 'score', 'reason', 'error']


def run_video_comparison(video_path, config_path, output_dir, methods=None,
                         max_frames=300, panel_width=640, progress=None, factory=None):
    """Decode each source frame once; give every method its own frame and gate.

    Results are a silent H.264 MP4, streaming frame CSV and summary JSON. PTS
    are retained (90 kHz output time base). This comparison shares compute
    between methods and does not measure event accuracy without reviewed GT.
    """
    if max_frames is not None and (type(max_frames) is not int or max_frames <= 0):
        raise ValueError('max_frames는 양의 정수 또는 None이어야 합니다.')
    if type(panel_width) is not int or panel_width < 320:
        raise ValueError('panel_width는 320 이상이어야 합니다.')
    methods = list(MODEL_FILES) if methods is None else list(methods)
    if not methods or len(set(methods)) != len(methods) or any(n not in MODEL_FILES for n in methods):
        raise ValueError('네 메서드 중 중복 없이 하나 이상 선택하세요.')
    info, _ = inspect_mp4(video_path)
    config, roi, _ = read_config(config_path)
    out = Path(output_dir).expanduser().resolve()
    # Refuse reuse so an interrupted run cannot overwrite a prior result.
    out.mkdir(parents=True, exist_ok=False)
    record = {'status': 'running', 'source': info, 'source_sha256': sha256(info['path']),
              'config': config, 'methods': methods, 'max_frames': max_frames,
              'timing': 'original source PTS, every decoded frame; shared sequential compute',
              'audio': 'result video has no audio; input is unchanged', 'frames': 0}
    save_json(out / 'run.json', record)
    detectors, gates, analyses, counts = {}, {}, {}, {}
    writer = encoder = iterator = None
    frames = 0
    status = 'complete'
    started = None
    close_errors = []
    try:
        for name in methods:
            detectors[name] = (factory or create_method)(name, config_path)
            gates[name] = EventGate()
            analyses[name] = ConsecutiveExitAnalysis()
            counts[name] = {'valid_frames': 0, 'invalid_frames': 0, 'raw_true': 0,
                            'voted_exit_frames': 0, 'events': 0, 'judge_ms': []}
        writer = av.open(str(out / 'comparison.mp4'), mode='w')
        started = time.perf_counter()
        iterator = iter(video_frames(info['path']))
        with (out / 'frames.csv').open('w', newline='', encoding='utf-8') as csv_file:
            csv_writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
            csv_writer.writeheader()
            while max_frames is None or frames < max_frames:
                try:
                    index, media_s, frame = next(iterator)
                except StopIteration:
                    break
                panels = []
                for name in methods:
                    detector, gate, analysis = detectors[name], gates[name], analyses[name]
                    before = time.perf_counter()
                    raw = score = error = None
                    try:
                        # Prevent a custom method from changing a peer's input.
                        raw = detector.judge(frame.copy())
                        if type(raw) is not bool:
                            raise TypeError('judge(frame) must return a Python bool')
                        last = getattr(detector, 'last', None)
                        if last is not None and not last.valid:
                            raise RuntimeError(last.reason)
                        voted = gate.update(raw)
                        three_consecutive = analysis.update(raw)
                        reason = getattr(last, 'reason', 'ok')
                        score = getattr(last, 'score', None)
                    except Exception as exc:
                        raw = None
                        voted = gate.update(None, valid=False)
                        three_consecutive = analysis.update(None, valid=False)
                        reason = str(exc)
                        error = f'{type(exc).__name__}: {exc}'
                    elapsed = (time.perf_counter() - before) * 1000
                    history = len(gate.vote.history)
                    row = {'frame_index': index, 'media_s': media_s, 'method': name,
                           'valid': voted.valid, 'raw_bool': raw,
                           'output_bool': (voted.state.value == 'EXIT') if voted.valid else None,
                           'state': voted.state.value if voted.valid else None,
                           'warmup': voted.valid and history < 5, 'history_size': history,
                           'event': voted.event, 'three_consecutive_exit': three_consecutive,
                           'judge_ms': elapsed, 'score': score,
                           'reason': reason, 'error': error}
                    csv_writer.writerow(row)
                    count = counts[name]
                    count['valid_frames'] += int(voted.valid)
                    count['invalid_frames'] += int(not voted.valid)
                    count['raw_true'] += int(raw is True)
                    count['voted_exit_frames'] += int(row['output_bool'] is True)
                    count['events'] += int(voted.event)
                    count['judge_ms'].append(elapsed)
                    panels.append(_panel(frame, detector, row, roi, panel_width))
                canvas = _grid(panels)
                if encoder is None:
                    encoder = writer.add_stream('libx264', rate=Fraction(info['rate']))
                    encoder.width, encoder.height = canvas.shape[1], canvas.shape[0]
                    encoder.pix_fmt = 'yuv420p'
                    encoder.time_base = Fraction(1, 90000)
                    encoder.codec_context.time_base = Fraction(1, 90000)
                    encoder.options = {'preset': 'veryfast', 'crf': '23'}
                output_frame = av.VideoFrame.from_ndarray(canvas, format='bgr24')
                output_frame.time_base = Fraction(1, 90000)
                output_frame.pts = round(media_s * 90000)
                for packet in encoder.encode(output_frame):
                    writer.mux(packet)
                frames += 1
                if progress is not None and (frames == 1 or frames % 10 == 0):
                    progress(frames, media_s, canvas)
        if not frames:
            raise ValueError('디코드 가능한 프레임이 없습니다.')
    except KeyboardInterrupt:
        status = 'interrupted'
        raise
    except Exception as exc:
        status = 'failed'
        record['error'] = f'{type(exc).__name__}: {exc}'
        # Source decode errors end the replay; they never generate a SAFE row.
        for gate in gates.values():
            gate.update(None, valid=False)
        raise
    finally:
        if iterator is not None:
            try:
                close_iterator = getattr(iterator, 'close', None)
                if callable(close_iterator):
                    close_iterator()
            except Exception as exc:
                close_errors.append(f'Decoder close: {type(exc).__name__}: {exc}')
                status = 'failed'
        try:
            if writer is not None:
                if encoder is not None:
                    for packet in encoder.encode():
                        writer.mux(packet)
                writer.close()
        except Exception as exc:
            close_errors.append(f'Video finalize: {type(exc).__name__}: {exc}')
            status = 'failed'
            if writer is not None:
                try:
                    writer.close()
                except Exception as close_exc:
                    close_errors.append(f'Writer close: {type(close_exc).__name__}: {close_exc}')
        for name, detector in detectors.items():
            try:
                close = getattr(detector, 'close', None)
                if callable(close):
                    close()
            except Exception as exc:
                close_errors.append(f'{name}: {type(exc).__name__}: {exc}')
                status = 'failed'
        wall_s = time.perf_counter() - started if started is not None else None
        record.update(status=status, frames=frames, wall_s=wall_s,
                      processing_fps=frames / wall_s if wall_s else None,
                      close_errors=close_errors)
        record['summary'] = {}
        for name, count in counts.items():
            times = count.pop('judge_ms')
            record['summary'][name] = dict(count, judge_p95_ms=float(np.percentile(times, 95)) if times else None)
        record['stopped_at_frame_limit'] = max_frames is not None and frames == max_frames
        record['artifacts'] = {'video': str(out / 'comparison.mp4'),
                               'frames': str(out / 'frames.csv'), 'summary': str(out / 'run.json')}
        save_json(out / 'run.json', record)
    if close_errors:
        raise RuntimeError('; '.join(close_errors))
    return record
