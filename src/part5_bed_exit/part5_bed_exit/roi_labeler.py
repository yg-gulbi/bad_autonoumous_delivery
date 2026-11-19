"""실제 카메라 화면에서 침대 ROI 네 점을 클릭해 설정 JSON을 만든다."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

import cv2
import numpy as np


WINDOW_TITLE = 'Part 5 Bed ROI Labeler'


def normalize_points(points, width, height):
    """픽셀 좌표 네 개를 노드가 사용하는 0~1 정규화 좌표로 바꾼다."""
    if width < 2 or height < 2:
        raise ValueError('Frame width and height must be at least 2')
    if len(points) != 4:
        raise ValueError('Exactly four ROI points are required')
    values = np.asarray(points, dtype=np.float64)
    if values.shape != (4, 2) or not np.isfinite(values).all():
        raise ValueError('ROI points must be four finite [x, y] pairs')
    if len(np.unique(values, axis=0)) != 4:
        raise ValueError('ROI points must not be duplicated')
    if not cv2.isContourConvex(values.astype(np.float32)):
        raise ValueError('Click four bed corners in boundary order without crossing')
    if abs(cv2.contourArea(values.astype(np.float32))) < 1.0:
        raise ValueError('ROI polygon area is too small')
    values[:, 0] /= width - 1
    values[:, 1] /= height - 1
    if np.any((values < 0.0) | (values > 1.0)):
        raise ValueError('ROI points must be inside the camera frame')
    return [[round(float(x), 6), round(float(y), 6)] for x, y in values]


def build_method_config(points, frame_shape, output_path, camera_index,
                        method='segmentation', device='cpu'):
    """클릭 결과를 바로 ``method_config``로 쓸 수 있는 JSON 객체로 만든다."""
    height, width = frame_shape[:2]
    output_path = Path(output_path).expanduser().resolve()
    package_root = Path(__file__).resolve().parents[1]
    model_dir = os.path.relpath(package_root / 'models', output_path.parent)
    return {
        'method': method,
        'model_dir': model_dir,
        'device': device,
        'roi': normalize_points(points, width, height),
        'roi_metadata': {
            'camera_index': camera_index,
            'frame_width': width,
            'frame_height': height,
            'created_at_utc': datetime.now(timezone.utc).isoformat(),
            'source': 'manual_camera_four_point_labeler',
        },
    }


def save_config(path, config, force=False):
    """기존 파일 보호와 임시 파일 교체를 사용해 설정 JSON을 저장한다."""
    path = Path(path).expanduser().resolve()
    if path.exists() and not force:
        raise FileExistsError(f'{path} already exists; use --force to replace it')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    temporary.replace(path)
    return path


class CameraRoiLabeler:
    """카메라 프레임 고정, 네 점 클릭과 미리보기를 관리한다."""

    def __init__(self):
        self.frame = None
        self.frozen_frame = None
        self.frozen = False
        self.points = []

    def on_mouse(self, event, x, y, _flags, _param):
        if event == cv2.EVENT_LBUTTONDOWN and self.frame is not None:
            if not self.frozen:
                self.frozen_frame = self.frame.copy()
                self.frozen = True
            if len(self.points) < 4:
                self.points.append((int(x), int(y)))
        elif event == cv2.EVENT_RBUTTONDOWN and self.points:
            self.points.pop()

    def reset(self):
        self.points.clear()

    def resume_live(self):
        self.points.clear()
        self.frozen_frame = None
        self.frozen = False

    def canvas(self):
        source = self.frozen_frame if self.frozen else self.frame
        canvas = source.copy()
        for index, point in enumerate(self.points):
            cv2.circle(canvas, point, 6, (0, 255, 255), -1)
            cv2.putText(
                canvas, str(index + 1), (point[0] + 8, point[1] - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2,
            )
        if len(self.points) >= 2:
            cv2.polylines(
                canvas,
                [np.asarray(self.points, dtype=np.int32)],
                len(self.points) == 4,
                (0, 255, 255),
                2,
            )
        state = 'FROZEN' if self.frozen else 'LIVE'
        text = f'{state} | click 4 corners ({len(self.points)}/4) | S save R reset SPACE live Q quit'
        cv2.rectangle(canvas, (0, 0), (canvas.shape[1], 34), (20, 20, 20), -1)
        cv2.putText(
            canvas, text, (8, 23), cv2.FONT_HERSHEY_SIMPLEX,
            0.48, (255, 255, 255), 1, cv2.LINE_AA,
        )
        return canvas


def run(camera_index, output_path, method='segmentation', device='cpu', force=False):
    """카메라 GUI를 실행하고 사용자가 승인한 네 점을 JSON으로 저장한다."""
    capture = cv2.VideoCapture(camera_index)
    if not capture.isOpened():
        raise RuntimeError(f'Cannot open camera_index={camera_index}')
    labeler = CameraRoiLabeler()
    try:
        ok, frame = capture.read()
        if not ok or frame is None or frame.size == 0:
            raise RuntimeError(f'Cannot read camera_index={camera_index}')
        labeler.frame = frame
        cv2.namedWindow(WINDOW_TITLE, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(WINDOW_TITLE, labeler.on_mouse)
        while True:
            if not labeler.frozen:
                try:
                    ok, frame = capture.read()
                except Exception as error:
                    raise RuntimeError(f'Camera read failed: {error}') from error
                if not ok or frame is None or frame.size == 0:
                    raise RuntimeError(f'Cannot read camera_index={camera_index}')
                labeler.frame = frame
            cv2.imshow(WINDOW_TITLE, labeler.canvas())
            key = cv2.waitKey(20) & 0xFF
            if key in (ord('q'), 27):
                return None
            if key == ord('r'):
                labeler.reset()
            elif key == ord(' '):
                if labeler.frozen:
                    labeler.resume_live()
                else:
                    labeler.frozen_frame = labeler.frame.copy()
                    labeler.frozen = True
            elif key == ord('s'):
                if len(labeler.points) != 4:
                    print(f'Four points are required; currently {len(labeler.points)}')
                    continue
                try:
                    config = build_method_config(
                        labeler.points,
                        labeler.canvas().shape,
                        output_path,
                        camera_index,
                        method,
                        device,
                    )
                except ValueError as error:
                    print(f'Invalid ROI: {error}')
                    continue
                return save_config(output_path, config, force)
    finally:
        capture.release()
        cv2.destroyAllWindows()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description='Click four bed corners on a live camera frame and save method_config JSON.',
    )
    parser.add_argument('--camera-index', type=int, default=0)
    parser.add_argument('--output', default='camera_roi_methods.json')
    parser.add_argument(
        '--method',
        choices=('detection', 'segmentation', 'yolo_pose', 'mediapipe_pose'),
        default='segmentation',
    )
    parser.add_argument('--device', default='cpu')
    parser.add_argument('--force', action='store_true')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        saved = run(
            args.camera_index,
            args.output,
            args.method,
            args.device,
            args.force,
        )
    except Exception as error:
        print(f'ROI labeling failed: {error}')
        return 1
    if saved is None:
        print('ROI labeling canceled; no file written')
        return 0
    print(f'Saved ROI method_config: {saved}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
