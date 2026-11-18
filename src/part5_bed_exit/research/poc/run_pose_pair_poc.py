#!/usr/bin/env python3
"""비가림·담요 가림 RGB 이미지 한 쌍에서 두 Pose 모델을 비교하는 POC.

검수된 침대 ROI를 불러 YOLO Pose와 MediaPipe Pose의 관절·이탈 판단을 실행하고
CSV, JSON과 비교 이미지를 저장한다. 영상 연속 판단이나 ROS 발행은 하지 않는다.
"""
from __future__ import annotations

import argparse
import csv
import json
import time
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


MODEL_FILES = {
    "yolo_pose": "yolo11n-pose.pt",
    "mediapipe_pose": "pose_landmarker_full.task",
}
CSV_FIELDS = (
    "condition", "method", "model", "image_path", "image_sha256",
    "preprocessing", "roi_version", "roi_source", "roi_normalized",
    "predicted_bool", "inference_time_ms", "candidate_count",
    "decision_reason", "failure_reason", "notes",
)


def prepare_frame(path: Path, output_width: int = 480):
    """이미지를 읽고 종횡비를 유지한 채 지정 너비로 확대·축소한다."""
    frame = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError(f"Cannot decode {path}")
    if output_width < 1:
        raise ValueError("output_width must be positive")
    if frame.shape[1] != output_width:
        height = round(frame.shape[0] * output_width / frame.shape[1])
        frame = cv2.resize(frame, (output_width, height), interpolation=cv2.INTER_CUBIC)
    return frame


def _text_bool(value: bool | None) -> str:
    """선택적 불리언을 CSV에 쓸 소문자 문자열로 변환한다."""
    return "" if value is None else str(value).lower()


