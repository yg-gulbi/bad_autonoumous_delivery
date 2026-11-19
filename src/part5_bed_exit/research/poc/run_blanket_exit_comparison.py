#!/usr/bin/env python3
"""실제·합성 담요 가림 조건에서 여러 침대 경계 이탈 단서를 비교하는 POC.

공개 이탈 정답이 없는 SLP RGB, 경계 통과 여부를 아는 마네킹 합성 자료, 생성 모델의
담요 제거 복원 자료를 서로 섞지 않고 별도 근거로 평가한다. 운영 판정 경로가 아니며
여러 휴리스틱·Pose·앙상블의 결과표와 시각화 산출물을 만드는 대형 연구 스크립트다.
"""
from __future__ import annotations

import argparse
import csv
import importlib.metadata
import json
import platform
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from part5_bed_exit.methods.bed_exit_core import (
    BedExitMethod,
    Candidate,
    DetectionROI,
    JudgmentUnavailable,
    MediaPipePoseROI,
    PolygonROI,
    SegmentationROI,
    YoloPoseROI,
)
from research.dataset_download_label.downloads import save_json, sha256
from research.poc.pose_visualization import METHOD_METADATA, write_image


MODEL_FILES = {
    "detection": "yolo11n.pt",
    "segmentation": "yolo11n-seg.pt",
    "yolo_pose": "yolo11n-pose.pt",
    "mediapipe_pose": "pose_landmarker_full.task",
}
ENSEMBLE_METHODS = {
    "ensemble_pose_positive_else_segment": {
        "family": "ensemble",
        "model": "YOLO-seg sensitive + YOLO-pose any + MediaPipe-pose any",
    },
    "ensemble_conservative_positive": {
        "family": "ensemble",
        "model": "positive-any; SAFE requires segmentation and pose agreement",
    },
}
MANNEQUIN_ROI = (
    (0.168, 0.074),
    (0.862, 0.074),
    (0.862, 0.925),
    (0.168, 0.925),
)
MANNEQUIN_IMAGES = ("000078.jpg", "000156.jpg", "000312.jpg")
CSV_FIELDS = (
    "track", "sample_id", "variant", "source_path", "source_sha256",
    "method", "method_family", "model", "expected_bool", "predicted_bool",
    "valid", "decision_reason", "score", "candidate_count",
    "inference_time_ms", "roi_version", "notes",
)


@dataclass(frozen=True)
class Sample:
    """실험 트랙 하나의 입력 이미지·ROI·정답·출처 메타데이터."""

    track: str
    sample_id: str
    variant: str
    path: Path
    roi: PolygonROI
    roi_version: str
    expected: bool | None
    notes: str


@dataclass
class MethodSpec:
    """비교 메서드 객체와 계열·표시명·Pose 백엔드를 묶은 설정."""

    name: str
    family: str
    model: str
    method: BedExitMethod
    pose_backend: str | None = None


