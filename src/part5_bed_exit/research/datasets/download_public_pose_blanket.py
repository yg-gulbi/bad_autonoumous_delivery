#!/usr/bin/env python3
"""Download one verified public in-bed pose or blanket dataset at a time."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RESEARCH_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = RESEARCH_ROOT.parent
sys.path.insert(0, str(PACKAGE_ROOT))

from research.datasets.downloads import (  # noqa: E402
    download_blanketgen2_fit3d,
    download_blanketset_public_metadata,
    download_fallvision_files,
    download_mannequin_rgb_in_bed,
    download_slp_miccai19_public_rgb,
    fallvision_inventory,
    save_json,
)
from research.datasets.download_datasets import select_fallvision_previews  # noqa: E402

DATASETS = {
    "mannequin_rgb_in_bed": "312 RGB images with LSP14 pose labels; no blanket",
    "slp_miccai19_public": "45 real thick-blanket RGB images; pose labels encrypted",
    "blanketset_metadata": "Public condition table and documentation; videos restricted",
    "blanketgen2_fit3d": "Synthetic transparent blanket layers; original Fit3D is separate",
    "fallvision_pose_preview": "Bed RGB archives with YOLOv7-generated pose CSV; not ground truth",
}


def run(dataset: str, root: Path, mode: str) -> dict:
    if dataset=="mannequin_rgb_in_bed":
        directory=root/"mannequin_rgb_in_bed"
        downloaded=download_mannequin_rgb_in_bed(directory)
        files=[directory/"raw"/"colorManneNumbered.zip",directory/"extracted",
               directory/"download_manifest.json"]
        status="downloaded"
    elif dataset=="slp_miccai19_public":
        directory=root/"slp2022"/"miccai19_public_unlabeled"
        downloaded=download_slp_miccai19_public_rgb(directory)
        files=[directory/"raw",directory/"download_manifest.json"]
        status="public_unlabeled_subset_downloaded"
    elif dataset=="blanketset_metadata":
        directory=root/"blanketset"/"public_metadata"
        downloaded=download_blanketset_public_metadata(directory)
        files=downloaded
        status="metadata_downloaded_videos_restricted"
    elif dataset=="blanketgen2_fit3d":
        directory=root/"blanketgen2_fit3d"
        downloaded=download_blanketgen2_fit3d(directory,include_videos=mode=="full")
        files=downloaded
        status="overlays_downloaded" if mode=="full" else "supplement_downloaded"
    elif dataset=="fallvision_pose_preview":
        directory=root/"fallvision"
        inventory=fallvision_inventory(directory)
        ids=select_fallvision_previews(inventory)
        required=sum(item["bytes"] for item in inventory if item["id"] in ids)
        downloaded=download_fallvision_files(inventory,ids,directory,max_bytes=required)
        files=downloaded
        status="preview_archives_downloaded"
    else:  # argparse prevents this path.
        raise ValueError(dataset)
    directory.mkdir(parents=True,exist_ok=True)
    result={"dataset":dataset,"description":DATASETS[dataset],"mode":mode,
            "status":status,"downloaded_items":len(downloaded),
            "artifacts":[str(path) for path in files]}
    save_json(directory/"status.json",result)
    return result


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset",choices=tuple(DATASETS))
    parser.add_argument("--mode",choices=("preview","full"),default="preview",
                        help="Only BlanketGen2 full mode downloads videos.zip (~29.8 GB).")
    parser.add_argument("--root",type=Path,
                        default=RESEARCH_ROOT/"data"/"datasets")
    parser.add_argument("--list",action="store_true")
    args=parser.parse_args()
    if args.list:
        for key,value in DATASETS.items():
            print(f"{key}: {value}")
        return 0
    if not args.dataset:
        parser.error("--dataset is required; bulk download is intentionally disabled")
    result=run(args.dataset,args.root,args.mode)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
