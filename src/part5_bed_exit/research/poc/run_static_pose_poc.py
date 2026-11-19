#!/usr/bin/env python3
"""고정된 Roboflow 이미지 20장에서 YOLO Pose와 MediaPipe Pose를 비교하는 POC.

라벨을 해석해 조건별 샘플을 고르고 두 모델의 관절·ROI 판정을 실행해 개별 이미지와
요약 표를 저장한다. 정적 자료에서 Pose 적용 가능성을 확인하기 위한 연구 경로다.
"""
from __future__ import annotations

import argparse
import csv
import importlib.metadata
import json
import platform
import time
from collections import Counter
from pathlib import Path

import cv2

from part5_bed_exit.methods.bed_exit_core import (
    JudgmentUnavailable,
    MediaPipePoseROI,
    PolygonROI,
    YoloPoseROI,
)
from research.dataset_download_label.downloads import save_json, sha256
from research.poc.pose_visualization import (
    build_pose_record,
    render_comparison_sheet,
    render_pose_observation,
    write_image,
)


CLASS_NAMES = {0: "bed_exit", 1: "hospital_bed", 2: "in_bed"}
QUOTAS = {"bed_exit": 6, "in_bed": 6, "empty_bed": 8}
MODEL_FILES = {
    "yolo_pose": "yolo11n-pose.pt",
    "mediapipe_pose": "pose_landmarker_full.task",
}
CSV_FIELDS = (
    "method", "model", "dataset", "split", "image_id", "image_sha256",
    "preprocessing", "roi_version", "roi_source", "raw_classes",
    "expected_bool", "predicted_bool", "inference_time_ms", "pose_detected",
    "decision_reason", "failure_reason", "notes",
)


def parse_yolo_labels(path: Path) -> list[dict]:
    """YOLO 라벨 파일을 클래스·BBox·면적 사전 목록으로 해석한다."""
    labels = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        fields = line.split()
        if not fields:
            continue
        if len(fields) != 5:
            raise ValueError(f"{path}:{line_number}: expected five YOLO fields")
        class_id = int(fields[0])
        if class_id not in CLASS_NAMES:
            raise ValueError(f"{path}:{line_number}: unknown class {class_id}")
        x, y, width, height = map(float, fields[1:])
        if not all(0 <= value <= 1 for value in (x, y, width, height)):
            raise ValueError(f"{path}:{line_number}: coordinates must be normalized")
        x1, y1, x2, y2 = x - width / 2, y - height / 2, x + width / 2, y + height / 2
        if x1 < 0 or y1 < 0 or x2 > 1 or y2 > 1 or x1 >= x2 or y1 >= y2:
            raise ValueError(f"{path}:{line_number}: invalid normalized box")
        labels.append({"class_id": class_id, "box": [x1, y1, x2, y2],
                       "area": width * height})
    return labels


def _largest(labels: list[dict], class_id: int) -> dict | None:
    """지정 클래스 라벨 중 면적이 가장 큰 항목을 반환한다."""
    matches = [label for label in labels if label["class_id"] == class_id]
    return max(matches, key=lambda label: label["area"], default=None)


def classify_sample(labels: list[dict]) -> str | None:
    """라벨 조합을 POC의 이탈·침대 안·모호 조건으로 분류한다."""
    classes = {label["class_id"] for label in labels}
    if 1 not in classes:
        return None
    if 0 in classes and 2 not in classes:
        return "bed_exit"
    if 2 in classes and 0 not in classes:
        return "in_bed"
    if classes == {1}:
        return "empty_bed"
    return None


