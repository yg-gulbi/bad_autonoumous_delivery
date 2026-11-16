"""Tests for single-frame image/video comparison and per-method image saving."""

import json

import cv2
import numpy as np
import pytest

import compare_single_frame as cm
from part5_bed_exit.bed_exit_method import BedExitMethod
from part5_bed_exit.method_template import BedExitMethodTemplate


def _write_temp_avi(path, frame_count=4):
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*'MJPG'),
        15.0,
        (64, 48),
    )
    assert writer.isOpened()
    for idx in range(frame_count):
        frame = np.full((48, 64, 3), idx * 40, dtype=np.uint8)
        writer.write(frame)
    writer.release()


def test_select_runners_supports_class_instance_and_filtering():
    class DummyA(BedExitMethod):
        def judge(self, frame) -> bool:
            return False

    class DummyB:
        def judge(self, frame) -> bool:
            return True

    runners = cm.select_runners({'a': DummyA, 'b': DummyB()}, selected=['b'])
    assert [r.name for r in runners] == ['b']
    assert isinstance(runners[0].detector, DummyB)


def test_select_runners_rejects_unknown_or_empty():
    with pytest.raises(ValueError):
        cm.select_runners({}, None)
    with pytest.raises(KeyError):
        cm.select_runners({'a': BedExitMethodTemplate()}, ['missing'])


def test_run_comparison_on_images_saves_per_method_outputs_and_loads_sidecar_roi(tmp_path):
    class RecordingMethod(BedExitMethod):
        def __init__(self, decision):
            self.decision = decision
            self.seen_rois = []
            self.closed = False

        def reset(self, roi_config=None):
            self.seen_rois.append(roi_config)

        def judge(self, frame) -> bool:
            return self.decision

        def render(self, frame):
            out = frame.copy()
            cv2.circle(out, (10, 10), 4, (0, 255, 0), -1)
            return out

        def close(self) -> None:
            self.closed = True

    img_dir = tmp_path / 'images'
    img_dir.mkdir()
    cv2.imwrite(str(img_dir / '01_a.png'), np.zeros((48, 64, 3), dtype=np.uint8))
    cv2.imwrite(str(img_dir / '02_b.png'), np.full((48, 64, 3), 120, dtype=np.uint8))
    (img_dir / '02_b.roi.json').write_text(
        json.dumps({'bed_polygon': [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]}),
        encoding='utf-8',
    )

    out_dir = tmp_path / 'outputs'
    m_det = RecordingMethod(False)
    m_seg = RecordingMethod(True)
    runners, disagreements, total = cm.run_comparison(
        source=str(img_dir),
        methods={'det': m_det, 'seg': m_seg},
        selected=['det', 'seg'],
        output_dir=out_dir,
        show=False,
    )

    assert total == 2
    assert disagreements == 2
    assert runners[0].safe_count == 2
    assert runners[0].exit_count == 0
    assert runners[1].exit_count == 2
    assert runners[1].safe_count == 0
    assert (out_dir / 'det' / '01_a.png').is_file()
    assert (out_dir / 'det' / '02_b.png').is_file()
    assert (out_dir / 'seg' / '01_a.png').is_file()
    assert (out_dir / 'seg' / '02_b.png').is_file()
    assert not np.array_equal(m_det.seen_rois[0].polygon, m_det.seen_rois[1].polygon)
    assert m_det.closed and m_seg.closed


def test_run_comparison_on_video_isolates_errors_without_voting(tmp_path):
    class FixedSeqMethod(BedExitMethod):
        def __init__(self, outputs):
            self._iter = iter(outputs)
            self.closed = False

        def judge(self, frame) -> bool:
            return next(self._iter)

        def close(self) -> None:
            self.closed = True

    video_path = tmp_path / 'sample.avi'
    _write_temp_avi(video_path, frame_count=4)

    peer = FixedSeqMethod([True, False, True, False])
    runners, disagreements, total = cm.run_comparison(
        source=str(video_path),
        methods={'peer': peer, 'unimplemented': BedExitMethodTemplate()},
        selected=['peer', 'unimplemented'],
        output_dir=None,
        show=False,
    )
    assert total == 4
    assert disagreements == 0  # peer error count 0, unimplemented error count 4 -> not comparable
    assert runners[0].valid_frames == 4
    assert runners[0].exit_count == 2
    assert runners[0].safe_count == 2
    assert runners[0].error_count == 0
    assert runners[1].valid_frames == 0
    assert runners[1].error_count == 4
    assert peer.closed is True
