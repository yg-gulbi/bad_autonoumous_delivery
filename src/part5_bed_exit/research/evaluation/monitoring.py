"""오프라인 평가용 3/5 투표, 이벤트 상태와 시각화 계층.

프레임별 판단을 이벤트 발생·복구 상태로 변환하고 연속 프레임 분석 및 화면 오버레이를
제공한다. ROS 운영 노드는 별도의 ``BedExitState``를 사용하므로 둘을 구분해야 한다.
"""
from dataclasses import dataclass
from enum import Enum

import cv2
import numpy as np

from part5_bed_exit.bed_exit_state import BedExitState


class State(str, Enum):
    """오프라인 투표 결과의 정상·이탈 상태 열거형."""

    SAFE = "SAFE"
    EXIT = "EXIT"


@dataclass
class GateResult:
    """현재 투표 상태, 신규 이벤트 여부와 관측 유효성을 묶은 결과."""

    state: State
    event: bool
    valid: bool


class EventGate:
    """저장소의 3/5 투표와 평가 전용 단일 이벤트 래치를 적용한다."""

    def __init__(self, k=3, n=5, rearm_safe_frames=5):
        """고정된 3/5 투표와 정상 5프레임 재무장 규칙을 초기화한다."""
        if (k, n) != (3, 5) or rearm_safe_frames != 5:
            raise ValueError("Part 5 requires k=3, n=5, rearm_safe_frames=5")
        self.k, self.n, self.rearm_safe_frames = k, n, rearm_safe_frames
        self.reset()

    def reset(self):
        """투표·상태·이벤트 래치·연속 정상 횟수를 초기화한다."""
        self.vote = BedExitState()
        self.state = State.SAFE
        self.latched = False
        self.safe_count = 0

    def update(self, exit_detected, valid=True):
        """프레임 관측을 반영하고 현재 상태와 신규 이벤트 여부를 반환한다."""
        if not valid:
            self.vote = BedExitState()
            self.state = State.SAFE
            self.safe_count = 0
            return GateResult(self.state, False, False)
        voted = self.vote.update(exit_detected)
        self.state = State.EXIT if voted else State.SAFE
        event = False
        if exit_detected:
            self.safe_count = 0
        else:
            self.safe_count += 1
        if voted and not self.latched:
            event = True
            self.latched = True
        if self.safe_count >= self.rearm_safe_frames:
            self.latched = False
        return GateResult(self.state, event, True)


class ConsecutiveExitAnalysis:
    """분석 전용 연속 3회 원시 True 지표이며 ROS 운영에는 사용하지 않는다."""

    def __init__(self, required=3):
        """고정된 연속 3프레임 기준을 검증하고 카운터를 초기화한다."""
        if required != 3:
            raise ValueError("The comparison plan fixes three consecutive frames")
        self.required = required
        self.reset()

    def reset(self):
        """연속 이탈 프레임 수를 0으로 되돌린다."""
        self.count = 0

    def update(self, exit_detected, valid=True):
        """유효한 원시 판단의 연속 True 횟수를 갱신한다."""
        if not valid:
            self.reset()
            return None
        if type(exit_detected) is not bool:
            raise TypeError("exit_detected must be a Python bool")
        self.count = self.count + 1 if exit_detected else 0
        return self.count >= self.required


class BedExitMonitor:
    """원시 판단 메서드와 오프라인 이벤트 게이트를 결합한다."""

    def __init__(self, method, **gate_kwargs):
        """판단 메서드와 게이트를 만들고 초기 정상 결과를 저장한다."""
        self.method = method
        self.gate = EventGate(**gate_kwargs)
        self.result = GateResult(State.SAFE, False, False)

    def judge(self, frame) -> bool:
        """프레임을 원시 메서드와 게이트에 통과시켜 최종 이탈 상태를 반환한다."""
        try:
            raw = self.method.judge(frame)
        except Exception:
            self.result = self.gate.update(None, valid=False)
            raise
        self.result = self.gate.update(raw, self.method.last.valid)
        return bool(self.result.state == State.EXIT)

    @property
    def event(self):
        """현재 프레임에서 신규 이벤트가 시작됐는지 반환한다."""
        return self.result.event

    @property
    def valid(self):
        """현재 결과가 유효한 관측에서 나온 것인지 반환한다."""
        return self.result.valid

    @property
    def state(self):
        """현재 오프라인 게이트의 정상·이탈 상태를 반환한다."""
        return self.result.state

    def reset(self):
        """원시 메서드와 이벤트 게이트를 함께 초기화한다."""
        self.method.reset()
        self.gate.reset()
        self.result = GateResult(State.SAFE, False, False)

    def close(self):
        """결합된 원시 판단 메서드의 자원을 해제한다."""
        self.method.close()


def draw_overlay(frame, method, gate_result):
    """ROI·BBox·관절·게이트 상태를 프레임 복사본에 그린다."""
    out = frame.copy()
    cv2.polylines(
        out, [np.rint(method.roi.pixels(frame.shape)).astype(np.int32)],
        True, (0, 220, 0), 2)
    if method.last.bbox is not None:
        x1, y1, x2, y2 = map(int, method.last.bbox)
        cv2.rectangle(out, (x1, y1), (x2, y2), (255, 200, 50), 2)
    for x, y, confidence in method.last.points:
        if confidence >= 0.5 and np.isfinite([x, y]).all():
            cv2.circle(out, (int(x), int(y)), 3, (0, 180, 255), -1)
    label = (
        f"{gate_result.state.value} | {method.last.reason}"
        if gate_result.valid else f"No decision (not published) | {method.last.reason}"
    )
    color = (0, 0, 255) if gate_result.state == State.EXIT else (0, 190, 0)
    cv2.putText(out, label, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
    return out