def discover_samples(dataset_root: Path) -> list[dict]:
    """test 우선순위와 조건별 할당량에 따라 고정 평가 샘플을 고른다."""
    buckets = {category: [] for category in QUOTAS}
    for split in ("test", "valid", "train"):
        for image_path in sorted((dataset_root / split / "images").glob("*.jpg")):
            label_path = dataset_root / split / "labels" / f"{image_path.stem}.txt"
            if not label_path.is_file():
                raise FileNotFoundError(label_path)
            labels = parse_yolo_labels(label_path)
            category = classify_sample(labels)
            if category is None:
                continue
            roi_label = _largest(labels, 1)
            patient_label = _largest(labels, 0 if category == "bed_exit" else 2)
            roi_box = roi_label["box"]
            sample = {
                "category": category,
                "expected_bool": True if category == "bed_exit" else False if category == "in_bed" else None,
                "split": split,
                "image_id": image_path.stem,
                "image_path": image_path,
                "label_path": label_path,
                "roi": [[roi_box[0], roi_box[1]], [roi_box[2], roi_box[1]],
                        [roi_box[2], roi_box[3]], [roi_box[0], roi_box[3]]],
                "patient_bbox": None if patient_label is None else patient_label["box"],
                "raw_classes": sorted({CLASS_NAMES[label["class_id"]] for label in labels}),
            }
            buckets[category].append(sample)
    missing = {category: quota - len(buckets[category]) for category, quota in QUOTAS.items()
               if len(buckets[category]) < quota}
    if missing:
        raise RuntimeError(f"Roboflow sample quotas unavailable: {missing}")
    return [sample for category in QUOTAS for sample in buckets[category][:QUOTAS[category]]]


def _configure(method, sample: dict) -> None:
    """독립 이미지 샘플의 ROI를 메서드에 적용한다."""
    roi = PolygonROI(sample["roi"])
    method.roi = roi
    method.selector.roi = roi
    method.reset()


def _text_bool(value: bool | None) -> str:
    """선택적 불리언을 CSV에 쓸 소문자 문자열로 변환한다."""
    return "" if value is None else str(value).lower()