class HaarFaceAnchorROI(BedExitMethod):
    """사람 얼굴만 기준점으로 쓰는 약하지만 해석 가능한 고전 기준선."""

    def __init__(self, roi: PolygonROI, outside_fraction: float = 0.25):
        """Haar 얼굴 검출기와 ROI 밖 허용 BBox 비율을 초기화한다."""
        super().__init__(roi)
        root = Path(cv2.data.haarcascades)
        self.frontal = cv2.CascadeClassifier(
            str(root / "haarcascade_frontalface_default.xml")
        )
        self.profile = cv2.CascadeClassifier(
            str(root / "haarcascade_profileface.xml")
        )
        if self.frontal.empty() or self.profile.empty():
            raise RuntimeError("OpenCV Haar cascades are unavailable")
        self.outside_fraction = outside_fraction

    @staticmethod
    def _nms(boxes: list[np.ndarray], threshold: float = 0.35) -> list[np.ndarray]:
        """겹치는 얼굴 BBox를 IoU 기반 비최대 억제로 제거한다."""
        if not boxes:
            return []
        pending = sorted(boxes, key=lambda box: float(np.prod(box[2:] - box[:2])),
                         reverse=True)
        kept: list[np.ndarray] = []
        while pending:
            chosen = pending.pop(0)
            kept.append(chosen)
            survivors = []
            for other in pending:
                lo = np.maximum(chosen[:2], other[:2])
                hi = np.minimum(chosen[2:], other[2:])
                inter = float(np.prod(np.maximum(hi - lo, 0)))
                union = float(np.prod(chosen[2:] - chosen[:2])
                              + np.prod(other[2:] - other[:2]) - inter)
                if inter / max(union, 1e-9) < threshold:
                    survivors.append(other)
            pending = survivors
        return kept

    def candidates(self, frame: np.ndarray) -> list[Candidate]:
        """정면·측면 Haar 검출 결과를 환자 후보 BBox 목록으로 만든다."""
        scale = min(1.0, 560.0 / max(frame.shape[:2]))
        small = cv2.resize(frame, None, fx=scale, fy=scale,
                           interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        min_face = max(24, round(min(small.shape[:2]) * 0.035))
        boxes: list[np.ndarray] = []
        for cascade, image, mirrored in (
            (self.frontal, gray, False),
            (self.profile, gray, False),
            (self.profile, cv2.flip(gray, 1), True),
        ):
            found = cascade.detectMultiScale(
                image, scaleFactor=1.08, minNeighbors=5,
                minSize=(min_face, min_face),
            )
            for x, y, width, height in found:
                if mirrored:
                    x = gray.shape[1] - x - width
                boxes.append(np.array(
                    [x / scale, y / scale, (x + width) / scale,
                     (y + height) / scale], dtype=np.float32
                ))
        return [Candidate(box=box, confidence=1.0)
                for box in self._nms(boxes)]

    def decide(self, candidate: Candidate, shape) -> tuple[bool, float, str]:
        """선택된 얼굴 BBox의 ROI 밖 비율로 이탈 여부를 판단한다."""
        score = self.roi.box_outside(candidate.bbox, shape)
        return score > self.outside_fraction, score, "face_box_outside_fraction"


class BlueBlanketProxyROI(BedExitMethod):
    """파란 직물의 경계 넘침만 보는 대리 지표이며 환자 근거로 사용하지 않는다."""

    def __init__(self, roi: PolygonROI, outside_fraction: float = 0.05):
        """파란 담요 마스크의 ROI 밖 허용 비율을 초기화한다."""
        super().__init__(roi)
        self.outside_fraction = outside_fraction

    def candidates(self, frame: np.ndarray) -> list[Candidate]:
        """HSV 색상·형태 연산으로 가장 큰 파란 직물 마스크 후보를 만든다."""
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, (85, 22, 38), (132, 205, 245))
        radius = max(3, round(min(frame.shape[:2]) / 180))
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1)
        )
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        minimum = frame.shape[0] * frame.shape[1] * 0.01
        out = []
        for index in range(1, count):
            x, y, width, height, area = stats[index]
            if area < minimum:
                continue
            component = labels == index
            out.append(Candidate(
                bbox=np.array([x, y, x + width, y + height], dtype=np.float32),
                confidence=float(area / (frame.shape[0] * frame.shape[1])),
                mask=component,
            ))
        return out

    def decide(self, candidate: Candidate, shape) -> tuple[bool | None, float | None, str]:
        """담요 마스크의 ROI 밖 픽셀 비율을 대리 이탈 지표로 계산한다."""
        if candidate.mask is None or not np.any(candidate.mask):
            return None, None, "empty_blanket_mask"
        area = int(np.count_nonzero(candidate.mask))
        outside = int(np.count_nonzero(candidate.mask & ~self.roi.mask(shape)))
        score = outside / area
        return score > self.outside_fraction, score, "blanket_spill_proxy"


def load_roi_config(path: Path) -> tuple[dict, PolygonROI]:
    """사람이 검수한 단일 ROI 설정을 읽고 다각형 객체를 만든다."""
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    if config.get("roi_reviewed") is not True:
        raise ValueError("roi_reviewed must be true")
    return config, PolygonROI(config["roi"])


def make_methods(roi: PolygonROI, model_dir: Path) -> dict[str, MethodSpec]:
    """담요 비교에 사용할 휴리스틱·YOLO·Pose 메서드 집합을 생성한다."""
    from ultralytics import YOLO

    model_dir = Path(model_dir)
    for filename in MODEL_FILES.values():
        if not (model_dir / filename).is_file():
            raise FileNotFoundError(model_dir / filename)
    detection_model = YOLO(str(model_dir / MODEL_FILES["detection"]), task="detect")
    segmentation_model = YOLO(
        str(model_dir / MODEL_FILES["segmentation"]), task="segment"
    )
    pose_model = YOLO(str(model_dir / MODEL_FILES["yolo_pose"]), task="pose")
    specs = [
        MethodSpec(
            "yolo_detect_standard", "detection", MODEL_FILES["detection"],
            DetectionROI(roi, model_dir / MODEL_FILES["detection"], model=detection_model,
                         device="cpu", confidence=0.5, imgsz=640,
                         outside_fraction=0.05),
        ),
        MethodSpec(
            "yolo_detect_sensitive", "detection", MODEL_FILES["detection"],
            DetectionROI(roi, model_dir / MODEL_FILES["detection"], model=detection_model,
                         device="cpu", confidence=0.05, imgsz=960,
                         outside_fraction=0.05),
        ),
        MethodSpec(
            "yolo_segment_standard", "segmentation", MODEL_FILES["segmentation"],
            SegmentationROI(roi, model_dir / MODEL_FILES["segmentation"],
                            model=segmentation_model, device="cpu", confidence=0.5,
                            imgsz=640, outside_fraction=0.05),
        ),
        MethodSpec(
            "yolo_segment_sensitive", "segmentation", MODEL_FILES["segmentation"],
            SegmentationROI(roi, model_dir / MODEL_FILES["segmentation"],
                            model=segmentation_model, device="cpu", confidence=0.05,
                            imgsz=960, outside_fraction=0.05),
        ),
        MethodSpec(
            "yolo_pose_any", "pose", MODEL_FILES["yolo_pose"],
            YoloPoseROI(roi, model_dir / MODEL_FILES["yolo_pose"], model=pose_model,
                        device="cpu", confidence=0.5, imgsz=640, policy="any_body",
                        point_conf=0.35),
            pose_backend="yolo_pose",
        ),
        MethodSpec(
            "yolo_pose_torso_sensitive", "pose", MODEL_FILES["yolo_pose"],
            YoloPoseROI(roi, model_dir / MODEL_FILES["yolo_pose"], model=pose_model,
                        device="cpu", confidence=0.05, imgsz=960, policy="torso",
                        point_conf=0.20),
            pose_backend="yolo_pose",
        ),
        MethodSpec(
            "mediapipe_pose_any", "pose", MODEL_FILES["mediapipe_pose"],
            MediaPipePoseROI(roi, model_dir / MODEL_FILES["mediapipe_pose"],
                             policy="any_body", point_conf=0.35),
            pose_backend="mediapipe_pose",
        ),
        MethodSpec(
            "mediapipe_pose_torso", "pose", MODEL_FILES["mediapipe_pose"],
            MediaPipePoseROI(roi, model_dir / MODEL_FILES["mediapipe_pose"],
                             policy="torso", point_conf=0.20),
            pose_backend="mediapipe_pose",
        ),
        MethodSpec(
            "haar_face_anchor", "face_anchor", "OpenCV Haar cascades",
            HaarFaceAnchorROI(roi),
        ),
        MethodSpec(
            "blue_blanket_proxy", "blanket_proxy", "HSV connected component",
            BlueBlanketProxyROI(roi),
        ),
    ]
    return {spec.name: spec for spec in specs}


