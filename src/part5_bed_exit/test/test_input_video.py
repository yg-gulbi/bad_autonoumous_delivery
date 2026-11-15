"""Downloaded media must feed VIDEO_PATH rather than remain disconnected."""

from pathlib import Path

import cv2
import numpy as np
import pytest

from part5_bed_exit import input_video as media


def test_auto_connects_downloaded_original_and_ignores_processed_media(tmp_path, monkeypatch):
    for relative in ['fallvision/processed/first.mp4', 'fallvision/raw/bed_rgb.MP4']:
        path=tmp_path/relative
        path.parent.mkdir(parents=True,exist_ok=True)
        path.touch()
    monkeypatch.setattr(media,'fallvision_inventory',lambda output: pytest.fail('Existing video must avoid network'))
    assert media.prepare_input_video(tmp_path)==tmp_path/'fallvision/raw/bed_rgb.MP4'


def test_missing_weights_only_directory_does_not_become_fake_video(tmp_path, monkeypatch):
    (tmp_path/'yolo11n.pt').touch()
    monkeypatch.setenv('BED_EXIT_DATA_DIR',str(tmp_path/'missing'))
    assert media.prepare_input_video(tmp_path, download=False) is None


def test_download_result_is_selected_automatically_and_reused(tmp_path, monkeypatch):
    inventories=[
        {'id':1,'name':'bed_raw.zip','bytes':100,'restricted':False},
        {'id':2,'name':'bed_raw_masked.mp4','bytes':1,'restricted':False},
        {'id':3,'name':'bed_original.mp4','bytes':1,'restricted':True},
    ]
    calls=[]
    monkeypatch.setattr(media,'fallvision_inventory',lambda output: inventories)
    def download(inventory, ids, output, max_bytes):
        calls.append(ids)
        path=Path(output)/'raw'/'bed_raw.zip'
        path.parent.mkdir(parents=True,exist_ok=True)
        path.touch()
        return [path]
    def extract(archive,output):
        path=Path(output)/'bed001.mp4'
        path.parent.mkdir(parents=True,exist_ok=True)
        path.touch()
        return [path]
    monkeypatch.setattr(media,'download_fallvision_files',download)
    monkeypatch.setattr(media,'extract_zip',extract)
    selected=media.prepare_input_video(tmp_path)
    assert selected.is_file() and selected.name=='bed001.mp4'
    assert (tmp_path/'fallvision/input_video.json').is_file()
    assert media.prepare_input_video(tmp_path)==selected
    assert calls==[[1]]


def test_failed_provider_is_reported_as_missing_data_not_success(tmp_path,monkeypatch):
    def unavailable(output):
        raise RuntimeError('provider unavailable')
    monkeypatch.setattr(media,'fallvision_inventory',unavailable)
    with pytest.raises(media.InputVideoUnavailable,match='모델 가중치 4개는 입력 영상이 아닙니다'):
        media.prepare_input_video(tmp_path)
    assert not (tmp_path/'fallvision/input_video.json').exists()


def test_explicit_path_is_honored_and_invalid_path_fails(tmp_path):
    video=tmp_path/'user.mov'
    video.touch()
    assert media.prepare_input_video(tmp_path,video_path=video)==video
    with pytest.raises(media.InputVideoUnavailable,match='지정한 입력 영상'):
        media.prepare_input_video(tmp_path,video_path=tmp_path/'missing.mp4')


def test_opencv_preview_reads_an_actual_downloaded_video_without_pyav(tmp_path):
    path=tmp_path/'bed_rgb.avi'
    writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'MJPG'),10,(32,24))
    assert writer.isOpened()
    writer.write(np.full((24,32,3),120,np.uint8))
    writer.release()
    frame=media.first_video_frame(path)
    assert frame.shape==(24,32,3) and frame.dtype==np.uint8
