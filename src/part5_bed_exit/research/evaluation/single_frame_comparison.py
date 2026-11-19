"""ROS 없이 직접 로딩용 Detection·Segmentation을 단일 프레임 단위로 비교하는 도구.

이미지, 영상 또는 웹캠을 입력으로 받고 각 메서드의 원시 판정과 ``render()`` 결과를
저장하거나 화면에 표시한다. 5프레임 투표나 ROS 토픽 발행은 수행하지 않는다.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import inspect
import json
from pathlib import Path
import sys
import time

import cv2

# ROS 2 setup.bash나 PYTHONPATH 없이도 part5_bed_exit 모듈을 바로 import할 수 있게 경로 추가
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from part5_bed_exit.bed_exit_method import validate_bed_exit_method
from part5_bed_exit.methods.bed_exit_core import PolygonROI
from part5_bed_exit.pretrained import create_method, read_config

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp'}

SELECTED_METHODS = None
DEFAULT_SOURCE = str(PACKAGE_ROOT / 'research' / 'data' / 'test_images')
DEFAULT_OUTPUT_DIR = PACKAGE_ROOT / 'results' / 'model_tests' / 'exploratory_prototypes' / 'single_frame_runs'
DEFAULT_CONFIG = PACKAGE_ROOT / 'config' / 'methods.template.json'


def resolve_roi(roi_config) -> PolygonROI:
    """정규화 좌표·ROI JSON·기존 객체를 코어 ``PolygonROI``로 통일한다."""
    if isinstance(roi_config, PolygonROI):
        return roi_config
    if isinstance(roi_config, (str, Path)):
        data = json.loads(Path(roi_config).read_text(encoding='utf-8'))
        return PolygonROI(data.get('roi', data.get('bed_polygon')))
    return PolygonROI(roi_config)


@dataclass
class MethodRunner:
    """개별 메서드의 단일 프레임 판정 결과, 처리 시간, 오류 이력을 관리한다."""

    name: str
    detector: object
    valid_frames: int = 0
    exit_count: int = 0
    safe_count: int = 0
    error_count: int = 0
    total_ms: float = 0.0
    last_decision: bool | None = None
    last_ms: float = 0.0
    last_error: str | None = None

    def prepare_image(self, roi_config=None) -> None:
        """독립 정적 이미지 평가 전 추적 상태를 초기화하고 필요 시 ROI를 갱신한다."""
        set_roi = getattr(self.detector, 'set_roi', None)
        if roi_config is not None and callable(set_roi):
            set_roi(roi_config)
            return
        reset = getattr(self.detector, 'reset', None)
        if callable(reset) and roi_config is not None:
            reset(roi_config=roi_config)
        elif callable(reset):
            reset()

    def evaluate(self, frame) -> bool | None:
        """단일 프레임에 대해 judge(frame)를 직접 실행·계측한다 (다수결 미적용)."""
        started = time.perf_counter()
        try:
            decision = self.detector.judge(frame)
            if type(decision) is not bool:
                raise TypeError('The method must return a Python bool (True = exit)')
        except Exception as exc:
            self.error_count += 1
            self.last_decision = None
            self.last_ms = (time.perf_counter() - started) * 1000.0
            self.last_error = f'{type(exc).__name__}: {exc}'
            return None

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self.valid_frames += 1
        self.exit_count += int(decision)
        self.safe_count += int(not decision)
        self.total_ms += elapsed_ms
        self.last_decision = decision
        self.last_ms = elapsed_ms
        self.last_error = None
        return decision

    def render_image(self, frame):
        """메서드 고유 시각화 결과를 반환한다. 미구현 시 기본 텍스트 오버레이를 사용한다."""
        render = getattr(self.detector, 'render', None)
        if callable(render):
            return render(frame)
        canvas = frame.copy()
        color = (0, 165, 255) if self.last_error else ((0, 0, 255) if self.last_decision else (0, 200, 0))
        cv2.putText(canvas, self.status_text(), (16, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        return canvas

    @property
    def avg_ms(self) -> float:
        """오류를 제외한 유효 프레임의 평균 처리 시간을 밀리초로 반환한다."""
        return self.total_ms / self.valid_frames if self.valid_frames else 0.0

    @property
    def fps(self) -> float:
        """평균 처리 시간을 초당 처리 프레임 수로 환산한다."""
        return 1000.0 / self.avg_ms if self.avg_ms > 0 else 0.0

    def status_text(self) -> str:
        """최근 판정·오류·ROI 지표·처리 시간을 한 줄 문자열로 만든다."""
        if self.last_error is not None:
            return f'{self.name}: ERROR ({self.last_error})'
        verdict = 'EXIT(True)' if self.last_decision else 'SAFE(False)'
        diag = getattr(self.detector, 'last', None)
        detail = ''
        if diag is not None and diag.score is not None:
            detail = f' outside={diag.score * 100:.1f}%'
        return f'{self.name}: {verdict}{detail} | {self.last_ms:.1f}ms'


def select_runners(methods: dict, selected: list[str] | None, roi_config=None) -> list[MethodRunner]:
    """등록된 메서드 목록에서 실행 대상을 고르고 객체를 생성/검증한다."""
    keys = list(methods.keys()) if selected is None else selected
    if not keys:
        raise ValueError('METHODS에 최소 1개 이상의 메서드를 등록해야 합니다.')

    runners = []
    for key in keys:
        if key not in methods:
            raise KeyError(f"선택한 메서드 키 '{key}'가 METHODS에 없습니다.")
        entry = methods[key]
        if inspect.isclass(entry):
            sig = inspect.signature(entry)
            detector = entry(roi_config=roi_config) if (roi_config is not None and 'roi_config' in sig.parameters) else entry()
        else:
            detector = entry
        runners.append(MethodRunner(name=key, detector=validate_bed_exit_method(detector)))
    return runners


def close_runners(runners: list[MethodRunner]) -> None:
    """실행 종료 시 각 메서드 객체의 close()가 있으면 안전하게 호출한다."""
    for runner in runners:
        close = getattr(runner.detector, 'close', None)
        if callable(close):
            close()


def _resolve_inputs(sources: list[str | Path]) -> tuple[list[Path], str | int | None]:
    """입력 경로를 정적 이미지 목록 또는 단일 영상/웹캠 소스로 분류한다."""
    image_paths: list[Path] = []
    for raw in sources:
        if isinstance(raw, int) or (isinstance(raw, str) and raw.isdigit()):
            if len(sources) > 1:
                raise ValueError('웹캠 인덱스는 단독 입력으로만 지정할 수 있습니다.')
            return [], int(raw)

        path = Path(raw)
        if path.is_dir():
            dir_images = sorted(
                p for p in path.iterdir()
                if p.is_file() and p.suffix.lower() in IMAGE_EXTS
            )
            if not dir_images:
                raise RuntimeError(f'디렉터리에 이미지 파일이 없습니다: {path}')
            image_paths.extend(dir_images)
        elif path.is_file() and path.suffix.lower() in IMAGE_EXTS:
            image_paths.append(path)
        elif path.is_file():
            if len(sources) > 1:
                raise ValueError('영상 파일은 단독 입력으로만 지정할 수 있습니다.')
            return [], str(path)
        else:
            raise FileNotFoundError(f'입력 경로를 찾을 수 없습니다: {raw}')
    return image_paths, None


def _sidecar_roi_path(image_path: Path) -> Path | None:
    """이미지별 '<stem>.roi.json' 또는 '<filename>.roi.json' 사이드카를 탐색한다."""
    for candidate in (
        image_path.with_suffix('.roi.json'),
        Path(f'{image_path}.roi.json'),
    ):
        if candidate.is_file():
            return candidate
    return None


def _save_rendered_image(output_dir: Path, method_name: str, label: str, image) -> Path:
    """메서드별 하위 폴더에 렌더링 결과 PNG를 저장하고 경로를 반환한다."""
    method_dir = output_dir / method_name
    method_dir.mkdir(parents=True, exist_ok=True)
    out_path = method_dir / f'{label}.png'
    cv2.imwrite(str(out_path), image)
    return out_path


def print_summary(
    total_frames: int,
    runners: list[MethodRunner],
    disagreements: int,
    comparable_frames: int,
) -> None:
    """메서드별 단일 프레임 판정 통계와 처리 속도를 표 형태로 출력한다."""
    print('\n=== 메서드 비교 결과 요약 (단일 프레임 판정) ===')
    print(f'총 입력 수: {total_frames}')
    print(
        f"{'Method':<18} {'Valid':>6} {'Err':>5} {'Exit':>7} "
        f"{'Safe':>7} {'Avg(ms)':>9} {'FPS':>7}"
    )
    print('-' * 66)
    for r in runners:
        print(
            f'{r.name:<18} {r.valid_frames:>6} {r.error_count:>5} '
            f'{r.exit_count:>7} {r.safe_count:>7} {r.avg_ms:>9.2f} {r.fps:>7.1f}'
        )
    if len(runners) >= 2 and comparable_frames > 0:
        agree_rate = (comparable_frames - disagreements) / comparable_frames * 100.0
        print(
            f'메서드 간 판정 불일치: {disagreements}/{comparable_frames} '
            f'(일치율 {agree_rate:.1f}%)'
        )


def run_comparison(
    source=DEFAULT_SOURCE,
    methods=None,
    selected=SELECTED_METHODS,
    roi_config=None,
    output_dir: str | Path | None = DEFAULT_OUTPUT_DIR,
    config_path: str | Path = DEFAULT_CONFIG,
    show: bool = False,
    max_frames: int | None = None,
) -> tuple[list[MethodRunner], int, int]:
    """정적 이미지(파일/폴더) 또는 영상/웹캠에 대해 모든 메서드를 실행·시각화 저장한다."""
    sources = list(source) if isinstance(source, (list, tuple)) else [source]
    image_paths, video_source = _resolve_inputs(sources)
    base_roi = resolve_roi(roi_config) if roi_config is not None else read_config(config_path)[1]
    active_methods = (
        {
            'detection': create_method(config_path, 'detection'),
            'segmentation': create_method(config_path, 'segmentation'),
        }
        if methods is None
        else methods
    )
    runners = select_runners(active_methods, selected, roi_config=base_roi)
    out_root = None if output_dir is None else Path(output_dir)

    total_frames = 0
    disagreements = 0
    comparable_frames = 0
    cap = None

    try:
        if image_paths:
            # 정적 이미지는 파일마다 추적 상태를 초기화하고 사이드카 ROI를 적용한다.
            for img_path in image_paths[:max_frames]:
                frame = cv2.imread(str(img_path))
                if frame is None:
                    raise RuntimeError(f'이미지를 읽을 수 없습니다: {img_path}')
                total_frames += 1

                sidecar = _sidecar_roi_path(img_path) if roi_config is None else None
                active_roi = resolve_roi(sidecar) if sidecar is not None else base_roi

                saved_paths = []
                for runner in runners:
                    runner.prepare_image(roi_config=active_roi)
                    runner.evaluate(frame)
                    rendered = runner.render_image(frame) if (out_root is not None or show) else None
                    if out_root is not None:
                        saved_paths.append(
                            _save_rendered_image(out_root, runner.name, img_path.stem, rendered)
                        )
                    if show:
                        cv2.imshow(f'{runner.name} - {img_path.name}', rendered)

                valid_decisions = [r.last_decision for r in runners if r.last_error is None]
                if len(valid_decisions) == len(runners) and len(runners) >= 2:
                    comparable_frames += 1
                    if len(set(valid_decisions)) > 1:
                        disagreements += 1

                summary = ' | '.join(r.status_text() for r in runners)
                print(f'[{img_path.name}] {summary}')
                for saved in saved_paths:
                    print(f'  -> saved: {saved}')
                if show:
                    if cv2.waitKey(0) & 0xFF in (ord('q'), 27):
                        break
        else:
            # 영상·웹캠은 프레임 순서를 유지해 같은 판단 객체에 연속 입력한다.
            cap = cv2.VideoCapture(video_source)
            if not cap.isOpened():
                raise RuntimeError(f'입력 소스를 열 수 없습니다: {video_source}')

            while max_frames is None or total_frames < max_frames:
                ok, frame = cap.read()
                if not ok:
                    break
                total_frames += 1

                for runner in runners:
                    runner.evaluate(frame)
                    rendered = runner.render_image(frame) if (out_root is not None or show) else None
                    if out_root is not None:
                        _save_rendered_image(out_root, runner.name, f'frame_{total_frames:06d}', rendered)
                    if show:
                        cv2.imshow(f'Bed Exit - {runner.name}', rendered)

                valid_decisions = [r.last_decision for r in runners if r.last_error is None]
                if len(valid_decisions) == len(runners) and len(runners) >= 2:
                    comparable_frames += 1
                    if len(set(valid_decisions)) > 1:
                        disagreements += 1

                print(f'[#{total_frames}] ' + ' | '.join(r.status_text() for r in runners))
                if show and (cv2.waitKey(1) & 0xFF in (ord('q'), 27)):
                    break
    finally:
        if cap is not None:
            cap.release()
        if show:
            cv2.destroyAllWindows()
        close_runners(runners)

    print_summary(total_frames, runners, disagreements, comparable_frames)
    return runners, disagreements, total_frames


def main():
    """명령행 인자를 해석해 단일 프레임 비교 실행기를 호출한다."""
    parser = argparse.ArgumentParser(description='Compare Part 5 bed-exit methods on images/video.')
    parser.add_argument(
        '--source',
        nargs='+',
        default=[DEFAULT_SOURCE],
        help='이미지 파일/디렉터리, 영상 파일, 또는 웹캠 인덱스(예: 0)',
    )
    parser.add_argument('--roi', default=None, help='공통 침대 ROI JSON 파일 경로')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG), help='method·모델·ROI 설정 JSON')
    parser.add_argument(
        '--output-dir',
        default=str(DEFAULT_OUTPUT_DIR),
        help='메서드별 시각화 이미지 저장 폴더',
    )
    parser.add_argument('--no-save', action='store_true', help='시각화 결과 이미지를 저장하지 않음')
    parser.add_argument('--show', action='store_true', help='OpenCV 창으로 결과 이미지/영상 표시')
    parser.add_argument('--max-frames', type=int, default=None, help='최대 처리 이미지/프레임 수')
    args = parser.parse_args()

    source_arg = args.source[0] if len(args.source) == 1 else args.source
    run_comparison(
        source=source_arg,
        roi_config=args.roi,
        config_path=args.config,
        output_dir=None if args.no_save else args.output_dir,
        show=args.show,
        max_frames=args.max_frames,
    )


if __name__ == '__main__':
    main()
