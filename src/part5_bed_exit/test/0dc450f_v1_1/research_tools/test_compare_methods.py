"""연구 도구: ROS 없이 여러 판단 메서드를 나란히 비교하는 실행기를 검증한다.

``research/evaluation/compare_methods.py``가 동일 영상 프레임을 여러 메서드에 전달해
판정 차이를 집계하도록 만들었을 때 추가한 테스트다. 임시 AVI와 가짜 판단 객체만
사용하며 최종 ROS 통합시험이나 모델 성능 기록 자체는 아니다.
"""

import cv2
import numpy as np
import pytest

import research.evaluation.compare_methods as cm
from part5_bed_exit.bed_exit_method import BedExitMethod


def _write_temp_avi(path, frame_count=6):
    """밝기가 증가하는 작은 MJPG 임시 영상을 생성한다."""
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
    """비교 대상 선택이 클래스·인스턴스와 이름 필터를 지원하는지 검증한다."""
    class DummyA(BedExitMethod):
        """항상 정상으로 판단하는 추상 기반 가짜 메서드."""

        def judge(self, frame) -> bool:
            """항상 ``False``를 반환한다."""
            return False

    class DummyB:
        """상속 없이 ``judge`` 계약만 만족하는 가짜 메서드."""

        def judge(self, frame) -> bool:
            """항상 ``True``를 반환한다."""
            return True

    runners = cm.select_runners({'a': DummyA, 'b': DummyB()}, selected=['b'])
    assert [r.name for r in runners] == ['b']
    assert isinstance(runners[0].detector, DummyB)


def test_select_runners_rejects_unknown_or_empty():
    """빈 메서드 집합과 존재하지 않는 선택 이름을 거부하는지 검증한다."""
    with pytest.raises(ValueError):
        cm.select_runners({}, None)
    with pytest.raises(KeyError):
        cm.select_runners({'a': object()}, ['missing'])


def test_run_comparison_on_video_isolates_errors_and_counts_disagreement(tmp_path):
    """영상 비교가 오류를 격리하고 원시 판정 불일치를 세는지 검증한다."""
    class FixedSeqMethod(BedExitMethod):
        """고정 판정열을 순서대로 반환하고 종료를 기록하는 가짜 메서드."""

        def __init__(self, outputs):
            """판정 반복자와 종료 플래그를 초기화한다."""
            self._iter = iter(outputs)
            self.closed = False

        def judge(self, frame) -> bool:
            """다음 고정 판정을 반환한다."""
            return next(self._iter)

        def close(self) -> None:
            """종료가 호출됐음을 기록한다."""
            self.closed = True

    class FailingMethod(BedExitMethod):
        """비교 실행기의 프레임별 오류 격리를 검증하는 실패 대역이다."""

        def judge(self, frame) -> bool:
            """모든 프레임에서 의도적으로 판단 실패를 발생시킨다."""
            raise NotImplementedError('test failure')

    video_path = tmp_path / 'sample.avi'
    _write_temp_avi(video_path, frame_count=6)

    method_a = FixedSeqMethod([True, True, True, False, False, True])
    method_b = FixedSeqMethod([True, False, True, True, False, False])
    methods = {
        'mine': method_a,
        'teammate': method_b,
        'unimplemented': FailingMethod(),
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
        methods={'peer': peer, 'unimplemented': FailingMethod()},
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
