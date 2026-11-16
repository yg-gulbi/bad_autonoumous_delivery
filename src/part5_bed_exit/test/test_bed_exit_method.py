"""Tests for class/object bed-exit method loading and starter template behavior."""

import sys
import types

import pytest

from part5_bed_exit.bed_exit_method import (
    BedExitMethod,
    load_bed_exit_method,
    validate_bed_exit_method,
)
from research.templates.method_template import BedExitMethodTemplate


def _register_temp_module(monkeypatch, module_name, **attrs):
    module = types.ModuleType(module_name)
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, module_name, module)
    return module


def test_load_class_instantiates_per_call_and_preserves_state(monkeypatch):
    class CountingDetector(BedExitMethod):
        def __init__(self):
            self.calls = 0
            self.closed = False

        def judge(self, frame) -> bool:
            self.calls += 1
            return self.calls >= 2

        def close(self) -> None:
            self.closed = True

    _register_temp_module(monkeypatch, '_fake_bed_exit_pkg', CountingDetector=CountingDetector)

    first = load_bed_exit_method('_fake_bed_exit_pkg:CountingDetector')
    second = load_bed_exit_method('_fake_bed_exit_pkg:CountingDetector')
    assert first is not second
    assert first.judge(None) is False
    assert first.judge(None) is True
    assert second.judge(None) is False
    first.close()
    assert first.closed is True
    assert second.closed is False


def test_load_duck_typed_class_and_prebuilt_object(monkeypatch):
    class DuckDetector:
        def __init__(self, flag=True):
            self.flag = flag

        def judge(self, frame):
            return self.flag

    prebuilt = DuckDetector(flag=False)
    _register_temp_module(
        monkeypatch,
        '_fake_duck_pkg',
        DuckDetector=DuckDetector,
        prebuilt_detector=prebuilt,
    )

    from_class = load_bed_exit_method('_fake_duck_pkg:DuckDetector')
    from_instance = load_bed_exit_method('_fake_duck_pkg:prebuilt_detector')
    assert from_class.judge(None) is True
    assert from_instance is prebuilt
    assert from_instance.judge(None) is False


@pytest.mark.parametrize('spec', ['', 'missing_colon', ':Symbol', 'pkg:'])
def test_invalid_method_spec_format_raises_value_error(spec):
    with pytest.raises(ValueError):
        load_bed_exit_method(spec)


def test_invalid_targets_rejected(monkeypatch):
    class MissingJudge:
        pass

    class NonCallableJudge:
        judge = True

    class IncompleteSubclass(BedExitMethod):
        pass

    def bare_function(frame):
        return False

    _register_temp_module(
        monkeypatch,
        '_fake_invalid_pkg',
        MissingJudge=MissingJudge,
        NonCallableJudge=NonCallableJudge,
        IncompleteSubclass=IncompleteSubclass,
        bare_function=bare_function,
    )

    with pytest.raises(TypeError):
        load_bed_exit_method('_fake_invalid_pkg:MissingJudge')
    with pytest.raises(TypeError):
        load_bed_exit_method('_fake_invalid_pkg:NonCallableJudge')
    with pytest.raises(TypeError):
        load_bed_exit_method('_fake_invalid_pkg:IncompleteSubclass')
    with pytest.raises(TypeError):
        load_bed_exit_method('_fake_invalid_pkg:bare_function')
    with pytest.raises(TypeError):
        validate_bed_exit_method(MissingJudge)


def test_starter_template_instantiates_and_raises_not_implemented(monkeypatch):
    _register_temp_module(
        monkeypatch,
        '_fake_template_holder',
        BedExitMethodTemplate=BedExitMethodTemplate,
    )
    detector = load_bed_exit_method('_fake_template_holder:BedExitMethodTemplate')
    assert isinstance(detector, BedExitMethod)
    with pytest.raises(NotImplementedError):
        detector.judge(None)
    detector.close()
