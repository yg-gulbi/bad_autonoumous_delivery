"""연구 도구: 평가에 사용할 원본 영상의 탐색·선택 규칙을 검증한다.

``research/evaluation/input_video.py``가 모델 가중치·가공 영상을 원본 MP4로
오인하지 않도록 만들었을 때 추가한 테스트다. 기존 파일 재사용, 명시 경로,
다운로드 실패와 OpenCV 디코딩을 확인하며 최종 ROS 통합시험에는 포함하지 않는다.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from research.evaluation import input_video as media


def test_auto_connects_downloaded_original_and_ignores_processed_media(tmp_path, monkeypatch):
    """자동 탐색이 가공 영상을 제외하고 기존 원본 영상을 선택하는지 검증한다."""
    for relative in ['fallvision/processed/first.mp4', 'fallvision/raw/bed_rgb.MP4']:
        path=tmp_path/relative
        path.parent.mkdir(parents=True,exist_ok=True)
        path.touch()
    monkeypatch.setattr(media,'fallvision_inventory',lambda output: pytest.fail('Existing video must avoid network'))
    assert media.prepare_input_video(tmp_path)==tmp_path/'fallvision/raw/bed_rgb.MP4'


def test_missing_weights_only_directory_does_not_become_fake_video(tmp_path, monkeypatch):
    """가중치 파일만 있는 디렉터리를 영상 입력으로 오인하지 않는지 검증한다."""
    (tmp_path/'yolo11n.pt').touch()
    monkeypatch.setenv('BED_EXIT_DATA_DIR',str(tmp_path/'missing'))
    assert media.prepare_input_video(tmp_path, download=False) is None


def test_download_result_is_selected_automatically_and_reused(tmp_path, monkeypatch):
    """다운로드된 영상을 즉시 선택하고 다음 호출에서 재사용하는지 검증한다."""
    inventories=[
        {'id':1,'name':'bed_raw.zip','bytes':100,'restricted':False},
        {'id':2,'name':'bed_raw_masked.mp4','bytes':1,'restricted':False},
        {'id':3,'name':'bed_original.mp4','bytes':1,'restricted':True},
    ]
    calls=[]
    monkeypatch.setattr(media,'fallvision_inventory',lambda output: inventories)
    def download(inventory, ids, output, max_bytes):
        """선택 파일 하나를 만든 뒤 경로를 반환하는 가짜 다운로드 함수."""
        calls.append(ids)
        path=Path(output)/'raw'/'bed_raw.zip'
        path.parent.mkdir(parents=True,exist_ok=True)
        path.touch()
        return [path]
    def extract(archive,output):
        """압축 해제 결과를 모사해 원본 MP4 경로를 만드는 가짜 함수."""
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
    """공급자 실패가 성공 경로가 아니라 입력 없음으로 보고되는지 검증한다."""
    def unavailable(output):
        """접근 불가 공급자를 모사해 예외를 발생시킨다."""
        raise RuntimeError('provider unavailable')
    monkeypatch.setattr(media,'fallvision_inventory',unavailable)
    with pytest.raises(media.InputVideoUnavailable,match='모델 가중치 4개는 입력 영상이 아닙니다'):
        media.prepare_input_video(tmp_path)
    assert not (tmp_path/'fallvision/input_video.json').exists()


def test_explicit_path_is_honored_and_invalid_path_fails(tmp_path):
    """명시한 영상 경로를 우선하고 없는 경로는 실패하는지 검증한다."""
    video=tmp_path/'user.mov'
    video.touch()
    assert media.prepare_input_video(tmp_path,video_path=video)==video
    with pytest.raises(media.InputVideoUnavailable,match='지정한 입력 영상'):
        media.prepare_input_video(tmp_path,video_path=tmp_path/'missing.mp4')


def test_opencv_preview_reads_an_actual_downloaded_video_without_pyav(tmp_path):
    """PyAV 없이 OpenCV만으로 다운로드 영상 첫 프레임을 읽는지 검증한다."""
    path=tmp_path/'bed_rgb.avi'
    writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'MJPG'),10,(32,24))
    assert writer.isOpened()
    writer.write(np.full((24,32,3),120,np.uint8))
    writer.release()
    frame=media.first_video_frame(path)
    assert frame.shape==(24,32,3) and frame.dtype==np.uint8
