"""ROS 2 없이 웹캠/영상 파일로 나와 팀원의 이탈 판단 메서드 객체들을 비교·검증하는 실행 스크립트."""

import argparse
from dataclasses import dataclass, field
import inspect
import os
from pathlib import Path
import sys
import time

import cv2

# ROS 2 setup.bash나 PYTHONPATH 없이도 part5_bed_exit 모듈을 바로 import할 수 있게 경로 추가
PACKAGE_ROOT = Path(__file__).resolve().parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from part5_bed_exit.bed_exit_method import validate_bed_exit_method
from part5_bed_exit.bed_exit_state import BedExitState
from part5_bed_exit.methods.common import ROI_ENV_VAR, default_camera_index
from part5_bed_exit.methods.segmentation_method import SegmentationBedExitMethod

# ==============================================================================
# [사용자 설정 영역] 나와 팀원의 메서드 클래스/객체를 여기에 등록하고 선택한다.
# ==============================================================================
# 각 클래스는 judge(self, frame) -> bool 메서드를 가져야 한다.
# 클래스를 그대로 넣어도 되고(MethodClass), 미리 생성한 객체를 넣어도 된다(MethodClass()).
METHODS = {
    'segmentation': SegmentationBedExitMethod,
    # 'teammate': TeammateBedExitMethod(),
}

# 이번 실행에서 비교할 메서드 키 목록 (None이면 METHODS에 등록된 전체 메서드 실행)
SELECTED_METHODS = None

# 기본 입력 소스: 외장 USB 웹캠이 연결되어 있으면 우선 선택(없으면 0) 또는 'sample.mp4'
DEFAULT_SOURCE = default_camera_index()
# ==============================================================================


@dataclass
class MethodRunner:
    """개별 메서드 객체의 판정 결과, 5프레임 다수결 상태, 처리 시간, 오류 이력을 관리한다."""

    name: str
    detector: object
    state: BedExitState = field(default_factory=BedExitState)
    valid_frames: int = 0
    raw_true: int = 0
    voted_unsafe: int = 0
    error_count: int = 0
    total_ms: float = 0.0
    last_raw: bool | None = None
    last_voted: bool | None = None
    last_ms: float = 0.0
    last_error: str | None = None

    def evaluate(self, frame) -> bool | None:
        """단일 프레임에 대해 judge(frame)를 실행·계측하고 5프레임 상태를 갱신한다."""
        started = time.perf_counter()
        try:
            raw = self.detector.judge(frame)
            voted = self.state.update(raw)
        except Exception as exc:
            # 한 메서드가 실패해도 이 메서드만 초기화하고 다른 메서드 비교는 계속한다.
            self.state = BedExitState()
            self.error_count += 1
            self.last_raw = None
            self.last_voted = None
            self.last_ms = (time.perf_counter() - started) * 1000.0
            self.last_error = f'{type(exc).__name__}: {exc}'
            return None

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self.valid_frames += 1
        self.raw_true += int(raw)
        self.voted_unsafe += int(voted)
        self.total_ms += elapsed_ms
        self.last_raw = raw
        self.last_voted = voted
        self.last_ms = elapsed_ms
        self.last_error = None
        return voted

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.valid_frames if self.valid_frames else 0.0

    @property
    def fps(self) -> float:
        avg = self.avg_ms
        return 1000.0 / avg if avg > 0 else 0.0

    def status_text(self) -> str:
        if self.last_error is not None:
            return f'{self.name}: ERROR ({self.last_error})'
        return (
            f'{self.name}: raw={self.last_raw} | 5f_vote={self.last_voted} '
            f'| {self.last_ms:.1f}ms (avg {self.avg_ms:.1f}ms, {self.fps:.1f} FPS)'
        )


def parse_source(raw_source):
    """문자열 '0'은 웹캠 인덱스 정수로, 그 외에는 영상 파일 경로 문자열로 변환한다."""
    if isinstance(raw_source, int):
        return raw_source
    return int(raw_source) if raw_source.isdigit() else raw_source


def select_runners(methods: dict, selected: list[str] | None) -> list[MethodRunner]:
    """등록된 메서드 목록에서 실행 대상을 고르고 객체를 생성/검증한다."""
    keys = list(methods.keys()) if selected is None else selected
    if not keys:
        raise ValueError('METHODS에 최소 1개 이상의 메서드를 등록해야 합니다.')

    runners = []
    for key in keys:
        if key not in methods:
            raise KeyError(f"선택한 메서드 키 '{key}'가 METHODS에 없습니다.")
        entry = methods[key]
        detector = entry() if inspect.isclass(entry) else entry
        runners.append(MethodRunner(name=key, detector=validate_bed_exit_method(detector)))
    return runners


def close_runners(runners: list[MethodRunner]) -> None:
    """실행 종료 시 각 메서드 객체의 close()가 있으면 안전하게 호출한다."""
    for runner in runners:
        close = getattr(runner.detector, 'close', None)
        if callable(close):
            close()


