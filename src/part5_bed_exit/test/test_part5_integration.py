"""Verify raw strategies, strict votes, and ROS publication at the boundary."""

import importlib.util
from itertools import product
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from part5_bed_exit.bed_exit_method import load_bed_exit_method
from part5_bed_exit.bed_exit_state import BedExitState
from part5_bed_exit.methods.bed_exit_core import (
    Candidate, DetectionROI, EventGate, JudgmentUnavailable, PolygonROI, State,
)
from part5_bed_exit.pretrained import read_config
from part5_bed_exit.downloads import extract_zip


def test_offline_gate_matches_ros_state_for_all_windows_and_expiration():
    for sequence in product((False, True), repeat=5):
        state, gate = BedExitState(), EventGate()
        for raw in sequence + (False, True, False, True, True):
            result = gate.update(raw)
            assert (result.state == State.EXIT) is state.update(raw)


def test_invalid_observation_resets_vote_but_cannot_rearm_an_event():
    gate = EventGate()
    assert [gate.update(True).event for _ in range(5)] == [False]*4 + [True]
    assert not gate.update(None, valid=False).valid
    recovered = [gate.update(True) for _ in range(5)]
    assert [r.state for r in recovered] == [State.SAFE]*4 + [State.EXIT]
    assert not any(r.event for r in recovered)
    for _ in range(5):
        gate.update(False)
    assert [gate.update(True).event for _ in range(3)] == [False, False, True]


def test_no_person_is_unavailable_instead_of_safe():
    roi = PolygonROI([[0, 0], [1, 0], [1, 1], [0, 1]])
    method = DetectionROI(roi, roi, 'unused', model=object())
    method.candidates = lambda frame: []
    with pytest.raises(JudgmentUnavailable, match='no_person'):
        method.judge(np.zeros((32, 32, 3), np.uint8))
    assert not method.last.valid


def test_factory_uses_config_relative_model_paths_and_existing_loader(tmp_path, monkeypatch):
    config = {'roi_reviewed': True, 'roi_version': 'test', 'model_dir': '../models',
              'bed_roi': [[0, 0], [1, 0], [1, 1], [0, 1]],
              'safe_roi': [[.1, .1], [.9, .1], [.9, .9], [.1, .9]]}
    path = tmp_path / 'config' / 'methods.json'
    path.parent.mkdir()
    path.write_text(json.dumps(config))
    assert read_config(path)[3] == tmp_path / 'models'
    import part5_bed_exit.pretrained as pretrained
    calls = []
    def factory(name, bed, safe, models, **kwargs):
        calls.append((name, models))
        return DetectionROI(bed, safe, 'unused', model=object())
    monkeypatch.setattr(pretrained, 'make_method', factory)
    for name in ('detection', 'segmentation', 'yolo_pose', 'mediapipe_pose'):
        assert callable(load_bed_exit_method(name, str(path)).judge)
    assert [name for name, _ in calls] == ['detection', 'segmentation', 'yolo_pose', 'mediapipe_pose']
    config['roi_reviewed'] = False
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match='roi_reviewed'):
        read_config(path)
    with pytest.raises(ValueError, match='method_config'):
        load_bed_exit_method('detection')


def test_ros_boundary_skips_camera_exceptions_and_bad_returns_then_rewarms(monkeypatch):
    # Exercise the real callback without requiring ROS discovery or a webcam.
    class Bool:
        def __init__(self, data):
            self.data = data
    monkeypatch.setitem(sys.modules, 'rclpy', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'rclpy.node', SimpleNamespace(Node=object))
    monkeypatch.setitem(sys.modules, 'std_msgs.msg', SimpleNamespace(Bool=Bool))
    path = Path(__file__).parents[1] / 'part5_bed_exit' / 'bed_exit_node.py'
    spec = importlib.util.spec_from_file_location('_bed_exit_callback_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    frame = np.zeros((8, 8, 3), np.uint8)
    capture = SimpleNamespace(read=lambda: (True, frame))
    published, errors = [], []
    detector = SimpleNamespace(judge=lambda frame: True)
    node = SimpleNamespace(capture=capture, detector=detector, state=BedExitState(),
                           publisher=SimpleNamespace(publish=lambda msg: published.append(msg.data)),
                           get_logger=lambda: SimpleNamespace(error=errors.append))
    process = lambda: module.BedExitNode.process_frame(node)
    for _ in range(5):
        process()
    assert published == [False]*4 + [True]
    def broken_read():
        raise RuntimeError('camera disconnected')
    capture.read = broken_read
    process()
    assert len(published) == 5 and not node.state.history
    capture.read = lambda: (True, frame)
    detector.judge = lambda frame: np.bool_(True)
    process()
    assert len(published) == 5 and not node.state.history
    detector.judge = lambda frame: (_ for _ in ()).throw(JudgmentUnavailable('no_person'))
    process()
    assert len(published) == 5 and not node.state.history
    detector.judge = lambda frame: True
    for _ in range(5):
        process()
    assert published[5:] == [False]*4 + [True]
    assert len(errors) == 3


def test_notebook_cells_compile_after_interactive_execution():
    path = Path(__file__).parents[1] / 'notebooks' / 'part5_bed_exit.ipynb'
    notebook = json.loads(path.read_text())
    assert notebook['nbformat'] == 4
    for index, cell in enumerate(notebook['cells']):
        if cell['cell_type'] == 'code':
            compile(''.join(cell['source']), f'notebook-cell-{index}', 'exec')


def test_dataset_zip_extraction_rejects_escape_paths_and_size_overrun(tmp_path):
    import zipfile
    archive = tmp_path / 'data.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('../escape.txt', 'outside')
    with pytest.raises(ValueError, match='Unsafe'):
        extract_zip(archive, tmp_path / 'output')
    assert not (tmp_path / 'escape.txt').exists()
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('frame.txt', '12345')
    with pytest.raises(ValueError, match='size budget'):
        extract_zip(archive, tmp_path / 'output', max_bytes=4)


def test_benchmark_errors_are_empty_decisions_and_reset_the_five_frame_window(tmp_path, monkeypatch):
    import part5_bed_exit.evaluation as evaluation
    class Method:
        def reset(self):
            self.last = SimpleNamespace(valid=False, score=None, reason='not_observed')
        def judge(self, frame):
            if frame[0, 0, 0] == 2:
                raise RuntimeError('backend failed')
            self.last = SimpleNamespace(valid=True, score=1.0, reason='test')
            return True
    def frames(path):
        for i in range(10):
            yield i, i / 10, np.full((8, 8, 3), i, np.uint8)
    monkeypatch.setattr(evaluation, 'video_frames', frames)
    clip = {'video': 'unused.mp4', 'duration_s': 1.0, 'clip_id': 'logic',
            'events': [{'event_id': 'e1', 'risk_start_s': .3, 'boundary_cross_s': .9, 'end_s': 1.0}],
            'patient_present_intervals_s': [[0, 1]]}
    summary, rows = evaluation.benchmark_clip(Method(), clip, tmp_path, tmp_path / 'out', warmup=0)
    assert rows[2]['raw_exit'] is None and rows[2]['state'] is None
    assert not rows[2]['valid'] and 'backend failed' in rows[2]['error']
    assert [r['event'] for r in rows[:8]] == [False]*7 + [True]
    assert summary['invalid_frames'] == 1
    assert summary['tracking_lost_s'] == pytest.approx(.1)