def configure_roi(spec: MethodSpec, roi: PolygonROI) -> None:
    """독립 샘플의 ROI를 메서드와 환자 선택기에 함께 적용한다."""
    spec.method.roi = roi
    spec.method.selector.roi = roi
    spec.method.reset()


def _selected(candidate: Candidate, bbox) -> bool:
    """후보가 메서드에서 최종 선택한 환자 BBox인지 비교한다."""
    return bbox is not None and np.allclose(
        candidate.bbox, np.asarray(bbox), rtol=0, atol=1e-3
    )


def _status_text(predicted: bool | None) -> tuple[str, tuple[int, int, int]]:
    """선택적 판정을 화면 문구와 BGR 상태 색상으로 변환한다."""
    if predicted is None:
        return "UNAVAILABLE", (30, 210, 255)
    if predicted:
        return "EXIT", (40, 40, 240)
    return "SAFE", (60, 210, 80)


def _draw_pose(canvas: np.ndarray, candidate: Candidate, backend: str,
               selected: bool, point_conf: float) -> None:
    """YOLO·MediaPipe 백엔드에 맞는 Pose 연결선과 관절을 그린다."""
    if candidate.points is None:
        return
    points = np.asarray(candidate.points, dtype=float)
    usable = np.isfinite(points).all(axis=1) & (points[:, 2] >= point_conf)
    metadata = METHOD_METADATA[backend]
    for start, end in metadata["connections"]:
        if start < len(points) and end < len(points) and usable[start] and usable[end]:
            cv2.line(
                canvas,
                tuple(np.rint(points[start, :2]).astype(int)),
                tuple(np.rint(points[end, :2]).astype(int)),
                (220, 220, 220) if selected else (105, 105, 105),
                4 if selected else 2,
                cv2.LINE_AA,
            )
    common = set(metadata["common"])
    for index, point in enumerate(points):
        if usable[index]:
            color = (60, 220, 80) if index in common else (255, 170, 60)
            if not selected:
                color = (120, 120, 120)
            cv2.circle(canvas, tuple(np.rint(point[:2]).astype(int)),
                       7 if selected else 4, color, -1, cv2.LINE_AA)


def render_tile(frame: np.ndarray, roi: PolygonROI, spec: MethodSpec | None,
                predicted: bool | None = None, elapsed_ms: float = 0.0,
                tile_width: int = 300) -> np.ndarray:
    """메서드 하나의 후보·ROI·판정·처리시간을 단일 타일로 렌더링한다."""
    canvas = frame.copy()
    roi_pixels = np.rint(roi.pixels(frame.shape)).astype(np.int32)
    cv2.polylines(canvas, [roi_pixels], True, (255, 0, 255), 6, cv2.LINE_AA)
    if spec is None:
        title, reason, score = "INPUT", "image + mattress ROI", None
        status, status_color = "SOURCE", (245, 245, 245)
    else:
        method = spec.method
        for candidate in method.last_candidates:
            is_selected = _selected(candidate, method.last.bbox)
            if candidate.mask is not None:
                mask = np.asarray(candidate.mask, dtype=bool)
                if mask.shape == frame.shape[:2]:
                    overlay = np.zeros_like(canvas)
                    overlay[mask & roi.mask(frame.shape)] = (45, 185, 65)
                    overlay[mask & ~roi.mask(frame.shape)] = (30, 30, 235)
                    canvas = cv2.addWeighted(canvas, 1.0, overlay, 0.38, 0)
            box = np.rint(candidate.bbox).astype(int)
            cv2.rectangle(canvas, tuple(box[:2]), tuple(box[2:]),
                          (255, 210, 40) if is_selected else (115, 115, 115),
                          5 if is_selected else 2)
            if spec.pose_backend:
                _draw_pose(canvas, candidate, spec.pose_backend, is_selected,
                           float(getattr(method, "point_conf", 0.35)))
        title = spec.name
        reason = method.last.reason
        score = method.last.score
        status, status_color = _status_text(predicted)
    height = round(frame.shape[0] * tile_width / frame.shape[1])
    tile = cv2.resize(canvas, (tile_width, height), interpolation=cv2.INTER_AREA)
    header = np.zeros((78, tile_width, 3), dtype=np.uint8)
    cv2.putText(header, title[:35], (8, 21), cv2.FONT_HERSHEY_SIMPLEX,
                0.48, (245, 245, 245), 1, cv2.LINE_AA)
    cv2.putText(header, status, (8, 45), cv2.FONT_HERSHEY_SIMPLEX,
                0.60, status_color, 2, cv2.LINE_AA)
    detail = reason
    if score is not None:
        detail += f" s={score:.3f}"
    if spec is not None:
        detail += f" {elapsed_ms:.0f}ms"
    cv2.putText(header, detail[:43], (8, 68), cv2.FONT_HERSHEY_SIMPLEX,
                0.37, (210, 210, 210), 1, cv2.LINE_AA)
    return np.vstack((header, tile))


