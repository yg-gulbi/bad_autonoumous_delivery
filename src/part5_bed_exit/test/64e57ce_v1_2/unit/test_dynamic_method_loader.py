"""ROS 실행 경로에서 사용하는 판단 메서드 동적 로더를 검증한다."""

from types import ModuleType, SimpleNamespace

import pytest

from part5_bed_exit import bed_exit_method


class _DynamicDetector:
    def judge(self, frame):
        return False


def test_loads_class_and_object_from_module_path(monkeypatch):
    module = ModuleType('test_dynamic_detector_module')
    module.Detector = _DynamicDetector
    module.detector = _DynamicDetector()
    monkeypatch.setattr(bed_exit_method, 'import_module', lambda name: module)

    loaded_class = bed_exit_method.load_bed_exit_method(
        'test_dynamic_detector_module:Detector'
    )
    loaded_object = bed_exit_method.load_bed_exit_method(
        'test_dynamic_detector_module:detector'
    )

    assert isinstance(loaded_class, _DynamicDetector)
    assert loaded_object is module.detector


def test_empty_method_uses_json_factory(monkeypatch):
    detector = SimpleNamespace(judge=lambda frame: False)
    calls = []

    monkeypatch.setattr(
        'part5_bed_exit.pretrained.create_method',
        lambda config_path, method_name=None: calls.append(
            (config_path, method_name)
        ) or detector,
    )

    assert bed_exit_method.load_bed_exit_method('', 'methods.json') is detector
    assert calls == [('methods.json', None)]


def test_builtin_method_overrides_json_selection(monkeypatch):
    detector = SimpleNamespace(judge=lambda frame: False)
    calls = []

    monkeypatch.setattr(
        'part5_bed_exit.pretrained.create_method',
        lambda config_path, method_name=None: calls.append(
            (config_path, method_name)
        ) or detector,
    )

    assert (
        bed_exit_method.load_bed_exit_method('segmentation', 'methods.json')
        is detector
    )
    assert calls == [('methods.json', 'segmentation')]


@pytest.mark.parametrize('method_path', ['invalid', ':Detector', 'module:'])
def test_rejects_invalid_module_paths(method_path):
    with pytest.raises(ValueError, match='method'):
        bed_exit_method.load_bed_exit_method(method_path)


def test_external_method_rejects_builtin_config():
    with pytest.raises(ValueError, match='method_config'):
        bed_exit_method.load_bed_exit_method('module:Detector', 'methods.json')
