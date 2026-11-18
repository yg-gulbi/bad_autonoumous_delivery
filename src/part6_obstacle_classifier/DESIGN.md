# `part6_obstacle_classifier` 설계도

## 1. 처리 플로우차트

```mermaid
flowchart TD
    IN_EVT["입력: /robot{2,3}/obstacle/inspect_image<br/>(std_msgs/Empty)"] --> ARM["이미지 1회 수신 활성화<br/>(평소에는 이미지 처리 안 함)"]
    ARM --> WAIT["카메라 1쌍 수신 대기<br/>(oakd/rgb/image_raw/compressed, oakd/stereo/image_raw, camera_info)"]
    WAIT --> CHK{"데이터 수신 완료?<br/>(RGB, Depth, CameraInfo)"}

    CHK -- 실패/타임아웃 --> ERR["출력: obstacle_classifier/result<br/>(오류 JSON, obstacle_present: null)"]
    CHK -- 완료 --> DET["RGB 이미지 검증 및 YOLO11n 객체 탐지"]

    DET --> HAS{"탐지된 객체가 있는가?"}
    HAS -- 아니오 --> EMPTY["출력: obstacle_classifier/result<br/>(정상 JSON, obstacle_present: false)"]
    HAS -- 예 --> DEPTH["각 탐지 객체 중심점의 깊이(Depth) 확인"]

    DEPTH --> VALID{"깊이 값이 유효한가?<br/>(정상 범위 내 측정값)"}
    VALID -- 아니오 --> INFO_ONLY["객체 정보만 기록<br/>(label, confidence, bbox)"]
    VALID -- 예 --> MAP["3D 위치 계산 및 지도(map) 좌표 변환"]
    IN_TF["상시 입력: /tf, /tf_static<br/>(tf2_msgs/TFMessage)"] -. 좌표 변환 정보 제공 .-> MAP

    MAP --> WITH_POSE["객체 정보 + 지도 위치(pose) 기록"]
    INFO_ONLY --> LIST["결과 목록에 추가"]
    WITH_POSE --> LIST
    LIST --> OK["출력: obstacle_classifier/result<br/>(정상 JSON, obstacle_present: true, detections)"]
```

## 2. 입출력 토픽 (네임스페이스 `/robot2`, `/robot3` 적용)

| 방향 | 파라미터 | 기본 토픽명 | 메시지 타입 |
|---|---|---|---|
| 입력 (이벤트) | `trigger_topic` | `obstacle/inspect_image` | `std_msgs/msg/Empty` |
| 입력 (RGB) | `rgb_topic` | `oakd/rgb/image_raw/compressed` | `sensor_msgs/msg/CompressedImage` |
| 입력 (Depth) | `depth_topic` | `oakd/stereo/image_raw` | `sensor_msgs/msg/Image` |
| 입력 (카메라 정보) | `camera_info_topic` | `oakd/rgb/camera_info` | `sensor_msgs/msg/CameraInfo` |
| 입력 (좌표 변환) | *(TF2 자동 구독)* | `/tf`, `/tf_static` | `tf2_msgs/msg/TFMessage` |
| 출력 (결과) | `output_topic` | `obstacle_classifier/result` | `std_msgs/msg/String` (JSON) |
