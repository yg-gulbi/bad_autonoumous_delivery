"""Tests for standalone ROS-free method comparison script."""

import cv2
import numpy as np
import pytest

import compare_methods as cm
from part5_bed_exit.bed_exit_method import BedExitMethod
from part5_bed_exit.method_template import BedExitMethodTemplate


def _write_temp_avi(path, frame_count=6):
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*'MJPG'),
        15.0,
        (64, 48),
    )
    assert writer.isOpened()
    for idx in range(frame_count):
        frame = np.full((48, 64, 3), idx * 30, dtype=np.uint8)
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


def test_run_comparison_on_video_isolates_errors_and_counts_disagreement(tmp_path):
    class FixedSeqMethod(BedExitMethod):
        def __init__(self, outputs):
            self._iter = iter(outputs)
            self.closed = False

        def judge(self, frame) -> bool:
            return next(self._iter)

        def close(self) -> None:
            self.closed = True

    video_path = tmp_path / 'sample.avi'
    _write_temp_avi(video_path, frame_count=6)

    method_a = FixedSeqMethod([True, True, True, False, False, True])
    method_b = FixedSeqMethod([True, False, True, True, False, False])
    methods = {
        'mine': method_a,
        'teammate': method_b,
        'unimplemented': BedExitMethodTemplate(),
    }

    runners, raw_disagreements, total_frames = cm.run_comparison(
        source=str(video_path),
        methods=methods,
        selected=['mine', 'teammate'],
        show=False,
    )
    assert total_frames == 6
    assert raw_disagreements == 3  # frames 1, 3, 5 differ
    assert runners[0].valid_frames == 6
    assert runners[0].raw_true == 4
    assert runners[0].voted_unsafe == 2  # frames 5 and 6
    assert runners[1].raw_true == 3
    assert runners[1].voted_unsafe == 1  # frame 5 only
    assert method_a.closed is True
    assert method_b.closed is True

    # Verify a failing method does not break evaluation of a valid peer method.
    peer = FixedSeqMethod([True, False, True])
    runners2, _, total2 = cm.run_comparison(
        source=str(video_path),
        methods={'peer': peer, 'unimplemented': BedExitMethodTemplate()},
        selected=['peer', 'unimplemented'],
        show=False,
        max_frames=3,
    )
    assert total2 == 3
    assert runners2[0].valid_frames == 3
    assert runners2[0].raw_true == 2
    assert runners2[0].error_count == 0
    assert runners2[1].valid_frames == 0
    assert runners2[1].error_count == 3
