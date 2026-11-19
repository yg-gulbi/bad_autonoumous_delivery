# 80805b4 v3 통합 테스트

## 목적

런타임 로더 복구 후 다음 두 경로를 검증한다.

1. `module:ClassName` 외부 자식 클래스 import → 객체 생성 → `BedExitState` 5프레임 투표
2. `segmentation` 내장 이름 → `load_bed_exit_method()` → `create_method()` → 실제 MP4 추론

## 빠른 통합 테스트

```bash
source /opt/ros/jazzy/setup.bash
ROS_LOG_DIR=/tmp/part5_v3_integration_logs \
PYTHONPATH=src/part5_bed_exit:${PYTHONPATH} \
python3 -m pytest \
  src/part5_bed_exit/test/80805b4_v1_3/integration/test_v3_dynamic_pipeline.py -q
```

## 실제 MP4 통합 테스트

1,024프레임과 `yolo11n-seg.pt`를 사용하므로 명시적으로 활성화한다.

```bash
source /opt/ros/jazzy/setup.bash
PART5_RUN_V3_MP4_INTEGRATION=1 \
ROS_LOG_DIR=/tmp/part5_v3_integration_logs \
PYTHONPATH=src/part5_bed_exit:${PYTHONPATH} \
src/part5_bed_exit/.venv/bin/python -m pytest \
  src/part5_bed_exit/test/80805b4_v1_3/integration/test_v3_mp4_runtime_loader.py \
  -q -s
```

통과 조건은 1,024프레임 완주, 유효 판정 존재, `True`·`False` 모두 존재와
판정 불가 프레임 미발행이다.
