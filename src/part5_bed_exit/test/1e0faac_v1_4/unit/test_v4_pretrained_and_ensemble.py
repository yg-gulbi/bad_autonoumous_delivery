"""목적: v1.4의 가중치 선택·프레임/ROI 회전·파라미터 오버라이드·다중 모델 앙상블을 검증한다."""

from pathlib import Path

import numpy as np
import pytest

from part5_bed_exit import bed_exit_method, pretrained
from part5_bed_exit.methods.bed_exit_core import JudgmentUnavailable, PolygonROI


class _Method:
    def judge(self, frame):
        return False


def test_resolve_weights_path_supports_string_absolute_and_mapping():
    model_dir = Path('/models')

    assert pretrained.resolve_weights_path(
        {'method': 'segmentation'}, 'segmentation', model_dir
    ) == Path('/models/yolo11n-seg.pt')

    assert pretrained.resolve_weights_path(
        {'method': 'segmentation', 'weights': 'yolo11l-seg.pt'},
        'segmentation',
        model_dir,
    ) == Path('/models/yolo11l-seg.pt')

    assert pretrained.resolve_weights_path(
        {'method': 'segmentation', 'weights': '/custom/best.pt'},
        'segmentation',
        model_dir,
    ) == Path('/custom/best.pt')

    assert pretrained.resolve_weights_path(
        {'method': 'segmentation', 'weights': 'yolo11l-seg.pt'},
        'detection',
        model_dir,
        explicit_method='detection',
    ) == Path('/models/yolo11n.pt')

    cfg = {
        'method': 'segmentation',
        'weights': {'segmentation': 'custom_seg.pt', 'detection': '/abs/det.pt'},
    }
    assert pretrained.resolve_weights_path(cfg, 'segmentation', model_dir) == Path(
        '/models/custom_seg.pt'
    )
    assert pretrained.resolve_weights_path(
        cfg, 'detection', model_dir, explicit_method='detection'
    ) == Path('/abs/det.pt')
    assert pretrained.resolve_weights_path(
        cfg, 'yolo_pose', model_dir, explicit_method='yolo_pose'
    ) == Path('/models/yolo11n-pose.pt')

    with pytest.raises(ValueError, match='weights'):
        pretrained.resolve_weights_path(
            {'method': 'segmentation', 'weights': 123}, 'segmentation', model_dir
        )


def test_create_method_passes_custom_weights_from_config(monkeypatch):
    roi = object()
    calls = []
    monkeypatch.setattr(
        pretrained,
        'read_config',
        lambda path: (
            {
                'method': 'segmentation',
                'weights': 'custom_person_seg.pt',
                'device': 'cuda:0',
            },
            roi,
            Path('/models'),
        ),
    )

    def create_segmentation(selected_roi, weights, **kwargs):
        calls.append((selected_roi, weights, kwargs))
        return 'custom-seg'

    monkeypatch.setattr(pretrained, 'SegmentationROI', create_segmentation)

    assert pretrained.create_method('methods.json') == 'custom-seg'
    assert calls[0][1] == Path('/models/custom_person_seg.pt')
    assert calls[0][2]['device'] == 'cuda:0'


def test_rotate_frame_and_roi_ccw_align_all_four_angles():
    roi = PolygonROI([[0.1, 0.1], [0.3, 0.1], [0.3, 0.4], [0.1, 0.4]])
    frame = np.zeros((40, 80, 3), dtype=np.uint8)
    frame[10, 16] = (255, 255, 255)
    frame[30, 65] = (0, 0, 255)

    for angle in (0, 90, 180, 270):
        rot_frame = pretrained.rotate_frame_ccw(frame, angle)
        rot_roi = pretrained.rotate_roi_ccw(roi, angle)

        in_ys, in_xs = np.where((rot_frame == (255, 255, 255)).all(axis=2))
        out_ys, out_xs = np.where((rot_frame == (0, 0, 255)).all(axis=2))
        assert len(in_xs) == 1 and len(out_xs) == 1
        assert rot_roi.contains((int(in_xs[0]), int(in_ys[0])), rot_frame.shape)
        assert not rot_roi.contains((int(out_xs[0]), int(out_ys[0])), rot_frame.shape)

    for bad_angle in (45, -90, 360, True):
        with pytest.raises(ValueError, match='frame_rotate_ccw'):
            pretrained.rotate_frame_ccw(frame, bad_angle)


