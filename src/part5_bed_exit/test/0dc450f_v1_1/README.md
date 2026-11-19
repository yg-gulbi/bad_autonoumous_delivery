# Part 5 테스트 구조

- `unit/`: 외부 카메라·ROS 통신·실제 모델 없이 코드 한 단위를 검증
- `integration/`: 코드 변경 없이 이동 가능한 MP4·ROS 통합 경계 테스트
- `legacy_mixed/test_part5_integration.py`: 이름은 통합 테스트지만 실제로는 다수결,
  사람 미검출, 팩토리, ROS 경계, Notebook 문법, ZIP 안전성, 벤치마크 오류까지 섞은
  **구형 교차 계층 테스트**. 새 단위 테스트의 기준 파일로 사용하지 않는다.
- `research_tools/`: 데이터셋·POC·비교 도구 자체를 검증하는 테스트
- `research_tools/test_dataset_downloads.py`: 공개 데이터셋 카탈로그·선별·다운로드
  안전성만 검증한다. 침대 이탈 모델의 정확도 시험이 아니다.
- `legacy_mixed/test_pretrained_bed_exit.py`: 이름과 달리 실제 가중치를 검증하는 파일이
  아니다. ROI 기하, 환자 선택, 판정 정책, 평가 지표, 외부 모델 어댑터 계약이 섞인
  **구형 코어·평가 혼합 테스트**다.

`legacy_mixed/`의 두 혼합 테스트는 추후 각 테스트를 `unit/`, `integration/`, `research_tools/`로
분리하고 중복 검증을 제거할 대상이다.
