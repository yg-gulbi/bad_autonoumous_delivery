from pathlib import Path

import pytest

from research.poc.run_static_pose_poc import (
    QUOTAS,
    classify_sample,
    discover_samples,
    parse_yolo_labels,
)


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
