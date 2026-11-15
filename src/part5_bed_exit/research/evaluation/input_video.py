"""Connect downloaded original RGB media to the local notebook."""

import os
from pathlib import Path
import re

from research.datasets.downloads import (
    download_fallvision_files, extract_zip, fallvision_inventory, save_json,
)


FALLVISION_PAGE = 'https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/75QPKK'
VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mov', '.mkv', '.webm', '.m4v'}
DERIVED_WORDS = {'mask', 'masked', 'landmark', 'landmarks', 'keypoint', 'keypoints',
                 'skeleton', 'pose', 'processed', 'annotated', 'depth', 'infrared',
                 'ir', 'results', 'output', 'outputs'}


class InputVideoUnavailable(RuntimeError):
    """No actual source video could be connected; weights are not input media."""


def first_video_frame(path):
    """Decode the preview with OpenCV; PyAV is needed only for PTS evaluation."""
    import cv2
    capture = cv2.VideoCapture(str(path))
    try:
        ok, frame = capture.read()
        if not ok or frame is None:
            raise InputVideoUnavailable(f'영상의 첫 프레임을 읽을 수 없습니다: {path}')
        return frame
    finally:
        capture.release()


def _words(value):
    return set(re.findall(r'[a-z0-9]+', str(value).lower()))


def discover_videos(roots):
    """Use deterministic source-path priority and exclude derived modalities.

    Names provide a source-selection hint, not proof of RGB provenance or GT.
    The selected image is shown for review before any inference/evaluation.
    """
    videos = {}
    for root_index, root in enumerate(roots):
        root = Path(root).expanduser().resolve()
        if not root.is_dir():
            continue
        for path in root.rglob('*'):
            if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            relative = path.relative_to(root)
            words = _words(relative.as_posix())
            if words & DERIVED_WORDS:
                continue
            priority = (0 if words & {'original', 'originals', 'raw', 'rgb'} else 1,
                        0 if words & {'bed', 'bedexit', 'bedcam'} else 1,
                        root_index, relative.as_posix().lower())
            resolved = path.resolve()
            if resolved not in videos or priority < videos[resolved]:
                videos[resolved] = priority
    return sorted(videos, key=videos.get)


def _original_bed_files(inventory, max_bytes):
    candidates = []
    for entry in inventory:
        text = ' '.join(str(entry.get(key, '')) for key in
                        ('name', 'directory', 'description', 'categories'))
        words = _words(text)
        suffix = Path(entry['name']).suffix.lower()
        if (entry.get('restricted') or entry['bytes'] > max_bytes
                or suffix not in VIDEO_EXTENSIONS | {'.zip'}
                or words & DERIVED_WORDS
                or not words & {'raw', 'original', 'originals', 'rgb'}
                or not words & {'bed', 'bedexit', 'bedcam'}):
            continue
        candidates.append(entry)
    return sorted(candidates, key=lambda item: (item['bytes'], item['name'], item['id']))


def prepare_input_video(data_dir, video_path=None, extra_roots=(), download=True,
                        max_bytes=300 * 1024**2):
    """Reuse local video, otherwise obtain one identified original bed archive.

    No fabricated video/labels, no guessed file IDs and no model-only fallback.
    Downloaded media never marks ROI or event labels as reviewed.
    """
    data_dir = Path(data_dir).expanduser().resolve()
    if video_path:
        selected = Path(video_path).expanduser().resolve()
        if not selected.is_file() or selected.suffix.lower() not in VIDEO_EXTENSIONS:
            raise InputVideoUnavailable(f'지정한 입력 영상을 찾을 수 없습니다: {selected}')
        return selected
    roots = [data_dir, *extra_roots]
    configured_root = os.environ.get('BED_EXIT_DATA_DIR')
    if configured_root:
        roots.append(Path(configured_root))
    # Only dataset-specific folders in Downloads; unrelated personal videos
    # are not selected as bed-exit data.
    for folder_name in ('Downloads', '다운로드'):
        folder = Path.home() / folder_name
        if folder.is_dir():
            roots.extend(child for child in folder.iterdir() if child.is_dir()
                         and _words(child.name) & {'fallvision', 'bed', 'bedexit', 'bed_exit'})
    videos = discover_videos(roots)
    if videos:
        return videos[0]
    if not download:
        return None

    destination = data_dir / 'fallvision'
    try:
        inventory = fallvision_inventory(destination)
        candidates = _original_bed_files(inventory, max_bytes)
        if not candidates:
            raise InputVideoUnavailable(
                '목록에서 크기 제한 내 원본 RGB 침대 MP4/ZIP을 명확하게 찾지 못했습니다. '
                '파일 목록의 원본/가공본 구분을 확인해야 합니다.')
        paths = download_fallvision_files(inventory, [candidates[0]['id']],
                                         destination, max_bytes=max_bytes)
        for path in paths:
            if path.suffix.lower() == '.zip':
                extract_zip(path, destination / 'extracted' / path.stem)
        videos = discover_videos([destination])
        if not videos:
            raise InputVideoUnavailable('다운로드한 파일에서 원본 영상이 발견되지 않았습니다.')
        selected = videos[0]
        save_json(destination / 'input_video.json', {
            'video': str(selected.relative_to(data_dir)), 'file_id': candidates[0]['id'],
            'source': FALLVISION_PAGE, 'roi_reviewed': False, 'labels_reviewed': False,
        })
        return selected
    except Exception as exc:
        # Credentials and request URLs can contain tokens; do not echo the
        # complete network exception into notebook outputs.
        message = str(exc) if isinstance(exc, InputVideoUnavailable) else type(exc).__name__
        raise InputVideoUnavailable(
            f'현재 입력 RGB 영상이 없습니다. 모델 가중치 4개는 입력 영상이 아닙니다. '
            f'FallVision 자동 다운로드를 완료하지 못했습니다 ({message}). '
            f'제공처: {FALLVISION_PAGE}\n'
            f'원본 영상을 {destination / "raw"}에 다운로드/압축 해제하면 다음 실행에서 자동 연결합니다.'
        ) from exc
