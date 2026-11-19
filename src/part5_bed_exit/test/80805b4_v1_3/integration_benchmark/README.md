# 80805b4 v3 integration benchmark

v2와 같은 MP4·라벨·4개 내장 모델·ROS 토픽·CSV·영상·rosbag 구성을 사용한다.
판단 객체 생성만 `create_method()` 직접 호출에서 운영 노드와 동일한
`load_bed_exit_method(method, config)` 호출로 변경한다.

## 실행

출력 디렉터리는 매 실행 전에 존재하지 않아야 한다.

```bash
source /opt/ros/jazzy/setup.bash
unset ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT
export ROS_DOMAIN_ID=0
export PYTHONPATH="$PWD/src/part5_bed_exit:${PYTHONPATH}"
export ROS_LOG_DIR=/tmp/part5_v3_benchmark_logs

src/part5_bed_exit/.venv/bin/python -m research.integration_benchmark.benchmark \
  --video src/part5_bed_exit/research/data/videos/test_input_sequence1.mp4 \
  --config src/part5_bed_exit/research/configs/test_input_sequence1_methods_v1.json \
  --timeline src/part5_bed_exit/research/configs/test_input_sequence1_timeline_v1.json \
  --output-dir src/part5_bed_exit/results/80805b4_v1_3/integration_benchmark/individual/segmentation \
  --mode individual \
  --method segmentation

src/part5_bed_exit/.venv/bin/python -m research.integration_benchmark.benchmark \
  --video src/part5_bed_exit/research/data/videos/test_input_sequence1.mp4 \
  --config src/part5_bed_exit/research/configs/test_input_sequence1_methods_v1.json \
  --timeline src/part5_bed_exit/research/configs/test_input_sequence1_timeline_v1.json \
  --output-dir src/part5_bed_exit/results/80805b4_v1_3/integration_benchmark/all_modules \
  --mode all
```

Detection·YOLO Pose·MediaPipe Pose 개별 실행은 `--method`와 출력 디렉터리만
각 이름으로 바꾼다. 빠른 연결 확인에는 `--max-frames 30`을 추가한다.

## 검증

```bash
ROS_LOG_DIR=/tmp/part5_v3_benchmark_logs \
PYTHONPATH=src/part5_bed_exit:${PYTHONPATH} \
python3 -m pytest \
  src/part5_bed_exit/test/80805b4_v1_3/integration_benchmark -q
```

실제 산출물 검증은 모든 개별 실행과 전체 실행이 끝난 뒤
`PART5_VERIFY_V3_BENCHMARK_OUTPUTS=1`을 지정한다.