def test_create_method_applies_json_and_override_precedence_for_all_methods(monkeypatch):
    roi = PolygonROI([[0.1, 0.2], [0.4, 0.2], [0.4, 0.5], [0.1, 0.5]])
    calls = []

    monkeypatch.setattr(
        pretrained,
        'read_config',
        lambda path: (
            {
                'method': 'segmentation',
                'device': 'cpu',
                'weights': 'from_json.pt',
                'yolo_confidence': 0.4,
                'outside_fraction': 0.08,
                'mask_threshold': 0.6,
                'selector_min_iou': 0.2,
            },
            roi,
            Path('/models'),
        ),
    )

    def capture(name):
        def _ctor(selected_roi, weights, **kwargs):
            calls.append((name, selected_roi, weights, kwargs))
            return name
        return _ctor

    monkeypatch.setattr(pretrained, 'SegmentationROI', capture('segmentation'))
    monkeypatch.setattr(pretrained, 'YoloPoseROI', capture('yolo_pose'))
    monkeypatch.setattr(pretrained, 'MediaPipePoseROI', capture('mediapipe_pose'))

    assert pretrained.create_method(
        'methods.json',
        overrides={
            'weights': 'override.pt',
            'device': 'cuda:0',
            'yolo_confidence': 0.3,
            'yolo_imgsz': 512,
            'selector_init_overlap': 0.35,
            'selector_ambiguity_gap': 0.1,
            'frame_rotate_ccw': 90,
        },
    ) == 'segmentation'
    _, seg_roi, seg_weights, seg_kwargs = calls[-1]
    assert seg_weights == Path('/models/override.pt')
    assert seg_roi is not roi
    assert seg_kwargs == {
        'device': 'cuda:0',
        'confidence': 0.3,
        'outside_fraction': 0.08,
        'mask_threshold': 0.6,
        'imgsz': 512,
        'min_iou': 0.2,
        'init_overlap': 0.35,
        'ambiguity_gap': 0.1,
    }

    pretrained.create_method(
        'methods.json',
        overrides={'frame_rotate_ccw': 90, 'roi_already_rotated': True},
    )
    assert calls[-1][1] is roi

    pretrained.create_method(
        'methods.json',
        method_name='yolo_pose',
        overrides={
            'pose_policy': 'torso',
            'pose_point_conf': 0.35,
            'pose_tolerance_px': 6.0,
            'yolo_imgsz': 480,
        },
    )
    assert calls[-1][3]['policy'] == 'torso'
    assert calls[-1][3]['point_conf'] == 0.35
    assert calls[-1][3]['tolerance_px'] == 6.0
    assert calls[-1][3]['imgsz'] == 480

    pretrained.create_method(
        'methods.json',
        method_name='mediapipe_pose',
        overrides={
            'pose_policy': 'torso',
            'pose_point_conf': 0.4,
            'pose_tolerance_px': 4.5,
            'mediapipe_num_poses': 2,
            'mediapipe_min_detection_conf': 0.6,
            'mediapipe_min_presence_conf': 0.7,
        },
    )
    assert calls[-1][3] == {
        'min_iou': 0.2,
        'policy': 'torso',
        'point_conf': 0.4,
        'tolerance_px': 4.5,
        'num_poses': 2,
        'min_pose_detection_confidence': 0.6,
        'min_pose_presence_confidence': 0.7,
    }

    with pytest.raises(ValueError, match='Unknown model override'):
        pretrained.create_method('methods.json', overrides={'nonexistent': 1})


def test_ensemble_bed_exit_method_policies_unavailable_and_lifecycle():
    class _Member:
        def __init__(self, outcome):
            self.outcome = outcome
            self.reset_called = 0
            self.closed = False

        def judge(self, frame):
            if isinstance(self.outcome, Exception):
                raise self.outcome
            return self.outcome

        def reset(self):
            self.reset_called += 1

        def close(self):
            self.closed = True

    m1, m2, m3 = _Member(True), _Member(False), _Member(True)
    ens_maj = pretrained.EnsembleBedExitMethod([m1, m2, m3], policy='majority')
    assert ens_maj.judge(None) is True
    assert ens_maj.last_votes == [True, False, True]

    assert pretrained.EnsembleBedExitMethod([m1, m2, m3], policy='all').judge(None) is False
    assert pretrained.EnsembleBedExitMethod([m2, _Member(False), m1], policy='any').judge(None) is True
    assert pretrained.EnsembleBedExitMethod([m1, m2, m3], min_votes=3).judge(None) is False
    assert pretrained.EnsembleBedExitMethod([m1, m2, m3], min_votes=2).judge(None) is True

    unavail = _Member(JudgmentUnavailable('no_person'))
    ens_partial = pretrained.EnsembleBedExitMethod([unavail, _Member(True), _Member(True)], policy='majority')
    assert ens_partial.judge(None) is True

    ens_strict = pretrained.EnsembleBedExitMethod(
        [unavail, _Member(True)], policy='majority', require_all_valid=True
    )
    with pytest.raises(JudgmentUnavailable, match='no_person'):
        ens_strict.judge(None)

    ens_all_fail = pretrained.EnsembleBedExitMethod([unavail, unavail])
    with pytest.raises(JudgmentUnavailable, match='all_ensemble_members_unavailable'):
        ens_all_fail.judge(None)

    ens_maj.reset()
    ens_maj.close()
    assert all(m.reset_called == 1 and m.closed for m in (m1, m2, m3))


