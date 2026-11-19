"""연구 도구: 데이터셋 카탈로그와 안전한 다운로드 규칙을 검증한다.

공개 데이터셋을 실험 입력으로 수집하는
``research/dataset_download_label/downloads.py``를 위해
만든 테스트다. 데이터셋 ID, 미리보기 선택, 이어받기와 HTML 오류 응답 거부를
대체 객체로 확인한다. 침대 이탈 판정 모듈의 최종 단위·통합시험은 아니다.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from research.dataset_download_label import downloads


ROOT = Path(__file__).resolve().parents[2]


def load_cli_module():
    """CLI 스크립트를 파일 경로에서 독립 모듈로 불러온다."""
    spec = importlib.util.spec_from_file_location(
        "download_datasets",
        ROOT / "research" / "dataset_download_label" / "download_datasets.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_catalog_has_exactly_ten_unique_dataset_ids():
    """데이터셋 카탈로그가 고유 ID 열 개만 포함하는지 검증한다."""
    data = json.loads(
        (ROOT / "research" / "dataset_download_label" / "dataset_catalog.json").read_text(
            encoding="utf-8"
        )
    )
    ids = [item["id"] for item in data["datasets"]]
    assert len(ids) == 10
    assert len(set(ids)) == 10
    assert {"enighttrack", "hp_bed", "blanketgen2_fit3d", "patientcare"} <= set(ids)


def test_patientcare_preview_selector_uses_three_bed_actions():
    """PatientCare 미리보기가 지정된 세 침대 행동을 고르는지 검증한다."""
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
    """FallVision 선택기가 작은 침대 영상과 Pose CSV를 우선하는지 검증한다."""
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
    """재개 다운로드가 HTML 로그인 응답을 데이터로 저장하지 않는지 검증한다."""
    class Response:
        """HTML 본문을 가진 최소 requests 응답 대역."""

        status_code = 200
        headers = {"Content-Type": "text/html", "Content-Length": "4"}

        def __enter__(self):
            """컨텍스트 관리자 진입 시 자기 자신을 반환한다."""
            return self

        def __exit__(self, *args):
            """컨텍스트 종료 시 예외를 억제하지 않는다."""
            return False

        def raise_for_status(self):
            """HTTP 상태 오류가 없는 응답을 모사한다."""
            return None

    monkeypatch.setattr(downloads.requests, "get", lambda *args, **kwargs: Response())
    with pytest.raises(RuntimeError, match="HTML"):
        downloads.download_file_resumable(
            "https://example.com/data.zip", tmp_path / "data.zip", max_bytes=100)
    assert not (tmp_path / "data.zip").exists()