def load_bed_roi_config(path: Path) -> tuple[dict, PolygonROI]:
    """이미지 쌍 전용으로 사람이 검수한 침대 다각형 설정을 읽는다."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("roi_reviewed") is not True:
        raise ValueError("Review the pair bed ROI and set roi_reviewed=true")
    version = config.get("roi_version")
    source = config.get("roi_source")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("roi_version is required")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("roi_source is required")
    return config, PolygonROI(config["roi"])


def run(
    uncovered_path: Path,
    covered_path: Path,
    roi_config_path: Path,
    model_dir: Path,
    output_dir: Path,
    output_width: int = 480,
) -> dict:
    """비가림·가림 이미지 쌍에서 두 Pose 모델을 실행하고 결과를 저장한다."""
    paths = {"uncovered": Path(uncovered_path), "covered": Path(covered_path)}
    model_dir, output_dir = Path(model_dir), Path(output_dir)
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite POC output: {output_dir}")
    for path in paths.values():
        if not path.is_file():
            raise FileNotFoundError(path)
    roi_config_path = Path(roi_config_path)
    roi_config, roi = load_bed_roi_config(roi_config_path)
    output_dir.mkdir(parents=True)

    methods = {
        "yolo_pose": YoloPoseROI(roi, model_dir / MODEL_FILES["yolo_pose"], device="cpu"),
        "mediapipe_pose": MediaPipePoseROI(roi, model_dir / MODEL_FILES["mediapipe_pose"]),
    }
    rows = []
    records = []
    comparison_items = []
    try:
        warmup = prepare_frame(paths["uncovered"], output_width)
        for method in methods.values():
            method.reset()
            try:
                method.judge(warmup.copy())
            except JudgmentUnavailable:
                pass

        for condition, path in paths.items():
            frame = prepare_frame(path, output_width)
            rendered = {}
            for method_name, method in methods.items():
                method.reset()
                started = time.perf_counter()
                predicted = None
                failure = ""
                try:
                    predicted = method.judge(frame.copy())
                except Exception as exc:
                    failure = (
                        method.last.reason
                        if isinstance(exc, JudgmentUnavailable)
                        else f"{type(exc).__name__}: {exc}"
                    )
                elapsed_ms = (time.perf_counter() - started) * 1000
                relative_image = Path(method_name) / f"{condition}.jpg"
                annotated = render_pose_observation(
                    frame,
                    method_name=method_name,
                    model_name=MODEL_FILES[method_name],
                    roi=roi,
                    candidates=method.last_candidates,
                    diagnostics=method.last,
                    predicted=predicted,
                    inference_time_ms=elapsed_ms,
                    failure_reason=failure,
                    point_conf=method.point_conf,
                    tolerance_px=method.tolerance_px,
                )
                write_image(output_dir / relative_image, annotated)
                rendered[method_name] = annotated
                record = build_pose_record(
                    method_name=method_name,
                    model_name=MODEL_FILES[method_name],
                    split=condition,
                    image_id=condition,
                    roi=roi,
                    shape=frame.shape,
                    candidates=method.last_candidates,
                    diagnostics=method.last,
                    predicted=predicted,
                    inference_time_ms=elapsed_ms,
                    failure_reason=failure,
                    point_conf=method.point_conf,
                    tolerance_px=method.tolerance_px,
                )
                record["source_image"] = str(path.resolve())
                record["source_image_sha256"] = sha256(path)
                record["visualization_path"] = relative_image.as_posix()
                records.append(record)
                rows.append({
                    "condition": condition,
                    "method": method_name,
                    "model": MODEL_FILES[method_name],
                    "image_path": str(path.resolve()),
                    "image_sha256": sha256(path),
                    "preprocessing": f"OpenCV BGR; cubic upscale to width {output_width}",
                    "roi_version": roi_config["roi_version"],
                    "roi_source": roi_config["roi_source"],
                    "roi_normalized": json.dumps(
                        roi.vertices.tolist(), separators=(",", ":")
                    ),
                    "predicted_bool": _text_bool(predicted),
                    "inference_time_ms": f"{elapsed_ms:.3f}",
                    "candidate_count": len(method.last_candidates),
                    "decision_reason": method.last.reason,
                    "failure_reason": failure,
                    "notes": "SLP official public demo crop; existing ground-truth overlay; no bed-exit label",
                })
            comparison = cv2.hconcat([rendered["yolo_pose"], rendered["mediapipe_pose"]])
            write_image(output_dir / "comparison" / f"{condition}.jpg", comparison)
            comparison_items.append((condition, comparison))
    finally:
        for method in methods.values():
            method.close()

    write_image(
        output_dir / "pose_pair_comparison.jpg",
        render_comparison_sheet(comparison_items, tile_width=960, columns=1),
    )
    with (output_dir / "results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    with (output_dir / "keypoints.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    summary = {
        "status": "complete",
        "dataset": "SLP2022 official public repository demo figure crops",
        "source_note": "Not raw SLP RGB; public demo_2d.PNG already contains ground-truth overlays",
        "inputs": {
            condition: {"path": str(path.resolve()), "sha256": sha256(path)}
            for condition, path in paths.items()
        },
        "preprocessing": f"OpenCV BGR; cubic upscale from 120x216 to {output_width}x{round(216 * output_width / 120)}",
        "roi": {
            "config_path": str(roi_config_path.resolve()),
            "version": roi_config["roi_version"],
            "source": roi_config["roi_source"],
            "normalized_vertices": roi.vertices.tolist(),
            "deployment_approved": bool(roi_config.get("deployment_approved", False)),
        },
        "rows": len(rows),
        "artifacts": {
            "comparison_sheet": "pose_pair_comparison.jpg",
            "keypoints_jsonl": "keypoints.jsonl",
            "results_csv": "results.csv",
        },
        "limitations": [
            "The public demo crops are low-resolution and already contain SLP ground-truth skeleton overlays.",
            "The two crops are different poses and are not a controlled matched-pair accuracy test.",
            "The manually reviewed mattress polygon is specific to these public demo crops and is not a deployment-camera ROI.",
            "Raw SLP RGB requires provider-approved access and SLP_ZIP_PASSWORD.",
        ],
    }
    save_json(output_dir / "summary.json", summary)
    return summary


def main() -> int:
    """명령행 인자를 해석해 이미지 쌍 Pose POC를 실행한다."""
    package_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uncovered", type=Path, required=True)
    parser.add_argument("--covered", type=Path, required=True)
    parser.add_argument("--roi-config", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, default=package_root / "models")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output-width", type=int, default=480)
    args = parser.parse_args()
    summary = run(
        args.uncovered,
        args.covered,
        args.roi_config,
        args.model_dir,
        args.output_dir,
        args.output_width,
    )
    print(f"Pose pair POC complete: {summary['rows']} rows -> {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
