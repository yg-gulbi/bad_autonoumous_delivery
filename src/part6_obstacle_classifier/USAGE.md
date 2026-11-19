# Part 6 장애물 분류 (`part6_obstacle_classifier`) 사용 가이드

기능 순서에 따라 분리된 4개의 ROS 2 노드와 2개의 테스트/디버깅 도구를 제공합니다.  
자세한 내부 설계 및 플로우차트는 [DESIGN.md](DESIGN.md), 검증 기록은 [TEST_REPORT.md](TEST_REPORT.md)를 참고하세요.

---

## 1. 빌드 및 환경 설정

```bash
cd /home/mu-02/rokey_ws/bad_autonoumous_delivery
colcon build --packages-select part6_obstacle_classifier
source install/setup.bash

# ros2 run에서 rokey_venv의 ultralytics 패키지를 인식하도록 PYTHONPATH 포함
export PYTHONPATH=/home/mu-02/venvs/rokey_venv/lib/python3.12/site-packages:$PYTHONPATH
```

*(참고: 관제 PC Discovery Server 없이 로컬 PC에서만 단독 테스트할 때는 `unset ROS_DISCOVERY_SERVER`를 실행해 주세요.)*

---

## 2. 노드별 실행 방법 (순서대로)

### 1번: 이미지 이벤트 수신 → 마스킹 + 객체 종류 발행 (`obstacle_classifier`)
타 파트에서 `hospital_nav/obstacle_image` (`sensor_msgs/msg/CompressedImage`)로 이미지 1장을 전송하면, 학습 모델(`medical_box_yolo11n.pt`)로 추론해 `pose` 없는 JSON과 마스킹 이미지를 발행합니다.
```bash
ros2 run part6_obstacle_classifier obstacle_classifier --ros-args -r __ns:=/robot2
```

### 2번: RGB + Depth 이벤트 수신 → 마스킹 + 객체 위치·종류 발행 (`rgbd_event_classifier`)
RGB·Depth·`CameraInfo`·TF를 이용해 `map` 좌표계 `pose`까지 계산해 발행합니다.
```bash
# RGB+Depth 토픽 도착 즉시 이벤트 처리
ros2 run part6_obstacle_classifier rgbd_event_classifier --ros-args -r __ns:=/robot2

# Empty 트리거(obstacle/inspect_image) 수신 시에만 1회 캡처·처리하려면:
ros2 run part6_obstacle_classifier rgbd_event_classifier --ros-args -r __ns:=/robot2 -p use_trigger:=true
```

### 3번: 로컬 이미지 파일/폴더 → 마스킹 + 객체 종류 토픽 발행 (`local_image_classifier`)
로컬 이미지들을 `(선택적 크롭 + 512x288 다운샘플링)` 후 ROS 2 토픽으로 송출합니다.
```bash
ros2 run part6_obstacle_classifier local_image_classifier --ros-args -r __ns:=/robot2 \
  -p input_path:=/home/mu-02/rokey_ws/part6_assets/medical_box_dataset/images/val \
  -p publish_hz:=2.0
```

### 4번: bag 실시간 재생 → 실시간 객체 위치 전달 (`bag_realtime_obstacle`)
`ros2 bag play`에서 나오는 실시간 RGB + Depth + `CameraInfo` + TF를 받아 연속으로 위치·종류·마스킹을 발행합니다.
```bash
# 터미널 1: 노드 실행
ros2 run part6_obstacle_classifier bag_realtime_obstacle --ros-args -r __ns:=/robot2

# 터미널 2: bag 재생
ros2 bag play /경로/데이터.bag
```

---

## 3. 출력 확인 및 테스트 도구

```bash
# JSON 결과 토픽 확인
ros2 topic echo /robot2/obstacle_classifier/result

# 마스킹된 이미지 확인
ros2 run rqt_image_view rqt_image_view

# TurtleBot4 실시간 RGB(마스크) + Depth 컬러맵 GUI 뷰어
ros2 run part6_obstacle_classifier test_turtlebot_viewer

# 로컬 이미지 512x288 다운샘플링·크롭 CLI 테스트
ros2 run part6_obstacle_classifier test_image_downsample /이미지/폴더 --save-dir ./out
```

---

## 4. 단위 테스트

```bash
PYTHONPATH=/home/mu-02/rokey_ws/bad_autonoumous_delivery/src/part6_obstacle_classifier \
  pytest /home/mu-02/rokey_ws/bad_autonoumous_delivery/src/part6_obstacle_classifier/test/test_pipeline.py
```
