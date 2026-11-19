# Part 5 v1.3 검증

`run_validation.sh`은 Bash 진입점이며 실제 테스트는 Python `pytest`와
`research.integration_benchmark.benchmark`가 수행한다. v1.3.1·v1.3.2처럼
공개 계약을 바꾸지 않는 patch도 이 테스트 트리를 계속 사용한다.

단위·통합·벤치마크를 한번에 실행하는 명령은 제공하지 않는다.
반드시 하나를 명시해야 한다.

```bash
cd ~/workspaces/bad_autonoumous_delivery

# Part 5 단위 테스트
bash src/part5_bed_exit/test/80805b4_v1_3/run_validation.sh unit

# Part 5 동적 로더·ROS 경계·실제 MP4 통합 테스트
bash src/part5_bed_exit/test/80805b4_v1_3/run_validation.sh integration

# Part 5 4개 모델 개별·전체 벤치마크
bash src/part5_bed_exit/test/80805b4_v1_3/run_validation.sh benchmark
```

기본 Python은 `src/part5_bed_exit/.venv/bin/python`이다. 다른 Python은
`PART5_PYTHON=/absolute/path/to/python` 환경 변수로 지정한다.

결과는 실행별로 다음 경로에 생성된다.

```text
src/part5_bed_exit/results/80805b4_v1_3/runs/YYYYMMDD_HHMMSS/
├── unit_tests/pytest.xml
├── integration_tests/pytest.xml
├── integration_benchmark/
│   ├── individual/{detection,segmentation,yolo_pose,mediapipe_pose}/
│   ├── all_modules/
│   └── pytest.xml
└── ros_logs/
```

`unit_tests`, `integration_tests`, `integration_benchmark`는 실행 코드가 아니라
JUnit·CSV·MP4·rosbag 결과를 저장하는 산출물 폴더다.