def test_create_method_builds_ensemble_from_json_and_overrides(monkeypatch):
    roi = PolygonROI([[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]])

    class _FakeBuilt:
        def __init__(self, kind, weights, kwargs):
            self.kind = kind
            self.weights = weights
            self.kwargs = kwargs

        def judge(self, frame):
            return False

    def make_ctor(kind):
        def _ctor(selected_roi, weights, **kwargs):
            return _FakeBuilt(kind, weights, kwargs)
        return _ctor

    monkeypatch.setattr(pretrained, 'SegmentationROI', make_ctor('segmentation'))
    monkeypatch.setattr(pretrained, 'YoloPoseROI', make_ctor('yolo_pose'))
    monkeypatch.setattr(pretrained, 'DetectionROI', make_ctor('detection'))

    monkeypatch.setattr(
        pretrained,
        'read_config',
        lambda path: (
            {
                'method': 'ensemble',
                'device': 'cuda:0',
                'ensemble': {
                    'policy': 'any',
                    'models': [
                        {'method': 'segmentation', 'weights': 'ft2_best.pt', 'outside_fraction': 0.08},
                        {'method': 'yolo_pose'},
                    ],
                },
            },
            roi,
            Path('/models'),
        ),
    )
    ens = pretrained.create_method('methods.json')
    assert isinstance(ens, pretrained.EnsembleBedExitMethod)
    assert ens.policy == 'any'
    assert len(ens.members) == 2
    assert ens.members[0].kind == 'segmentation'
    assert ens.members[0].weights == Path('/models/ft2_best.pt')
    assert ens.members[0].kwargs['outside_fraction'] == 0.08
    assert ens.members[0].kwargs['device'] == 'cuda:0'
    assert ens.members[1].kind == 'yolo_pose'
    assert ens.members[1].weights == Path('/models/yolo11n-pose.pt')

    monkeypatch.setattr(
        pretrained,
        'read_config',
        lambda path: ({'method': 'segmentation', 'device': 'cpu'}, roi, Path('/models')),
    )
    ens_cli = pretrained.create_method(
        'methods.json',
        overrides={
            'ensemble_methods': 'segmentation,detection,yolo_pose',
            'ensemble_min_votes': 2,
        },
    )
    assert isinstance(ens_cli, pretrained.EnsembleBedExitMethod)
    assert [m.kind for m in ens_cli.members] == ['segmentation', 'detection', 'yolo_pose']
    assert ens_cli.min_votes == 2


def test_load_bed_exit_method_forwards_overrides_and_rejects_for_external(monkeypatch):
    detector = _Method()
    calls = []
    monkeypatch.setattr(
        'part5_bed_exit.pretrained.create_method',
        lambda config_path, method_name=None, overrides=None: calls.append(
            (config_path, method_name, overrides)
        ) or detector,
    )

    assert bed_exit_method.load_bed_exit_method(
        '', 'methods.json', overrides={'device': 'cuda:0'}
    ) is detector
    assert bed_exit_method.load_bed_exit_method(
        'segmentation', 'methods.json', overrides={'yolo_imgsz': 512}
    ) is detector
    assert calls == [
        ('methods.json', None, {'device': 'cuda:0'}),
        ('methods.json', 'segmentation', {'yolo_imgsz': 512}),
    ]

    with pytest.raises(ValueError, match='overrides'):
        bed_exit_method.load_bed_exit_method(
            'module:Method', '', overrides={'device': 'cuda:0'}
        )