def _environment() -> dict:
    """POC 재현을 위한 플랫폼·Python·패키지 버전을 수집한다."""
    packages = {}
    for name in ("numpy", "opencv-python", "torch", "ultralytics", "mediapipe"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    import torch
    return {"python": platform.python_version(), "platform": platform.platform(),
            "packages": packages, "device": "cpu", "cuda_available": torch.cuda.is_available()}


def run(dataset_root: Path, model_dir: Path, output_dir: Path) -> dict:
    """고정 정적 샘플에서 두 Pose 모델을 실행하고 표·이미지를 저장한다."""
    dataset_root, model_dir, output_dir = map(Path, (dataset_root, model_dir, output_dir))
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite POC output: {output_dir}")
    samples = discover_samples(dataset_root)
    output_dir.mkdir(parents=True)
    full_roi = PolygonROI([[0, 0], [1, 0], [1, 1], [0, 1]])
    load_started = time.perf_counter()
    methods = {
        "yolo_pose": YoloPoseROI(full_roi, model_dir / MODEL_FILES["yolo_pose"], device="cpu"),
        "mediapipe_pose": MediaPipePoseROI(full_roi, model_dir / MODEL_FILES["mediapipe_pose"]),
    }
    model_load_ms = (time.perf_counter() - load_started) * 1000
    rows = []
    pose_records = []
    comparison_items = []
    try:
        warmup_frame = cv2.imread(str(samples[0]["image_path"]), cv2.IMREAD_COLOR)
        if warmup_frame is None:
            raise ValueError(f"Cannot decode {samples[0]['image_path']}")
        for method in methods.values():
            _configure(method, samples[0])
            try:
                method.judge(warmup_frame.copy())
            except JudgmentUnavailable:
                pass
        for sample in samples:
            frame = cv2.imread(str(sample["image_path"]), cv2.IMREAD_COLOR)
            if frame is None:
                raise ValueError(f"Cannot decode {sample['image_path']}")
            rendered = {}
            for method_name, method in methods.items():
                _configure(method, sample)
                started = time.perf_counter()
                predicted = None
                failure = ""
                try:
                    predicted = method.judge(frame.copy())
                except Exception as exc:
                    failure = method.last.reason if isinstance(exc, JudgmentUnavailable) else f"{type(exc).__name__}: {exc}"
                elapsed_ms = (time.perf_counter() - started) * 1000
                visualization_path = Path(method_name) / f"{sample['image_id']}.jpg"
                annotated = render_pose_observation(
                    frame,
                    method_name=method_name,
                    model_name=MODEL_FILES[method_name],
                    roi=method.roi,
                    candidates=method.last_candidates,
                    diagnostics=method.last,
                    predicted=predicted,
                    inference_time_ms=elapsed_ms,
                    failure_reason=failure,
                    point_conf=method.point_conf,
                    tolerance_px=method.tolerance_px,
                )
                write_image(output_dir / visualization_path, annotated)
                rendered[method_name] = annotated
                pose_record = build_pose_record(
                    method_name=method_name,
                    model_name=MODEL_FILES[method_name],
                    split=sample["split"],
                    image_id=sample["image_id"],
                    roi=method.roi,
                    shape=frame.shape,
                    candidates=method.last_candidates,
                    diagnostics=method.last,
                    predicted=predicted,
                    inference_time_ms=elapsed_ms,
                    failure_reason=failure,
                    point_conf=method.point_conf,
                    tolerance_px=method.tolerance_px,
                )
                pose_record["visualization_path"] = visualization_path.as_posix()
                pose_records.append(pose_record)
                rows.append({
                    "method": method_name,
                    "model": MODEL_FILES[method_name],
                    "dataset": "Roboflow Bed Exit v4",
                    "split": sample["split"],
                    "image_id": sample["image_id"],
                    "image_sha256": sha256(sample["image_path"]),
                    "preprocessing": "Roboflow auto-orient + stretch 512x512; OpenCV BGR",
                    "roi_version": "roboflow_hospital_bed_box_v1",
                    "roi_source": "largest hospital_bed YOLO box; static POC only",
                    "raw_classes": ",".join(sample["raw_classes"]),
                    "expected_bool": _text_bool(sample["expected_bool"]),
                    "predicted_bool": _text_bool(predicted),
                    "inference_time_ms": f"{elapsed_ms:.3f}",
                    "pose_detected": str(predicted is not None).lower(),
                    "decision_reason": method.last.reason,
                    "failure_reason": failure,
                    "notes": "empty bed expects no decision" if sample["category"] == "empty_bed" else "class-derived provisional expected bool",
                })
            comparison = cv2.hconcat([rendered["yolo_pose"], rendered["mediapipe_pose"]])
            write_image(output_dir / "comparison" / f"{sample['image_id']}.jpg", comparison)
            comparison_items.append((sample["image_id"], comparison))
    finally:
        for method in methods.values():
            method.close()
    write_image(output_dir / "pose_comparison_sheet.jpg", render_comparison_sheet(comparison_items))
    with (output_dir / "results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    with (output_dir / "keypoints.jsonl").open("w", encoding="utf-8") as handle:
        for record in pose_records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    summary = {
        "status": "complete",
        "dataset_root": str(dataset_root.resolve()),
        "dataset_manifest_sha256": sha256(dataset_root / "data.yaml"),
        "selection": [{key: value for key, value in sample.items()
                       if key not in ("image_path", "label_path")} for sample in samples],
        "selection_counts": dict(Counter(sample["category"] for sample in samples)),
        "environment": _environment(),
        "model_load_ms": model_load_ms,
        "warmup_frames_per_method": 1,
        "weights": {name: {"file": MODEL_FILES[name],
                            "sha256": sha256(model_dir / MODEL_FILES[name])} for name in methods},
        "rows": len(rows),
        "artifacts": {
            "keypoints_jsonl": "keypoints.jsonl",
            "yolo_pose_images": len(samples),
            "mediapipe_pose_images": len(samples),
            "comparison_images": len(samples),
            "comparison_sheet": "pose_comparison_sheet.jpg",
        },
        "method_summary": {},
        "limitations": [
            "Static images do not validate the runtime 3-of-5 temporal vote.",
            "Expected bool is a provisional Roboflow class proxy, not reviewed clinical ground truth.",
            "The hospital_bed label box is a per-image POC ROI, not a reviewed deployment-camera ROI.",
        ],
    }
    for method_name in methods:
        selected = [row for row in rows if row["method"] == method_name]
        valid = [row for row in selected if row["predicted_bool"]]
        summary["method_summary"][method_name] = {
            "images": len(selected), "valid_decisions": len(valid),
            "failures": dict(Counter(row["failure_reason"] for row in selected if row["failure_reason"])),
            "mean_inference_ms": sum(float(row["inference_time_ms"]) for row in selected) / len(selected),
        }
    save_json(output_dir / "summary.json", summary)
    return summary


def main() -> int:
    """기본 경로와 명령행 인자를 조합해 정적 Pose POC를 실행한다."""
    package_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, default=package_root / "research/data/datasets/roboflow_bed_exit_v4/raw")
    parser.add_argument("--model-dir", type=Path, default=package_root / "models")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    summary = run(args.dataset_root, args.model_dir, args.output_dir)
    print(f"POC complete: {summary['rows']} rows -> {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
