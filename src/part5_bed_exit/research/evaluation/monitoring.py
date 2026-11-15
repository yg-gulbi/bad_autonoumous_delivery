"""Offline 3/5 event gating and visualization; not part of the ROS runtime."""
from dataclasses import dataclass
from enum import Enum

import cv2
import numpy as np

from part5_bed_exit.bed_exit_state import BedExitState


class State(str, Enum):
    SAFE = "SAFE"
    EXIT = "EXIT"


@dataclass
class GateResult:
    state: State
    event: bool
    valid: bool


class EventGate:
    """Apply the repository 3/5 vote and an evaluation-only event latch."""

    def __init__(self, k=3, n=5, rearm_safe_frames=5):
        if (k, n) != (3, 5) or rearm_safe_frames != 5:
            raise ValueError("Part 5 requires k=3, n=5, rearm_safe_frames=5")
        self.k, self.n, self.rearm_safe_frames = k, n, rearm_safe_frames
        self.reset()

    def reset(self):
        self.vote = BedExitState()
        self.state = State.SAFE
        self.latched = False
        self.safe_count = 0

    def update(self, exit_detected, valid=True):
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


class BedExitMonitor:
    """Compose one raw method with the offline gate for previews and tests."""

    def __init__(self, method, **gate_kwargs):
        self.method = method
        self.gate = EventGate(**gate_kwargs)
        self.result = GateResult(State.SAFE, False, False)

    def judge(self, frame) -> bool:
        try:
            raw = self.method.judge(frame)
        except Exception:
            self.result = self.gate.update(None, valid=False)
            raise
        self.result = self.gate.update(raw, self.method.last.valid)
        return bool(self.result.state == State.EXIT)

    @property
    def event(self):
        return self.result.event

    @property
    def valid(self):
        return self.result.valid

    @property
    def state(self):
        return self.result.state

    def reset(self):
        self.method.reset()
        self.gate.reset()
        self.result = GateResult(State.SAFE, False, False)

    def close(self):
        self.method.close()


def draw_overlay(frame, method, gate_result):
    out = frame.copy()
    for roi, color in ((method.bed_roi, (255, 180, 0)), (method.safe_roi, (0, 220, 0))):
        cv2.polylines(
            out, [np.rint(roi.pixels(frame.shape)).astype(np.int32)], True, color, 2)
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
