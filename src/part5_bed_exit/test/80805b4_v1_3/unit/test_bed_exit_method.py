"""목적: ``bed_exit_method.py``의 인터페이스 검사와 로더 함수를 검증한다.

대상은 ``BedExitMethod``, ``validate_bed_exit_method()``와
``load_bed_exit_method()``이다. 실제 모델 import와 생성은 가짜 모듈·팩터리로 격리한다.
"""

from types import ModuleType, SimpleNamespace

import pytest

from part5_bed_exit import bed_exit_method


class _Method:
    def judge(self, frame):
        return False


def test_bed_exit_method_requires_judge_implementation():
    with pytest.raises(TypeError):
        bed_exit_method.BedExitMethod()


def test_validate_bed_exit_method_returns_valid_instance():
    detector = _Method()
    assert bed_exit_method.validate_bed_exit_method(detector) is detector


@pytest.mark.parametrize(
    'target',
    [object(), SimpleNamespace(judge=False), _Method],
)
def test_validate_bed_exit_method_rejects_invalid_target(target):
    with pytest.raises(TypeError):
        bed_exit_method.validate_bed_exit_method(target)


def test_load_bed_exit_method_instantiates_external_class_or_returns_object(monkeypatch):
    module = ModuleType('fake_method_module')
    module.Method = _Method
    module.method = _Method()
    monkeypatch.setattr(bed_exit_method, 'import_module', lambda name: module)

    loaded_class = bed_exit_method.load_bed_exit_method('fake_method_module:Method')
    loaded_object = bed_exit_method.load_bed_exit_method('fake_method_module:method')

    assert isinstance(loaded_class, _Method)
    assert loaded_object is module.method


def test_load_bed_exit_method_uses_config_selection_when_method_is_empty(monkeypatch):
    detector = _Method()
    calls = []
    monkeypatch.setattr(
        'part5_bed_exit.pretrained.create_method',
        lambda config_path, method_name=None: calls.append(
            (config_path, method_name)
        ) or detector,
    )

    assert bed_exit_method.load_bed_exit_method('', 'methods.json') is detector
    assert calls == [('methods.json', None)]


def test_load_bed_exit_method_passes_builtin_override_to_factory(monkeypatch):
    detector = _Method()
    calls = []
    monkeypatch.setattr(
        'part5_bed_exit.pretrained.create_method',
        lambda config_path, method_name=None: calls.append(
            (config_path, method_name)
        ) or detector,
    )

    assert bed_exit_method.load_bed_exit_method(
        'segmentation', 'methods.json'
    ) is detector
    assert calls == [('methods.json', 'segmentation')]


@pytest.mark.parametrize('method_path', ['', 'invalid', ':Method', 'module:'])
def test_load_bed_exit_method_rejects_missing_or_invalid_selection(method_path):
    with pytest.raises(ValueError):
        bed_exit_method.load_bed_exit_method(method_path)


def test_load_bed_exit_method_rejects_config_for_external_method():
    with pytest.raises(ValueError, match='method_config'):
        bed_exit_method.load_bed_exit_method('module:Method', 'methods.json')