def draw_overlay(frame, frame_idx: int, runners: list[MethodRunner]):
    """화면 표시용 복사본 위에 프레임 번호, 마스크/ROI(지원 시), 5프레임 다수결 상태를 그린다."""
    if len(runners) == 1 and callable(getattr(runners[0].detector, 'render', None)):
        runner = runners[0]
        canvas = runner.detector.render(frame)
        h, w = canvas.shape[:2]
        color = (0, 165, 255) if runner.last_error is not None else ((0, 0, 255) if runner.last_voted else (0, 200, 0))
        footer = f'Frame {frame_idx} | {runner.status_text()} | q/ESC quit'
        cv2.rectangle(canvas, (0, max(0, h - 34)), (w, h), (20, 20, 20), -1)
        cv2.putText(canvas, footer, (12, h - 11), cv2.FONT_HERSHEY_SIMPLEX, 0.52, color, 2, cv2.LINE_AA)
        return canvas

    canvas = frame.copy()
    cv2.putText(
        canvas,
        f'Frame {frame_idx} (Press q or ESC to quit)',
        (16, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (255, 255, 255),
        2,
    )
    for row_idx, runner in enumerate(runners, start=1):
        color = (0, 200, 0)
        if runner.last_error is not None:
            color = (0, 165, 255)
        elif runner.last_voted:
            color = (0, 0, 255)
        cv2.putText(
            canvas,
            runner.status_text(),
            (16, 28 + row_idx * 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            color,
            2,
        )
    return canvas


def print_summary(
    total_frames: int,
    runners: list[MethodRunner],
    raw_disagreements: int,
    comparable_frames: int,
) -> None:
    """전체 실행 종료 후 메서드별 판정 통계와 처리 속도를 표 형태로 출력한다."""
    print('\n=== 메서드 비교 결과 요약 ===')
    print(f'총 입력 프레임: {total_frames}')
    print(
        f"{'Method':<18} {'Valid':>6} {'Err':>5} {'RawTrue':>8} "
        f"{'5fUnsafe':>9} {'Avg(ms)':>9} {'FPS':>7}"
    )
    print('-' * 68)
    for r in runners:
        print(
            f'{r.name:<18} {r.valid_frames:>6} {r.error_count:>5} '
            f'{r.raw_true:>8} {r.voted_unsafe:>9} {r.avg_ms:>9.2f} {r.fps:>7.1f}'
        )
    if len(runners) >= 2 and comparable_frames > 0:
        agree_rate = (comparable_frames - raw_disagreements) / comparable_frames * 100.0
        print(
            f'메서드 간 raw 판정 불일치 프레임: {raw_disagreements}/{comparable_frames} '
            f'(일치율 {agree_rate:.1f}%)'
        )


def run_comparison(
    source=DEFAULT_SOURCE,
    methods=None,
    selected=SELECTED_METHODS,
    show: bool = True,
    max_frames: int | None = None,
) -> tuple[list[MethodRunner], int, int]:
    """웹캠 또는 영상 프레임마다 선택된 모든 메서드 객체를 순차 실행·비교한다."""
    runners = select_runners(METHODS if methods is None else methods, selected)
    cap = None
    total_frames = 0
    raw_disagreements = 0
    comparable_frames = 0

    try:
        cap = cv2.VideoCapture(parse_source(source))
        if not cap.isOpened():
            raise RuntimeError(f'입력 소스를 열 수 없습니다: {source}')

        while max_frames is None or total_frames < max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            total_frames += 1

            # 모든 메서드에 동일한 원본 프레임을 전달하기 위해 화면 그리기 전에 평가한다.
            for runner in runners:
                runner.evaluate(frame)

            raw_results = [r.last_raw for r in runners if r.last_error is None]
            if len(raw_results) == len(runners) and len(runners) >= 2:
                comparable_frames += 1
                if len(set(raw_results)) > 1:
                    raw_disagreements += 1

            if show:
                cv2.imshow('Bed Exit Method Comparison', draw_overlay(frame, total_frames, runners))
                if cv2.waitKey(1) & 0xFF in (ord('q'), 27):
                    break
            else:
                summary_line = ' | '.join(r.status_text() for r in runners)
                print(f'[#{total_frames}] {summary_line}')
    finally:
        if cap is not None:
            cap.release()
        if show:
            cv2.destroyAllWindows()
        close_runners(runners)

    print_summary(total_frames, runners, raw_disagreements, comparable_frames)
    return runners, raw_disagreements, total_frames


def main():
    parser = argparse.ArgumentParser(description='Compare Part 5 bed-exit method classes.')
    parser.add_argument(
        '--source',
        default=str(DEFAULT_SOURCE),
        help='웹캠 인덱스(예: 0) 또는 영상 파일 경로(예: sample.mp4)',
    )
    parser.add_argument('--no-show', action='store_true', help='OpenCV 창 없이 콘솔 로그만 출력')
    parser.add_argument('--max-frames', type=int, default=None, help='최대 처리 프레임 수 제한')
    parser.add_argument('--roi', default=None, help='침대 경계 ROI JSON 경로 (미지정 시 기본 methods/bed_roi.json)')
    args = parser.parse_args()
    if args.roi:
        os.environ[ROI_ENV_VAR] = args.roi
    run_comparison(
        source=args.source,
        show=not args.no_show,
        max_frames=args.max_frames,
    )


if __name__ == '__main__':
    main()
