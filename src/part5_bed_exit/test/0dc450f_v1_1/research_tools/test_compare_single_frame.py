"""연구 도구: 단일 이미지·프레임 모델 비교기의 입출력을 검증한다.

``research/evaluation/single_frame_comparison.py``로 Detection·Segmentation 결과와 ROI·마스크
프리뷰를 빠르게 확인하기 위해 만든 테스트다. ROI JSON, 결과 이미지 저장과 오류
격리를 가짜 메서드로 확인하며 5프레임 투표·ROS 발행·최종 성능평가는 다루지 않는다.
"""

import json

import cv2
import numpy as np
import pytest

import research.evaluation.single_frame_comparison as cm
from part5_bed_exit.bed_exit_method import BedExitMethod
from part5_bed_exit.method_template import BedExitMethodTemplate


def _write_temp_avi(path, frame_count=4):
    """밝기가 증가하는 작은 MJPG 임시 영상을 생성한다."""
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
    """단일 프레임 실행기가 클래스·인스턴스와 필터를 지원하는지 검증한다."""
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
    """빈 메서드 집합과 잘못된 선택 이름을 거부하는지 검증한다."""
    with pytest.raises(ValueError):
        cm.select_runners({}, None)
    with pytest.raises(KeyError):
        cm.select_runners({'a': BedExitMethodTemplate()}, ['missing'])


def test_run_comparison_on_images_saves_per_method_outputs_and_loads_sidecar_roi(tmp_path):
    """이미지 비교가 사이드카 ROI를 읽고 메서드별 결과를 저장하는지 검증한다."""
    class RecordingMethod(BedExitMethod):
        """적용된 ROI와 종료 호출을 기록하는 고정 판정 메서드."""

        def __init__(self, decision):
            """고정 판정과 기록용 상태를 초기화한다."""
            self.decision = decision
            self.seen_rois = []
            self.closed = False

        def reset(self, roi_config=None):
            """샘플마다 전달된 ROI를 기록한다."""
            self.seen_rois.append(roi_config)

        def judge(self, frame) -> bool:
            """설정된 고정 판정을 반환한다."""
            return self.decision

        def render(self, frame):
            """결과 저장 확인용 초록 원을 그린 영상 복사본을 반환한다."""
            out = frame.copy()
            cv2.circle(out, (10, 10), 4, (0, 255, 0), -1)
            return out

        def close(self) -> None:
            """종료가 호출됐음을 기록한다."""
            self.closed = True

    img_dir = tmp_path / 'images'
    img_dir.mkdir()
    cv2.imwrite(str(img_dir / '01_a.png'), np.zeros((48, 64, 3), dtype=np.uint8))
    cv2.imwrite(str(img_dir / '02_b.png'), np.full((48, 64, 3), 120, dtype=np.uint8))
    (img_dir / '02_b.roi.json').write_text(
        json.dumps({'bed_polygon': [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]}),
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
    assert not np.array_equal(m_det.seen_rois[0].vertices, m_det.seen_rois[1].vertices)
    assert m_det.closed and m_seg.closed


def test_run_comparison_on_video_isolates_errors_without_voting(tmp_path):
    """영상 단일 프레임 비교가 투표 없이 메서드 오류를 격리하는지 검증한다."""
    class FixedSeqMethod(BedExitMethod):
        """고정 판정열을 순서대로 반환하는 가짜 메서드."""

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
