# Part 5 침대 이탈 감지 모듈 (`part5_bed_exit`)

웹캠의 BGR 이미지(`numpy` 배열)를 판단 함수에 전달한다. 함수는 Python `bool`을 반환한다 (`True`: 이탈 감지 / unsafe, `False`: 정상 / safe). 신체 인식, ROI 설정, 모델 초기화 및 전처리는 연결한 메서드가 담당한다.

최근 5개 프레임 중 `True`가 3개 이상이면 내부 `state.unsafe`와 출력이 `True`가 된다. 초기 4개 프레임은 `False`이며, 정상 판단된 프레임마다 `/patient/bed_exit` (`std_msgs/msg/Bool`)을 발행한다.

## 메서드 연결

Python에서 `import` 가능한 모듈에 아래 함수를 구현한다.

```python
def judge(frame):
    # 실제 메서드가 판정한 값을 Python bool로 반환한다.
    return bool(your_method(frame))
```

상태를 유지하는 메서드도 모듈 내부 객체를 함수에서 호출하면 된다. 실제 판단 알고리즘(YOLO 추론 등)은 이 공통 노드 골격과 분리해 교체할 수 있다.

## 빌드와 실행

ROS 2 Jazzy를 `source`한 뒤 `bad_ws` 워크스페이스 루트에서 실행한다.

```bash
source /opt/ros/jazzy/setup.bash
colcon build --packages-select part5_bed_exit --symlink-install
source install/setup.bash
ros2 run part5_bed_exit bed_exit --ros-args \
  -p method:=your_package.your_method:judge \
  -p output_topic:=/patient/bed_exit \
  -p camera_index:=0 \
  -p capture_fps:=30.0
```

`capture_fps`는 프레임 수집 시도 주기다. 실제 처리율은 카메라와 메서드 실행 시간에 제한되며, 호출은 순차 처리된다.

카메라 수신 실패 또는 판단 예외·잘못된 반환값은 로그로 남기고 5프레임 이력을 초기화한다. 오류를 정상 판정(`False`)으로 바꾸어 발행하지 않으며 해당 프레임의 토픽은 발행하지 않는다. 복구 후 다시 5프레임을 수집한다.
