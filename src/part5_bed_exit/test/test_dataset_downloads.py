import importlib.util
import json
from pathlib import Path

import pytest

from research.datasets import downloads


ROOT = Path(__file__).resolve().parents[1]


def load_cli_module():
    spec = importlib.util.spec_from_file_location(
        "download_datasets", ROOT / "research" / "datasets" / "download_datasets.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_catalog_has_exactly_ten_unique_dataset_ids():
    data = json.loads(
        (ROOT / "research" / "datasets" / "dataset_catalog.json").read_text(encoding="utf-8"))
    ids = [item["id"] for item in data["datasets"]]
    assert len(ids) == 10
    assert len(set(ids)) == 10
    assert {"enighttrack", "hp_bed", "blanketgen2_fit3d", "patientcare"} <= set(ids)


def test_patientcare_preview_selector_uses_three_bed_actions():
    cli = load_cli_module()
    inventory = []
    for action in (
        "patient_lying_on_bed",
        "patient_sitting_up_from_bed",
        "standing_up_from_bed",
        "walking_with_normal_gait",
    ):
        for index in range(5):
            inventory.append({"path": f"real/{action}/{index}.mp4", "bytes": 10})
    selected = cli.select_patientcare_previews(inventory, per_class=2)
    assert len(selected) == 6
    assert all("walking_with_normal_gait" not in path for path in selected)


def test_fallvision_preview_selector_uses_smallest_bed_archives_and_pose_csvs():
    cli = load_cli_module()
    inventory = [
        {"id": 1, "name": "f_raw_b_1.rar", "directory":
         "Fall Detection Video Dataset/Fall/Bed/Raw Video", "bytes": 900},
        {"id": 2, "name": "f_raw_b_3.rar", "directory":
         "Fall Detection Video Dataset/Fall/Bed/Raw Video", "bytes": 400},
        {"id": 3, "name": "f_mask_b_3_keypoints_csv.rar", "directory":
         "Fall Detection Video Dataset/Fall/Bed/Mask Video", "bytes": 10},
        {"id": 4, "name": "nf_raw_b_1.rar", "directory":
         "Fall Detection Video Dataset/No Fall/Bed/Raw Video", "bytes": 800},
        {"id": 5, "name": "nf_raw_b_2.rar", "directory":
         "Fall Detection Video Dataset/No Fall/Bed/Raw Video", "bytes": 200},
        {"id": 6, "name": "nf_mask_b_2_ketpoints_csv.rar", "directory":
         "Fall Detection Video Dataset/No Fall/Bed/Mask Video", "bytes": 8},
    ]
    for item in inventory:
        item["restricted"] = False
    assert cli.select_fallvision_previews(inventory) == [2, 3, 5, 6]


def test_resumable_download_rejects_html(tmp_path, monkeypatch):
    class Response:
        status_code = 200
        headers = {"Content-Type": "text/html", "Content-Length": "4"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def raise_for_status(self):
            return None

    monkeypatch.setattr(downloads.requests, "get", lambda *args, **kwargs: Response())
    with pytest.raises(RuntimeError, match="HTML"):
        downloads.download_file_resumable(
            "https://example.com/data.zip", tmp_path / "data.zip", max_bytes=100)
    assert not (tmp_path / "data.zip").exists()