def render_ensemble_tile(frame: np.ndarray, roi: PolygonROI, name: str,
                         predicted: bool | None, reason: str,
                         elapsed_ms: float, tile_width: int = 300) -> np.ndarray:
    """앙상블 이름·판정·근거를 ROI 위에 표시한 타일을 만든다."""
    canvas = frame.copy()
    roi_pixels = np.rint(roi.pixels(frame.shape)).astype(np.int32)
    cv2.polylines(canvas, [roi_pixels], True, (255, 0, 255), 6, cv2.LINE_AA)
    height = round(frame.shape[0] * tile_width / frame.shape[1])
    tile = cv2.resize(canvas, (tile_width, height), interpolation=cv2.INTER_AREA)
    status, status_color = _status_text(predicted)
    header = np.zeros((78, tile_width, 3), dtype=np.uint8)
    cv2.putText(header, name[:35], (8, 21), cv2.FONT_HERSHEY_SIMPLEX,
                0.43, (245, 245, 245), 1, cv2.LINE_AA)
    cv2.putText(header, status, (8, 45), cv2.FONT_HERSHEY_SIMPLEX,
                0.60, status_color, 2, cv2.LINE_AA)
    cv2.putText(header, f"{reason} {elapsed_ms:.0f}ms"[:43], (8, 68),
                cv2.FONT_HERSHEY_SIMPLEX, 0.37, (210, 210, 210),
                1, cv2.LINE_AA)
    return np.vstack((header, tile))


def tile_grid(tiles: list[np.ndarray], columns: int = 4) -> np.ndarray:
    """크기가 같은 결과 타일을 지정 열 수의 격자로 배치한다."""
    if not tiles:
        raise ValueError("tiles cannot be empty")
    target_height = max(tile.shape[0] for tile in tiles)
    target_width = max(tile.shape[1] for tile in tiles)
    normalized = []
    for tile in tiles:
        canvas = np.zeros((target_height, target_width, 3), dtype=np.uint8)
        canvas[:tile.shape[0], :tile.shape[1]] = tile
        normalized.append(canvas)
    blank = np.zeros_like(normalized[0])
    rows = []
    for start in range(0, len(normalized), columns):
        row = normalized[start:start + columns]
        rows.append(np.hstack(row + [blank] * (columns - len(row))))
    return np.vstack(rows)


def contact_sheet(items: list[tuple[str, np.ndarray]], columns: int = 3,
                  item_width: int = 600) -> np.ndarray:
    """여러 샘플 이미지를 제목과 함께 검수용 연락 시트로 배치한다."""
    rendered = []
    for label, image in items:
        height = round(image.shape[0] * item_width / image.shape[1])
        small = cv2.resize(image, (item_width, height), interpolation=cv2.INTER_AREA)
        header = np.zeros((40, item_width, 3), np.uint8)
        cv2.putText(header, label[:70], (8, 27), cv2.FONT_HERSHEY_SIMPLEX,
                    0.58, (245, 245, 245), 1, cv2.LINE_AA)
        rendered.append(np.vstack((header, small)))
    return tile_grid(rendered, columns=columns)


def _translate(image: np.ndarray, dx: int, dy: int,
               interpolation: int) -> np.ndarray:
    """이미지 또는 마스크를 지정 픽셀만큼 평행 이동한다."""
    matrix = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(
        image, matrix, (image.shape[1], image.shape[0]),
        flags=interpolation, borderMode=cv2.BORDER_CONSTANT, borderValue=0,
    )


