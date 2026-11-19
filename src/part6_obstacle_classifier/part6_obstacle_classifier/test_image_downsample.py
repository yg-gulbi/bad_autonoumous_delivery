"""로컬 이미지(또는 프레임 폴더)를 512x288로 다운샘플링한 뒤 YOLO로 객체를 인식하는 테스트 스크립트."""

import argparse
from pathlib import Path

import cv2
from ultralytics import YOLO

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp'}


def collect_image_paths(input_path: str) -> list[Path]:
    """단일 이미지 파일 또는 폴더에서 이미지 파일 경로 목록을 수집."""
    path = Path(input_path)
    if not path.exists():
        raise FileNotFoundError(f'입력 경로를 찾을 수 없습니다: {input_path}')
    if path.is_file():
        return [path]
    return sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS)


def crop_by_ratio(frame, crop_ratio: tuple[float, float, float, float]):
    """(x1, y1, x2, y2) 비율(0.0~1.0) 기준으로 관심 영역(ROI)을 크롭."""
    h, w = frame.shape[:2]
    rx1, ry1, rx2, ry2 = crop_ratio
    x1 = max(0, min(int(w * rx1), w - 1))
    y1 = max(0, min(int(h * ry1), h - 1))
    x2 = max(x1 + 1, min(int(w * rx2), w))
    y2 = max(y1 + 1, min(int(h * ry2), h))
    return frame[y1:y2, x1:x2]


def downsample_frame(frame, width: int = 512, height: int = 288):
    """축소 시 화질 손실을 줄이기 위해 INTER_AREA 보간법으로 다운샘플링."""
    return cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)


def annotate_result(frame, result, alpha: float = 0.35):
    """탐지된 객체의 세그멘테이션 마스크(또는 미지원 시 bbox 내부)를 반투명 마스킹해 반환."""
    if getattr(result, 'masks', None) is not None:
        return result.plot()

    annotated = frame.copy()
    if result.boxes is None:
        return annotated

    palette = [
        (0, 255, 0),
        (0, 165, 255),
        (255, 0, 255),
        (255, 255, 0),
        (0, 0, 255),
    ]
    for box in result.boxes:
        cls_id = int(box.cls[0])
        label = str(result.names[cls_id])
        conf = float(box.conf[0])
        color = palette[cls_id % len(palette)]
        x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())

        # bbox 영역 내부를 반투명 색으로 마스킹
        overlay = annotated.copy()
        cv2.rectangle(overlay, (x1, y1), (x2, y2), color, -1)
        annotated = cv2.addWeighted(overlay, alpha, annotated, 1.0 - alpha, 0)
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
        cv2.putText(
            annotated,
            f'{label} {conf:.2f}',
            (x1, max(18, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            color,
            2,
        )
    return annotated


def run_image_detection(
    input_path: str,
    model_path: str = 'yolo11n.pt',
    width: int = 512,
    height: int = 288,
    conf: float = 0.25,
    crop_ratio: tuple[float, float, float, float] | None = None,
    save_dir: str = '',
    no_show: bool = False,
):
    """이미지들을 (선택적 크롭 후) 지정 해상도로 다운샘플링해 YOLO 추론 후 결과를 출력·표시한다."""
    image_paths = collect_image_paths(input_path)
    if not image_paths:
        print(f'[안내] 처리할 이미지가 없습니다: {input_path}')
        return 0

    model = YOLO(model_path)
    out_dir = Path(save_dir) if save_dir else None
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)

    window_name = f'YOLO Detection ({width}x{height})'
    if not no_show:
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

    processed = 0
    try:
        for img_path in image_paths:
            raw = cv2.imread(str(img_path))
            if raw is None:
                print(f'[건너뜀] 이미지를 읽을 수 없습니다: {img_path}')
                continue

            source = crop_by_ratio(raw, crop_ratio) if crop_ratio is not None else raw
            resized = downsample_frame(source, width=width, height=height)
            results = model(resized, conf=conf, verbose=False)
            result = results[0]

            detections = []
            for box in result.boxes:
                cls_id = int(box.cls[0])
                label = str(result.names[cls_id])
                score = float(box.conf[0])
                x1, y1, x2, y2 = [round(v, 1) for v in box.xyxy[0].tolist()]
                detections.append(f'{label}({score:.2f}) [{x1}, {y1}, {x2}, {y2}]')

            print(
                f'[{img_path.name}] {raw.shape[1]}x{raw.shape[0]} -> {width}x{height} | '
                f'탐지 {len(detections)}개: {", ".join(detections) if detections else "없음"}'
            )

            annotated = annotate_result(resized, result)
            if out_dir is not None:
                cv2.imwrite(str(out_dir / img_path.name), annotated)

            if not no_show:
                cv2.imshow(window_name, annotated)
                key = cv2.waitKey(0) & 0xFF
                if key in (ord('q'), 27):  # q 또는 ESC로 종료, 다른 키는 다음 프레임
                    break

            processed += 1
    finally:
        if not no_show:
            cv2.destroyAllWindows()

    return processed


def main(args=None):
    parser = argparse.ArgumentParser(
        description='이미지를 512x288로 다운샘플링하여 YOLO 객체 인식을 수행하는 테스트 도구'
    )
    parser.add_argument('input', help='이미지 파일 또는 이미지 폴더 경로')
    parser.add_argument('--model', default='yolo11n.pt', help='YOLO 모델 경로 (기본: yolo11n.pt)')
    parser.add_argument('--width', type=int, default=512, help='다운샘플링 너비 (기본: 512)')
    parser.add_argument('--height', type=int, default=288, help='다운샘플링 높이 (기본: 288)')
    parser.add_argument('--conf', type=float, default=0.25, help='탐지 신뢰도 임계값')
    parser.add_argument(
        '--crop-ratio',
        default='',
        help='크롭 비율 x1,y1,x2,y2 (예: 0.15,0.42,0.95,1.0)',
    )
    parser.add_argument('--save-dir', default='', help='결과 이미지를 저장할 폴더(선택)')
    parser.add_argument('--no-show', action='store_true', help='창을 띄우지 않고 콘솔/저장만 수행')
    parsed, _ = parser.parse_known_args(args)

    crop_ratio = None
    if parsed.crop_ratio:
        parts = [float(v.strip()) for v in parsed.crop_ratio.split(',')]
        if len(parts) != 4:
            raise ValueError('--crop-ratio는 x1,y1,x2,y2 4개 숫자여야 합니다.')
        crop_ratio = (parts[0], parts[1], parts[2], parts[3])

    run_image_detection(
        input_path=parsed.input,
        model_path=parsed.model,
        width=parsed.width,
        height=parsed.height,
        conf=parsed.conf,
        crop_ratio=crop_ratio,
        save_dir=parsed.save_dir,
        no_show=parsed.no_show,
    )


if __name__ == '__main__':
    main()
