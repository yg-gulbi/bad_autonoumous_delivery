#!/usr/bin/env python3
"""승인된 Part 5 연구 데이터셋 열 개의 다운로드 절차를 통합한 CLI 도구.

데이터셋별 접근 방식과 미리보기 선택을 분기하고, 접근 권한이 필요한 자료는 우회하지
않고 안내 파일을 남긴다. 실제 파일 전송 보조 함수는 ``downloads.py``에 있다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

RESEARCH_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = RESEARCH_ROOT.parent
sys.path.insert(0, str(PACKAGE_ROOT))

from research.dataset_download_label.downloads import (  # noqa: E402
    download_blanketset_public_metadata,
    download_fallvision_files,
    download_file,
    download_huggingface_dataset,
    download_roboflow,
    download_roboflow_previews,
    download_slp_archive,
    download_slp_miccai19_public_rgb,
    download_slp_samples,
    ensure_free_space,
    extract_zip,
    fallvision_inventory,
    huggingface_inventory,
    save_json,
)

DEFAULT_ROOT = RESEARCH_ROOT / "data" / "datasets"
CATALOG_PATH = Path(__file__).resolve().parent / "dataset_catalog.json"

REFERENCE_ARCHIVES = {
    "slp2022": (
        "https://codeload.github.com/ostadabbas/SLP-Dataset-and-Code/zip/refs/heads/master",
        "SLP-Dataset-and-Code.zip",
    ),
    "tip_pimesh": (
        "https://codeload.github.com/USTCWzy/PIMesh/zip/refs/heads/main",
        "PIMesh.zip",
    ),
    "hp_bed": (
        "https://library.imaging.org/admin/apis/public/api/ist/website/downloadArticle/ei/33/8/art00006",
        "BED_Net_paper.pdf",
    ),
    "blanketgen2_fit3d": (
        "https://arxiv.org/pdf/2501.12318",
        "BlanketGen2_Fit3D_paper.pdf",
    ),
}


def load_catalog() -> list[dict]:
    """승인된 데이터셋 카탈로그 JSON을 읽고 고유 항목 열 개인지 검증한다."""
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    datasets = data["datasets"]
    if len(datasets) != 10 or len({item["id"] for item in datasets}) != 10:
        raise RuntimeError("dataset_catalog.json must contain exactly ten unique datasets")
    return datasets


def write_access_note(dataset: dict, directory: Path) -> Path:
    """자동 다운로드가 불가능한 데이터셋의 접근 절차 안내 파일을 작성한다."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "ACCESS_REQUIRED.md"
    path.write_text(
        "\n".join(
            [
                f"# {dataset['name']}",
                "",
                f"- Official source: {dataset['url']}",
                f"- Access state: {dataset['access']}",
                f"- Download rule: {dataset['download_function']}",
                f"- License note: {dataset['license']}",
                "",
                "No access control was bypassed. Add provider-approved files under `raw/`, then rerun",
                "`research/dataset_download_label/dataset_preview.py` to build a viewable index.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def download_reference(dataset_id: str, directory: Path) -> list[str]:
    """데이터셋별 공식 코드·문서 등 작은 참조 압축 파일만 내려받는다."""
    spec = REFERENCE_ARCHIVES.get(dataset_id)
    if not spec:
        return []
    url, name = spec
    path = download_file(url, directory / "reference" / name, max_bytes=500 * 1024**2)
    outputs = [str(path)]
    if path.suffix.lower() == ".zip":
        extract_dir = directory / "reference" / "extracted"
        extract_zip(path, extract_dir, max_bytes=2 * 1024**3)
        outputs.append(str(extract_dir))
    return outputs


def select_patientcare_previews(inventory: list[dict], per_class: int = 3) -> list[str]:
    """PatientCare 목록에서 침대 관련 행동별 대표 미리보기를 고른다."""
    classes = (
        "patient_lying_on_bed",
        "patient_sitting_up_from_bed",
        "standing_up_from_bed",
    )
    selected = []
    for class_name in classes:
        matches = [item["path"] for item in inventory if class_name in item["path"] and item["path"].endswith(".mp4")]
        selected.extend(matches[:per_class])
    return selected


def select_fallvision_previews(inventory: list[dict]) -> list[int]:
    """Select the smallest raw Bed/Fall and Bed/No Fall RARs plus pose CSVs.

    FallVision v2 is distributed as 60 RAR archives rather than individual
    MP4/CSV files.  A preview must therefore choose bounded archives explicitly.
    """
    selected = []
    for category in ("Fall", "No Fall"):
        directory = f"Fall Detection Video Dataset/{category}/Bed/Raw Video"
        raw = [item for item in inventory
               if item.get("directory") == directory
               and item["name"].lower().endswith(".rar")
               and not item.get("restricted")]
        if not raw:
            raise RuntimeError(f"No unrestricted FallVision archive in {directory}")
        chosen = min(raw, key=lambda item: item["bytes"])
        selected.append(chosen["id"])

        stem = Path(chosen["name"]).stem.replace("_raw_", "_mask_")
        pose = [item for item in inventory
                if item["name"].startswith(stem)
                and "points_csv" in item["name"]
                and item["name"].lower().endswith(".rar")
                and not item.get("restricted")]
        if pose:
            selected.append(min(pose, key=lambda item: item["bytes"])["id"])
    return selected


def run_one(dataset: dict, root: Path, mode: str) -> dict:
    """카탈로그 항목 하나를 지정 모드로 처리하고 결과 요약을 반환한다."""
    dataset_id = dataset["id"]
    directory = root / dataset_id
    directory.mkdir(parents=True, exist_ok=True)
    result = {"id": dataset_id, "name": dataset["name"], "status": "pending", "files": []}
    try:
        result["files"].extend(download_reference(dataset_id, directory))
        if dataset_id == "roboflow_bed_exit_v4":
            if mode == "preview":
                result["files"].extend(str(path) for path in download_roboflow_previews(directory / "previews"))
                result["files"].append(str(write_access_note(dataset, directory)))
                result["status"] = "preview_downloaded_full_access_required"
            elif not os.getenv("ROBOFLOW_API_KEY"):
                result["files"].append(str(write_access_note(dataset, directory)))
                result["status"] = "access_required"
            else:
                result["files"].append(str(download_roboflow(directory / "raw")))
                result["status"] = "downloaded"
        elif dataset_id == "fallvision":
            inventory = fallvision_inventory(directory)
            ids = [f["id"] for f in inventory if not f["restricted"]]
            if mode == "preview":
                ids = select_fallvision_previews(inventory)
            if not ids:
                raise RuntimeError("No downloadable FallVision files matched the requested mode")
            required = sum(f["bytes"] for f in inventory if f["id"] in ids)
            ensure_free_space(directory, required)
            result["files"].extend(str(p) for p in download_fallvision_files(
                inventory, ids, directory, max_bytes=required))
            result["status"] = "downloaded"
        elif dataset_id == "slp2022":
            if mode == "full":
                ensure_free_space(directory, 17_534_988_296)
                result["files"].append(str(download_slp_archive(directory)))
            if os.getenv("SLP_ZIP_PASSWORD"):
                sample_count = 3 if mode == "preview" else 10
                download_slp_samples(directory / "samples", sample_count)
                result["files"].append(str(directory / "samples"))
                result["status"] = "downloaded"
            else:
                public_files = download_slp_miccai19_public_rgb(directory / "miccai19_public_unlabeled")
                result["files"].append(str(directory / "miccai19_public_unlabeled"))
                result["files"].append(str(write_access_note(dataset, directory)))
                result["status"] = ("downloaded_archive_and_public_unlabeled_subset_access_required"
                                    if mode == "full" else
                                    "public_unlabeled_subset_downloaded_full_access_required")
                result["public_unlabeled_images"] = len(public_files)
        elif dataset_id == "blanketset":
            result["files"].extend(str(path) for path in download_blanketset_public_metadata(
                directory / "public_metadata"))
            result["files"].append(str(write_access_note(dataset, directory)))
            result["status"] = "public_metadata_downloaded_video_access_required"
        elif dataset_id == "patientcare":
            if mode == "preview":
                inventory = huggingface_inventory(directory)
                paths = select_patientcare_previews(inventory)
                if not paths:
                    raise RuntimeError("No target bed-action MP4 paths found in Patient Care inventory")
                patterns = paths
                max_bytes = 2 * 1024**3
            else:
                patterns = None
                max_bytes = 8 * 1024**3
            result["files"].extend(str(p) for p in download_huggingface_dataset(
                directory, patterns=patterns, max_total_bytes=max_bytes))
            result["status"] = "downloaded"
        else:
            result["files"].append(str(write_access_note(dataset, directory)))
            result["status"] = "access_required"
    except Exception as exc:  # Each provider must be reported independently.
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["files"].append(str(write_access_note(dataset, directory)))
    return result


def main() -> int:
    """대상 데이터셋과 모드를 해석해 순차 다운로드·요약 저장을 수행한다."""
    catalog = load_catalog()
    by_id = {item["id"]: item for item in catalog}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--mode", choices=("preview", "full"), default="preview")
    parser.add_argument("--dataset", choices=tuple(by_id), help="Download exactly one dataset")
    parser.add_argument("--list", action="store_true", help="List dataset IDs without downloading")
    args = parser.parse_args()
    if args.list:
        for item in catalog:
            print(f"{item['id']}: {item['name']}")
        return 0
    if not args.dataset:
        parser.error("--dataset is required; bulk download is intentionally disabled")

    args.root.mkdir(parents=True, exist_ok=True)
    print(f"[{args.dataset}] {args.mode} download", flush=True)
    result = run_one(by_id[args.dataset], args.root, args.mode)
    print(f"  -> {result['status']}", flush=True)
    status_path = args.root / "status" / f"{args.dataset}.json"
    save_json(status_path, {
        "mode": args.mode,
        "catalog": str(CATALOG_PATH),
        "result": result,
    })
    print(f"Status: {status_path}")
    return 1 if result["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
