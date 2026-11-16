import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from part5_bed_exit.methods.bed_exit_core import Candidate, Diagnostics, PolygonROI
from research.poc.run_static_pose_poc import (
    QUOTAS,
    classify_sample,
    discover_samples,
    parse_yolo_labels,
)
from research.poc.pose_visualization import (
    build_pose_record,
    render_comparison_sheet,
    render_pose_observation,
    write_image,
)
from research.poc.run_pose_pair_poc import load_bed_roi_config, prepare_frame


def _write_sample(root: Path, split: str, stem: str, labels: str) -> None:
    (root / split / "images").mkdir(parents=True, exist_ok=True)
    (root / split / "labels").mkdir(parents=True, exist_ok=True)
    (root / split / "images" / f"{stem}.jpg").write_bytes(b"test")
    (root / split / "labels" / f"{stem}.txt").write_text(labels, encoding="utf-8")


def test_parse_and_classify_roboflow_labels(tmp_path):
    path = tmp_path / "labels.txt"
    path.write_text("0 0.5 0.5 0.2 0.4\n1 0.5 0.5 0.8 0.9\n", encoding="utf-8")
    labels = parse_yolo_labels(path)
    assert classify_sample(labels) == "bed_exit"
    assert labels[0]["box"] == pytest.approx([.4, .3, .6, .7])
    path.write_text("3 0.5 0.5 0.2 0.2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown class"):
        parse_yolo_labels(path)


def test_selection_is_quota_bounded_and_test_first(tmp_path):
    labels = {
        "bed_exit": "0 .5 .5 .2 .2\n1 .5 .5 .8 .8\n",
        "in_bed": "2 .5 .5 .2 .2\n1 .5 .5 .8 .8\n",
        "empty_bed": "1 .5 .5 .8 .8\n",
    }
    for category, quota in QUOTAS.items():
        for index in range(quota):
            _write_sample(tmp_path, "test", f"{category}_{index:02d}", labels[category])
        _write_sample(tmp_path, "valid", f"{category}_extra", labels[category])
    samples = discover_samples(tmp_path)
    assert len(samples) == sum(QUOTAS.values())
    assert all(sample["split"] == "test" for sample in samples)
    assert [sample["category"] for sample in samples].count("empty_bed") == 8


def test_pose_artifact_keeps_keypoints_and_marks_roi_exit(tmp_path):
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    roi = PolygonROI([[.1, .1], [.8, .1], [.8, .8], [.1, .8]])
    points = np.tile(np.array([50.0, 50.0, .9]), (17, 1))
    points[9, :2] = [95.0, 50.0]
    candidate = Candidate(np.array([20, 20, 96, 90], dtype=float), .88, points=points)
    diagnostics = Diagnostics(
        valid=True,
        reason="visible_keypoint_outside",
        score=1 / 13,
        bbox=tuple(candidate.bbox),
        points=points.tolist(),
    )
    record = build_pose_record(
        method_name="yolo_pose",
        model_name="test.pt",
        split="test",
        image_id="sample",
        roi=roi,
        shape=frame.shape,
        candidates=[candidate],
        diagnostics=diagnostics,
        predicted=True,
        inference_time_ms=12.3,
        failure_reason="",
        point_conf=.5,
        tolerance_px=3.0,
    )
    assert record["candidate_count"] == 1
    assert record["candidates"][0]["selected_patient"] is True
    assert record["candidates"][0]["keypoints"][9]["name"] == "left_wrist"
    assert record["candidates"][0]["keypoints"][9]["outside_roi"] is True

    rendered = render_pose_observation(
        frame,
        method_name="yolo_pose",
        model_name="test.pt",
        roi=roi,
        candidates=[candidate],
        diagnostics=diagnostics,
        predicted=True,
        inference_time_ms=12.3,
        failure_reason="",
        point_conf=.5,
        tolerance_px=3.0,
    )
    output = tmp_path / "pose.jpg"
    write_image(output, rendered)
    assert rendered.shape == frame.shape
    assert output.stat().st_size > 0
    sheet = render_comparison_sheet([("sample.rf.hash", np.hstack([rendered, rendered]))])
    assert sheet.shape[1] == 1536


def test_pose_pair_frame_upscale_preserves_aspect_ratio(tmp_path):
    path = tmp_path / "input.png"
    assert cv2.imwrite(str(path), np.zeros((216, 120, 3), dtype=np.uint8))
    frame = prepare_frame(path, output_width=480)
    assert frame.shape == (864, 480, 3)
    with pytest.raises(ValueError, match="positive"):
        prepare_frame(path, output_width=0)


def test_pose_pair_requires_reviewed_bed_roi(tmp_path):
    path = tmp_path / "roi.json"
    config = {
        "roi_reviewed": True,
        "roi_version": "pair_v1",
        "roi_source": "manual mattress polygon",
        "deployment_approved": False,
        "roi": [[.1, .1], [.9, .1], [.9, .9], [.1, .9]],
    }
    path.write_text(json.dumps(config), encoding="utf-8")
    loaded, roi = load_bed_roi_config(path)
    assert loaded["deployment_approved"] is False
    assert np.allclose(roi.vertices, config["roi"])

    config["roi_reviewed"] = False
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="roi_reviewed"):
        load_bed_roi_config(path)
