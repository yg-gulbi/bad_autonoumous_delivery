import csv
import json
from types import SimpleNamespace

import cv2
import numpy as np

from research.evaluation.video_comparison import run_video_comparison


class FakeMethod:
    def __init__(self):
        self.last = SimpleNamespace(valid=False, reason='not_observed', score=None,
                                    bbox=None, points=[])

    def judge(self, frame):
        result = bool(frame.mean() > 100)
        self.last = SimpleNamespace(valid=True, reason='fake_threshold',
                                    score=float(frame.mean()), bbox=None, points=[])
        return result

    def close(self):
        pass


def test_four_method_video_comparison_writes_result_table(tmp_path):
    video = tmp_path / 'clip.mp4'
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'mp4v'), 10, (32, 32))
    assert writer.isOpened()
    for value in (0, 0, 255, 255, 255, 0):
        writer.write(np.full((32, 32, 3), value, np.uint8))
    writer.release()
    config = tmp_path / 'methods.json'
    config.write_text(json.dumps({
        'model_dir': '.',
        'roi': [[0, 0], [1, 0], [1, 1], [0, 1]],
    }))
    names = ['detection', 'segmentation', 'yolo_pose', 'mediapipe_pose']
    output = tmp_path / 'result'

    record = run_video_comparison(
        video, config, output, methods=names, max_frames=None, panel_width=320,
        factory=lambda name, path: FakeMethod())

    assert record['status'] == 'complete' and record['frames'] == 6
    with (output / 'comparison.csv').open(newline='', encoding='utf-8') as stream:
        rows = list(csv.DictReader(stream))
    assert [row['method'] for row in rows] == names
    assert all(row['valid_frames'] == '6' for row in rows)
    assert all(row['voted_exit_frames'] == '2' for row in rows)
    assert (output / 'comparison.mp4').is_file()
    assert (output / 'frames.csv').is_file()
    assert record['artifacts']['comparison'] == str(output / 'comparison.csv')
