#!/usr/bin/env python3
"""Create local JPEG thumbnails and an HTML index for downloaded datasets."""
from __future__ import annotations

import argparse
import html
from pathlib import Path

import cv2

RESEARCH_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = RESEARCH_ROOT / "data" / "datasets"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv", ".webm"}


def read_preview(path: Path):
    if path.suffix.lower() in IMAGE_SUFFIXES:
        return cv2.imread(str(path))
    capture = cv2.VideoCapture(str(path))
    try:
        ok, frame = capture.read()
        return frame if ok else None
    finally:
        capture.release()


def build_previews(root: Path, per_dataset: int = 12) -> Path:
    preview_root = root / "previews"
    preview_root.mkdir(parents=True, exist_ok=True)
    sections = []
    for dataset_dir in sorted(p for p in root.iterdir() if p.is_dir() and p.name != "previews"):
        candidates = [
            p for p in sorted(dataset_dir.rglob("*"))
            if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES | VIDEO_SUFFIXES
        ]
        cards = []
        for index, source in enumerate(candidates[:per_dataset], start=1):
            frame = read_preview(source)
            if frame is None:
                continue
            height, width = frame.shape[:2]
            scale = min(1.0, 640 / max(width, 1))
            if scale < 1.0:
                frame = cv2.resize(frame, (round(width * scale), round(height * scale)))
            target_dir = preview_root / dataset_dir.name
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{index:02d}.jpg"
            if not cv2.imwrite(str(target), frame, [cv2.IMWRITE_JPEG_QUALITY, 88]):
                continue
            image_href = target.relative_to(root).as_posix()
            source_text = source.relative_to(root).as_posix()
            cards.append(
                f'<figure><a href="{html.escape(image_href)}"><img src="{html.escape(image_href)}"></a>'
                f'<figcaption>{html.escape(source_text)}</figcaption></figure>'
            )
        access_note = dataset_dir / "ACCESS_REQUIRED.md"
        note = ""
        if access_note.is_file():
            note = f'<p><a href="{html.escape(access_note.relative_to(root).as_posix())}">Access status</a></p>'
        sections.append(
            f"<section><h2>{html.escape(dataset_dir.name)}</h2>{note}"
            + ("<div class=grid>" + "".join(cards) + "</div>" if cards else "<p>No viewable media downloaded.</p>")
            + "</section>"
        )
    index = root / "preview_index.html"
    index.write_text(
        "<!doctype html><meta charset=utf-8><title>Part 5 dataset previews</title>"
        "<style>body{font:15px sans-serif;margin:24px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}"
        "figure{margin:0;border:1px solid #ccc;padding:8px}img{width:100%;height:220px;object-fit:contain;background:#111}"
        "figcaption{overflow-wrap:anywhere;margin-top:6px;color:#333}</style>"
        "<h1>Part 5 dataset previews</h1>" + "".join(sections),
        encoding="utf-8",
    )
    return index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--per-dataset", type=int, default=12)
    args = parser.parse_args()
    if args.per_dataset < 1:
        parser.error("--per-dataset must be positive")
    if not args.root.is_dir():
        parser.error(f"Dataset root does not exist: {args.root}")
    print(build_previews(args.root, args.per_dataset))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
