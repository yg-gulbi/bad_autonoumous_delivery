# Part 6 장애물 분류

터틀봇 ROS 2 이미지 토픽을 구독하는 독립 패키지다. 실제 플로우차트와 모델은
아직 제공되지 않았으며 현재 구조는 아래 기본 흐름을 구현한 골격이다.
다른 파트의 코드나 주행 제어는 변경하지 않는다.

```text
이미지 수신 → 최신 프레임 저장 → BGR 변환 → 이미지 검증 → 전처리
 → 모델 검출·분류 → 결과 검증 → 장애물 유무 분기 → JSON 발행
이미지 미수신 → image_timeout
변환·모델·결과 검증 실패 → processing_error
```

## 유닛별 구현 프로젝트

각 유닛은 별도 ROS 패키지가 아니라 이 패키지 내부의 작업 단위다.

| 유닛 | 담당 함수/파일 | 구현 내용 | 완료 기준 |
| --- | --- | --- | --- |
| U1 이미지 입력 | node의 image_callback, decode_image | 실제 터틀봇 토픽 확인, Image→BGR 변환, sensor-data QoS | 실제 프레임 수신 및 색상·촬영 시각 확인 |
| U2 전처리 | classifier의 validate_frame, preprocess | BGR uint8 검증, 필요시 ROI 설계 | 빈 이미지·잘못된 형상 차단; 좌표 기준 명시 |
| U3 모델 어댑터 | 새 methods/<모델명>.py의 classify | 모델 로딩, 검출·종류 분류, 원본 픽셀 좌표 복원 | 합의한 종류별 샘플 평가 및 list[Detection] 반환 |
| U4 결과 구성 | classify_obstacles, build_result, build_error | 신뢰도·좌표 검증, 정상·오류 구분 | 장애물 없음과 판단 불가 구분, 촬영 header 보존 |
| U5 ROS 실행 | process_latest, publish_result, check_image_timeout, launch | 처리 주기, 최신 프레임 처리, 단절 감지 | 단절·복구·모델 예외 검증, 처리 지연 측정 |
| U6 통합 검증 | test/, 실기 검증 | 입력→모델→출력 및 Part 3·4 계약 검토 | 오탐·미탐·FPS·지연을 측정해 팀 기준과 비교 |

진행 순서: U1/U2 → U3 → U4/U5 → U6.
U3 착수 전에 분류 종류(예: 사람/휠체어/카트), 모델, 데이터셋,
신뢰도 기준을 정한다. RGB 영상만으로 거리·경로 점유를 확정하지 않는다.
거리 판단이 필요하면 depth/LiDAR 동기화 유닛을 추가 설계한다.

## 모델 계약

`method`는 무인자 생성 가능한 `module:ClassName`이다.
`classify(frame)`는 BGR uint8 이미지를 받아 `list[Detection]`을 반환한다.
Detection은 label, confidence(0~1), bbox(x1,y1,x2,y2)를 가진다.
bbox는 원본 이미지 내부 픽셀 좌표이며, 빈 리스트는 정상 추론 결과에서만
장애물 없음이다. 모델 내부 오류는 예외로 전달한다.
모델이 없으면 processing_error가 발행되며 장애물 없음으로 처리하지 않는다.
현재 실제 장애물 인식 모델은 포함되어 있지 않다.

## 실행

```bash
# 터틀봇 환경에서 실제 토픽·타입·QoS 확인
ros2 topic list -t
ros2 topic info <실제_이미지_토픽> --verbose

colcon build --symlink-install --packages-select part6_obstacle_classifier
source install/setup.bash
ros2 run part6_obstacle_classifier obstacle_classifier --ros-args \
  -p image_topic:=<실제_이미지_토픽> \
  -p method:=<모델_모듈>:<클래스>
ros2 topic echo /obstacle_classifier/result

PYTHONPATH=src/part6_obstacle_classifier python3 -m pytest src/part6_obstacle_classifier/test -q
```

입력은 `sensor_msgs/msg/Image`이며 compressed 입력은 아직 지원하지 않는다.
기본 image_topic=camera/image_raw는 확인 전 임시값이다.
processing_hz=5.0, image_timeout_sec=2.0은 조정 가능한 초기값이다.
단일 실행기에서 추론하므로 느린 모델은 콜백 처리도 지연시킨다.
실기 성능 측정 후 필요하면 별도 추론 작업자로 분리한다.
수신 제한시간은 로컬 수신 시각 기준이며 네트워크에 지연된 촬영 시각의
신선도 검증은 로봇/호스트 시각 동기화를 확인한 뒤 추가한다.

출력 `obstacle_classifier/result`의 `std_msgs/msg/String` JSON은
Part 6 내부 검증용 임시 계약이다. 팀 ICD 확정 후 정식 메시지로 변경한다.
정상: status=ok, obstacle_present=true/false, detections, 원본 header.
오류: status=image_timeout/processing_error, obstacle_present=null, error.
타임아웃은 단절당 한 번 발행한다. 소비자는 결과 수신 시간도 감시해야 한다.
Part 3·4와 자동 정지/재개는 연결하지 않았다.

## 추가 검증 항목

실제 ROS 이미지 구독, 타임아웃 후 복구, 잘못된 인코딩, 로봇 namespace,
모델 장시간 실행 지연은 ROS/터틀봇 환경에서 통합 검증해야 한다.
가중치·영상은 Git에 추가하지 않는다.
