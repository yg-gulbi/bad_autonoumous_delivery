# 80805b4 v3 통합 테스트

## 목적

런타임 로더 복구 후 다음 세 경로를 검증한다.

1. `module:ClassName` 외부 자식 클래스 import → 객체 생성 → `BedExitState` 5프레임 투표
2. `segmentation` 내장 이름 → `load_bed_exit_method()` → `create_method()` → 실제 MP4 추론
3. 연구용 MP4 ROS 노드의 카메라·발행·프리뷰 예외 격리

### `test_v3_mp4_replay_resilience.py`

실제 모델 정확도를 측정하는 테스트가 아니라 연구용 `Mp4RosReplayNode`의 생존성을
검증하는 회귀 테스트다. 가짜 MP4 캡처와 ROS publisher를 사용해 다음 실패를 강제로
발생시킨다.

- `capture.read()`가 예외를 던져도 콜백 밖으로 전파하지 않고 투표 이력을 초기화한다.
- ROS 발행이 실패해도 해당 판단을 발행 완료로 취급하지 않고 다시 워밍업한다.
- 프리뷰가 실패해도 판단·발행 결과는 유지하고 화면 출력만 비활성화한다.

이 테스트는 모델 가중치, ROS Discovery와 실제 MP4가 필요하지 않으므로 일반 통합 테스트에서
항상 실행한다.

## 빠른 통합 테스트

```bash
source /opt/ros/jazzy/setup.bash
ROS_LOG_DIR=/tmp/part5_v3_integration_logs \
PYTHONPATH=src/part5_bed_exit:${PYTHONPATH} \
python3 -m pytest \
  src/part5_bed_exit/test/80805b4_v1_3/integration/test_v3_dynamic_pipeline.py \
  src/part5_bed_exit/test/80805b4_v1_3/integration/test_v3_mp4_replay_resilience.py -q
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
