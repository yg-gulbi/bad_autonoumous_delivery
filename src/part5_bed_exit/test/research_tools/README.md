# 연구·POC 도구 테스트

이 디렉터리는 최종 운영 모듈의 단위·통합시험이 아니라 데이터셋 준비, 정적 이미지
POC, 오프라인 모델 비교와 결과 파일 생성 도구를 검증한다.

- `test_blanket_exit_comparison.py`: 담요 합성·앙상블 POC
- `test_compare_methods.py`: 다중 메서드 오프라인 영상 비교
- `test_compare_single_frame.py`: 단일 프레임 결과·프리뷰 저장
- `test_input_video.py`: 평가용 원본 영상 선택
- `test_static_pose_poc.py`: Roboflow·SLP Pose 시각화
- `test_training_data_preparation.py`: 외부 라벨 학습 형식 변환
- `test_video_comparison.py`: MP4 비교 결과 CSV·영상·JSON 생성
- `test_dataset_downloads.py`: 공개 데이터셋 카탈로그·미리보기 선택·다운로드 안전성
