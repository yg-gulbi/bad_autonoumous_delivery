# 모델 시험 결과

이 폴더는 ROS 노드 통합 전후와 관계없이 모델 자체를 이미지·데이터셋에 실행한 결과를
한곳에 모은다. MP4·웹캠·토픽·rosbag 결과는 `../integration_tests/`에 둔다.

- `dataset_benchmarks/`: 이름 있는 데이터셋 단위 정량·정성 결과
- `exploratory_prototypes/`: 소수 정적 이미지로 방법을 검토한 초기 POC
- `archived_runs/`: 새 실험으로 대체됐지만 근거 보존을 위해 남긴 과거 실행

각 실험 폴더의 CSV·JSON은 수치 결과이고, `panels/`, contact sheet, JPG·PNG는 사람이
모델 출력을 확인하는 시각적 결과다.