def composite_person(frame: np.ndarray, person_mask: np.ndarray,
                     dx: int, dy: int) -> tuple[np.ndarray, np.ndarray]:
    """사람 픽셀을 배경에서 분리해 이동시킨 합성 프레임과 마스크를 만든다."""
    mask = np.asarray(person_mask, dtype=np.uint8)
    if mask.shape != frame.shape[:2] or not np.any(mask):
        raise ValueError("person mask must be a nonempty frame-size mask")
    radius = max(5, round(min(frame.shape[:2]) / 100))
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1)
    )
    inpaint_mask = cv2.dilate(mask, kernel, iterations=2) * 255
    background = cv2.inpaint(frame, inpaint_mask, radius, cv2.INPAINT_TELEA)
    moved_image = _translate(frame * mask[..., None], dx, dy, cv2.INTER_LINEAR)
    moved_mask = _translate(mask, dx, dy, cv2.INTER_NEAREST) > 0
    feather = cv2.GaussianBlur(moved_mask.astype(np.float32), (0, 0), radius / 2)
    feather = np.clip(feather, 0, 1)[..., None]
    composite = np.clip(
        background.astype(np.float32) * (1 - feather)
        + moved_image.astype(np.float32) * feather,
        0, 255,
    ).astype(np.uint8)
    return composite, moved_mask


