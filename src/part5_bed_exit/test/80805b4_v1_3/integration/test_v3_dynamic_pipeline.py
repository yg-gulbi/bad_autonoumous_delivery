"""외부 자식 클래스를 실제 import해 5프레임 투표까지 연결한다."""

import importlib
import sys

import numpy as np
import pytest

from part5_bed_exit.bed_exit_method import load_bed_exit_method
from part5_bed_exit.bed_exit_state import BedExitState


@pytest.mark.integration
def test_external_child_class_loads_and_reaches_five_frame_vote(tmp_path, monkeypatch):
    module_name = 'v3_dynamic_method_fixture'
    module_path = tmp_path / f'{module_name}.py'
    module_path.write_text(
        """from part5_bed_exit.bed_exit_method import BedExitMethod

class DynamicBedExitMethod(BedExitMethod):
    def judge(self, frame):
        return bool(frame[0, 0, 0])
""",
        encoding='utf-8',
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, module_name, raising=False)
    importlib.invalidate_caches()

    detector = load_bed_exit_method(f'{module_name}:DynamicBedExitMethod')
    state = BedExitState()
    frames = [np.full((2, 2, 3), value, np.uint8) for value in (1, 1, 0, 1, 0)]

    outputs = [state.update(detector.judge(frame)) for frame in frames]

    assert type(detector).__name__ == 'DynamicBedExitMethod'
    assert outputs == [False, False, False, False, True]
    assert list(state.history) == [True, True, False, True, False]