def add_textured_blanket(frame: np.ndarray, person_mask: np.ndarray,
                         texture: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """사람 하체 영역에 크기 조정한 담요 질감을 합성한다."""
    ys, xs = np.nonzero(person_mask)
    if not len(xs):
        raise ValueError("person mask cannot be empty")
    x1, x2, y1, y2 = xs.min(), xs.max(), ys.min(), ys.max()
    width, height = x2 - x1 + 1, y2 - y1 + 1
    top = y1 + round(height * 0.20)
    margin_x = round(width * 0.16)
    margin_y = round(height * 0.05)
    polygon = np.array([
        [x1 - margin_x, top], [x2 + margin_x, top + round(height * 0.02)],
        [x2 + margin_x, y2 + margin_y], [x1 - margin_x, y2 + margin_y],
    ], np.int32)
    polygon[:, 0] = np.clip(polygon[:, 0], 0, frame.shape[1] - 1)
    polygon[:, 1] = np.clip(polygon[:, 1], 0, frame.shape[0] - 1)
    mask = np.zeros(frame.shape[:2], np.uint8)
    cv2.fillPoly(mask, [polygon], 255)
    target = cv2.resize(texture, (frame.shape[1], frame.shape[0]),
                        interpolation=cv2.INTER_CUBIC)
    blur = max(5, round(min(frame.shape[:2]) / 120))
    alpha = cv2.GaussianBlur(mask.astype(np.float32) / 255.0, (0, 0), blur)
    alpha = np.clip(alpha * 0.97, 0, 0.97)[..., None]
    result = np.clip(
        frame.astype(np.float32) * (1 - alpha)
        + target.astype(np.float32) * alpha,
        0, 255,
    ).astype(np.uint8)
    return result, mask > 0


def largest_person_mask(segmentation_model, frame: np.ndarray) -> np.ndarray:
    """Segmentation 결과에서 면적이 가장 큰 사람 마스크를 반환한다."""
    result = segmentation_model.predict(
        source=frame, classes=[0], conf=0.10, imgsz=960, device="cpu",
        retina_masks=True, verbose=False,
    )[0]
    if result.boxes is None or result.masks is None or len(result.boxes) == 0:
        raise RuntimeError("YOLO segmentation could not isolate mannequin person")
    boxes = result.boxes.xyxy.cpu().numpy()
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    mask = result.masks.data[int(np.argmax(areas))].cpu().numpy() > 0.5
    if mask.shape != frame.shape[:2]:
        raise ValueError("Expected original-resolution person mask")
    return mask


def build_synthetic_samples(mannequin_dir: Path, texture_path: Path,
                            output_dir: Path, segmentation_model) -> list[Sample]:
    """마네킹 사람 이동과 담요 합성으로 정답을 아는 경계 통과 샘플을 만든다."""
    mannequin_dir, output_dir = Path(mannequin_dir), Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    texture_frame = cv2.imread(str(texture_path), cv2.IMREAD_COLOR)
    if texture_frame is None:
        raise ValueError(f"Cannot decode blanket texture source: {texture_path}")
    h, w = texture_frame.shape[:2]
    texture = texture_frame[round(h * 0.32):round(h * 0.88),
                            round(w * 0.18):round(w * 0.82)]
    roi = PolygonROI(MANNEQUIN_ROI)
    variants = (
        ("safe_uncovered", 0.00, 0.00, False),
        ("safe_covered", 0.00, 0.00, True),
        ("left_exit_covered", -0.24, 0.00, True),
        ("right_exit_covered", 0.24, 0.00, True),
        ("bottom_exit_covered", 0.00, 0.18, True),
    )
    samples = []
    manifest = []
    for filename in MANNEQUIN_IMAGES:
        source = mannequin_dir / filename
        frame = cv2.imread(str(source), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError(f"Cannot decode {source}")
        person_mask = largest_person_mask(segmentation_model, frame)
        for variant, dx_fraction, dy_fraction, covered in variants:
            dx = round(frame.shape[1] * dx_fraction)
            dy = round(frame.shape[0] * dy_fraction)
            if variant == "safe_uncovered":
                composite, moved_mask = frame.copy(), person_mask.copy()
            else:
                composite, moved_mask = composite_person(frame, person_mask, dx, dy)
            blanket_mask = None
            if covered:
                composite, blanket_mask = add_textured_blanket(
                    composite, moved_mask, texture
                )
            outside_fraction = float(
                np.count_nonzero(moved_mask & ~roi.mask(frame.shape))
                / max(np.count_nonzero(moved_mask), 1)
            )
            expected = outside_fraction > 0.05
            sample_id = f"{Path(filename).stem}_{variant}"
            output_path = output_dir / f"{sample_id}.jpg"
            write_image(output_path, composite)
            samples.append(Sample(
                track="synthetic_boundary",
                sample_id=sample_id,
                variant=variant,
                path=output_path,
                roi=roi,
                roi_version="mannequin_visible_mattress_v1",
                expected=expected,
                notes=(
                    f"deterministic composite; dx={dx}; dy={dy}; "
                    f"person_mask_outside={outside_fraction:.6f}; "
                    f"textured_blanket={covered}"
                ),
            ))
            manifest.append({
                "sample_id": sample_id,
                "source": str(source.resolve()),
                "variant": variant,
                "dx_px": dx,
                "dy_px": dy,
                "covered": covered,
                "expected_bool": expected,
                "person_mask_outside_fraction": outside_fraction,
                "blanket_mask_area": (None if blanket_mask is None
                                      else int(np.count_nonzero(blanket_mask))),
                "path": str(output_path.resolve()),
                "sha256": sha256(output_path),
            })
    save_json(output_dir / "manifest.json", {"samples": manifest})
    return samples


def real_samples(real_dir: Path, roi: PolygonROI, roi_version: str,
                 max_real: int) -> list[Sample]:
    """SLP 실제 RGB 파일을 정답 없는 관찰용 샘플 목록으로 만든다."""
    paths = sorted(Path(real_dir).glob("*.png"))[:max_real]
    if not paths:
        raise FileNotFoundError(f"No PNG frames in {real_dir}")
    return [Sample(
        track="real_blanket_unlabeled",
        sample_id=path.stem,
        variant="cover2",
        path=path,
        roi=roi,
        roi_version=roi_version,
        expected=None,
        notes="Real SLP cover2 RGB; no public bed-exit or pose ground truth",
    ) for path in paths]


def restoration_samples(original: Path, restored: Path, roi: PolygonROI,
                        roi_version: str) -> list[Sample]:
    """원본과 생성형 담요 제거 복원 이미지를 별도 실험 샘플로 만든다."""
    samples = []
    for variant, path, note in (
        ("covered_original", Path(original),
         "Real blanket image; also present in the 45-frame track"),
        ("ai_blanket_removed", Path(restored),
         "Generated reconstruction; body pose is hallucinated and not evidence"),
    ):
        if not path.is_file():
            raise FileNotFoundError(path)
        samples.append(Sample(
            track="ai_restoration_qualitative",
            sample_id=f"image_000026_{variant}",
            variant=variant,
            path=path,
            roi=roi,
            roi_version=roi_version,
            expected=None,
            notes=note,
        ))
    return samples


def _bool_text(value: bool | None) -> str:
    """선택적 불리언을 CSV용 소문자 문자열로 변환한다."""
    return "" if value is None else str(value).lower()


def derive_ensemble_predictions(
    predictions: dict[str, bool | None],
) -> dict[str, tuple[bool | None, str, tuple[str, ...]]]:
    """개별 메서드 판정으로 보수적 Pose·마스크 앙상블 결과를 계산한다."""
    components = (
        "yolo_segment_sensitive", "yolo_pose_any", "mediapipe_pose_any"
    )
    segment, yolo_pose, mediapipe_pose = (
        predictions[name] for name in components
    )
    pose_positive_else_segment = (
        True if yolo_pose is True or mediapipe_pose is True else segment
    )
    if any(value is True for value in (segment, yolo_pose, mediapipe_pose)):
        conservative = True
    elif segment is False and any(
        value is False for value in (yolo_pose, mediapipe_pose)
    ):
        conservative = False
    else:
        conservative = None
    return {
        "ensemble_pose_positive_else_segment": (
            pose_positive_else_segment,
            "pose_positive_else_segment",
            components,
        ),
        "ensemble_conservative_positive": (
            conservative,
            "positive_any_safe_agreement",
            components,
        ),
    }


def run_sample(sample: Sample, methods: dict[str, MethodSpec],
               output_dir: Path) -> tuple[list[dict], np.ndarray]:
    """샘플 하나에 모든 메서드·앙상블을 실행하고 행 데이터와 패널을 만든다."""
    frame = cv2.imread(str(sample.path), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError(f"Cannot decode {sample.path}")
    rows = []
    tiles = [render_tile(frame, sample.roi, None)]
    predictions: dict[str, bool | None] = {}
    elapsed_by_method: dict[str, float] = {}
    for spec in methods.values():
        configure_roi(spec, sample.roi)
        started = time.perf_counter()
        predicted = None
        failure = ""
        try:
            predicted = spec.method.judge(frame.copy())
        except Exception as exc:
            failure = (spec.method.last.reason if isinstance(exc, JudgmentUnavailable)
                       else f"{type(exc).__name__}: {exc}")
        elapsed_ms = (time.perf_counter() - started) * 1000
        predictions[spec.name] = predicted
        elapsed_by_method[spec.name] = elapsed_ms
        tiles.append(render_tile(frame, sample.roi, spec, predicted, elapsed_ms))
        rows.append({
            "track": sample.track,
            "sample_id": sample.sample_id,
            "variant": sample.variant,
            "source_path": str(sample.path.resolve()),
            "source_sha256": sha256(sample.path),
            "method": spec.name,
            "method_family": spec.family,
            "model": spec.model,
            "expected_bool": _bool_text(sample.expected),
            "predicted_bool": _bool_text(predicted),
            "valid": str(predicted is not None).lower(),
            "decision_reason": spec.method.last.reason,
            "score": ("" if spec.method.last.score is None
                      else f"{spec.method.last.score:.6f}"),
            "candidate_count": len(spec.method.last_candidates),
            "inference_time_ms": f"{elapsed_ms:.3f}",
            "roi_version": sample.roi_version,
            "notes": f"{sample.notes}; failure={failure}" if failure else sample.notes,
        })
    for name, (predicted, reason, components) in derive_ensemble_predictions(
        predictions
    ).items():
        metadata = ENSEMBLE_METHODS[name]
        elapsed_ms = sum(elapsed_by_method[component] for component in components)
        tiles.append(render_ensemble_tile(
            frame, sample.roi, name, predicted, reason, elapsed_ms
        ))
        rows.append({
            "track": sample.track,
            "sample_id": sample.sample_id,
            "variant": sample.variant,
            "source_path": str(sample.path.resolve()),
            "source_sha256": sha256(sample.path),
            "method": name,
            "method_family": metadata["family"],
            "model": metadata["model"],
            "expected_bool": _bool_text(sample.expected),
            "predicted_bool": _bool_text(predicted),
            "valid": str(predicted is not None).lower(),
            "decision_reason": reason,
            "score": "",
            "candidate_count": 0,
            "inference_time_ms": f"{elapsed_ms:.3f}",
            "roi_version": sample.roi_version,
            "notes": f"{sample.notes}; derived from {','.join(components)}",
        })
    grid = tile_grid(tiles, columns=4)
    grid_path = output_dir / sample.track / "comparisons" / f"{sample.sample_id}.jpg"
    write_image(grid_path, grid)
    return rows, grid


def _method_summary(rows: list[dict]) -> dict:
    """프레임별 결과를 메서드·트랙별 판정 수와 시간 통계로 집계한다."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["method"]].append(row)
    output = {}
    for name, selected in grouped.items():
        valid = [row for row in selected if row["valid"] == "true"]
        unavailable = [row for row in selected if row["valid"] != "true"]
        elapsed = [float(row["inference_time_ms"]) for row in selected]
        summary = {
            "samples": len(selected),
            "valid": len(valid),
            "valid_rate": len(valid) / len(selected),
            "exit": sum(row["predicted_bool"] == "true" for row in valid),
            "safe": sum(row["predicted_bool"] == "false" for row in valid),
            "unavailable": len(unavailable),
            "reason_counts": dict(Counter(row["decision_reason"] for row in selected)),
            "latency_ms_median": statistics.median(elapsed),
            "latency_ms_p95": float(np.percentile(elapsed, 95)),
        }
        labeled = [row for row in selected if row["expected_bool"]]
        if labeled:
            correct = sum(row["predicted_bool"] == row["expected_bool"] for row in labeled)
            valid_labeled = [row for row in labeled if row["valid"] == "true"]
            correct_valid = sum(
                row["predicted_bool"] == row["expected_bool"] for row in valid_labeled
            )
            summary.update({
                "labeled_samples": len(labeled),
                "end_to_end_accuracy_unavailable_is_wrong": correct / len(labeled),
                "valid_labeled": len(valid_labeled),
                "conditional_accuracy_when_valid": (
                    None if not valid_labeled else correct_valid / len(valid_labeled)
                ),
                "false_positive": sum(
                    row["expected_bool"] == "false"
                    and row["predicted_bool"] == "true" for row in labeled
                ),
                "false_negative": sum(
                    row["expected_bool"] == "true"
                    and row["predicted_bool"] == "false" for row in labeled
                ),
            })
        output[name] = summary
    return output


def _environment() -> dict:
    """실험 재현을 위한 플랫폼·Python·패키지 버전을 수집한다."""
    packages = {}
    for name in ("numpy", "opencv-python", "torch", "ultralytics", "mediapipe"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": packages,
        "device": "cpu",
    }


def run(args) -> dict:
    """모든 실험 트랙을 준비·실행하고 CSV·JSON·검수 이미지를 저장한다."""
    output_dir = Path(args.output_dir)
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite output: {output_dir}")
    output_dir.mkdir(parents=True)
    config, real_roi = load_roi_config(args.roi_config)
    methods = make_methods(real_roi, args.model_dir)
    # Reuse the already loaded YOLO segmentation model to create composites.
    segmentation_model = methods["yolo_segment_standard"].method.model
    synthetic = build_synthetic_samples(
        args.mannequin_dir, args.blanket_texture,
        output_dir / "synthetic_boundary" / "inputs", segmentation_model,
    )
    samples = real_samples(
        args.real_dir, real_roi, config["roi_version"], args.max_real
    )
    samples.extend(synthetic)
    samples.extend(restoration_samples(
        args.restoration_original, args.restoration_generated,
        real_roi, config["roi_version"],
    ))

    rows = []
    grids: dict[str, list[tuple[str, np.ndarray]]] = defaultdict(list)
    try:
        warmup = cv2.imread(str(samples[0].path), cv2.IMREAD_COLOR)
        for spec in methods.values():
            configure_roi(spec, samples[0].roi)
            try:
                spec.method.judge(warmup.copy())
            except Exception:
                pass
        for index, sample in enumerate(samples, 1):
            sample_rows, grid = run_sample(sample, methods, output_dir)
            rows.extend(sample_rows)
            grids[sample.track].append((sample.sample_id, grid))
            print(f"[{index:02d}/{len(samples):02d}] {sample.track}/{sample.sample_id}",
                  flush=True)
    finally:
        for spec in methods.values():
            spec.method.close()

    with (output_dir / "results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    contact_paths = {}
    for track, items in grids.items():
        chunks = [items[start:start + 9] for start in range(0, len(items), 9)]
        contact_paths[track] = []
        for number, chunk in enumerate(chunks, 1):
            relative = Path(track) / f"contact_sheet_{number:02d}.jpg"
            write_image(output_dir / relative, contact_sheet(chunk))
            contact_paths[track].append(relative.as_posix())

    summaries = {}
    for track in grids:
        summaries[track] = _method_summary(
            [row for row in rows if row["track"] == track]
        )
    summary = {
        "status": "complete",
        "environment": _environment(),
        "roi": {
            "config": str(Path(args.roi_config).resolve()),
            "version": config["roi_version"],
            "source": config["roi_source"],
            "vertices": config["roi"],
            "deployment_approved": bool(config.get("deployment_approved", False)),
        },
        "sample_counts": dict(Counter(sample.track for sample in samples)),
        "method_count": len(methods) + len(ENSEMBLE_METHODS),
        "methods": {
            name: {"family": spec.family, "model": spec.model}
            for name, spec in methods.items()
        } | ENSEMBLE_METHODS,
        "weights": {
            key: {"file": value, "sha256": sha256(Path(args.model_dir) / value)}
            for key, value in MODEL_FILES.items()
        },
        "rows": len(rows),
        "track_method_summary": summaries,
        "artifacts": {
            "results_csv": "results.csv",
            "contact_sheets": contact_paths,
            "per_sample_comparisons": {
                track: len(items) for track, items in grids.items()
            },
            "synthetic_manifest": "synthetic_boundary/inputs/manifest.json",
        },
        "limitations": [
            "The 45 real SLP cover2 RGB frames have no public bed-exit ground truth, so exit/safe counts are predictions, not accuracy.",
            "The synthetic track uses YOLO segmentation to cut out mannequins and OpenCV inpainting for the background; it is a stress test, not clinical validation.",
            "The textured synthetic blanket is an occlusion overlay and does not reproduce real cloth geometry or pressure.",
            "The generated blanket-removal image hallucinates hidden body pose and must not be used as safety evidence or a deployment input.",
            "The blue-blanket proxy detects fabric, not the patient, and is included only to expose its false-evidence failure mode.",
            "Static images do not validate the required 5-frame majority vote or camera-error reset behavior.",
        ],
    }
    save_json(output_dir / "summary.json", summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    """담요 이탈 비교 실험의 명령행 인자 파서를 구성한다."""
    package_root = Path(__file__).resolve().parents[2]
    data_root = package_root / "research" / "data" / "datasets"
    slp_root = data_root / "slp2022"
    real_dir = (slp_root / "miccai19_public_unlabeled" / "raw" / "SLP"
                / "simLab" / "00004" / "RGB" / "cover2")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-dir", type=Path, default=real_dir)
    parser.add_argument(
        "--roi-config", type=Path,
        default=package_root / "research" / "configs"
        / "slp_miccai19_cover2_roi_v1.json",
    )
    parser.add_argument("--model-dir", type=Path, default=package_root / "models")
    parser.add_argument(
        "--mannequin-dir", type=Path,
        default=data_root / "mannequin_rgb_in_bed" / "extracted"
        / "colorManneNumbered" / "images",
    )
    parser.add_argument(
        "--blanket-texture", type=Path, default=real_dir / "image_000026.png"
    )
    parser.add_argument(
        "--restoration-original", type=Path, default=real_dir / "image_000026.png"
    )
    parser.add_argument(
        "--restoration-generated", type=Path,
        default=slp_root / "public_demo_pair"
        / "image_000026_ai_blanket_removed_v1.png",
    )
    parser.add_argument("--max-real", type=int, default=45)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main() -> int:
    """명령행 인자를 읽어 담요 이탈 비교 실험을 실행한다."""
    parser = build_parser()
    args = parser.parse_args()
    if args.max_real < 1:
        parser.error("--max-real must be positive")
    summary = run(args)
    print(json.dumps({
        "status": summary["status"],
        "sample_counts": summary["sample_counts"],
        "method_count": summary["method_count"],
        "rows": summary["rows"],
        "output_dir": str(args.output_dir.resolve()),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
