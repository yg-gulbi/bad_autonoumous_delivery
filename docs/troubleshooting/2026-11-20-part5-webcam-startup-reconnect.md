# Part 5 노드 시작 시 웹캠 미연결 종료 및 USB 재연결 보강

- 프로젝트: `bad_autonoumous_delivery` (`part5_bed_exit`)
- 발생일: 2026-11-20
- 기록일: 2026-11-20
- 상태: 단위 테스트 통과 / 실구동 웹캠 분리·재연결 검증 대기
- 관련 파일·Issue·PR·커밋:
  - `src/part5_bed_exit/part5_bed_exit/bed_exit_node.py`
  - `src/part5_bed_exit/test/80805b4_v1_3/unit/test_bed_exit_node.py`
  - `src/part5_bed_exit/results/80805b4_v1_3/20261120_114826_unit_camera_reconnect/unit_tests/README.md`
  - `src/part5_bed_exit/README.md`
  - `.gitignore`, `src/part5_bed_exit/.gitignore`, `src/part6_obstacle_classifier/.gitignore`

## 1. 문제정의

### 기술적으로 관찰한 사실

- `ros2 run part5_bed_exit bed_exit --ros-args -p method_config:="..." -p camera_index:=2 ...` 실행 시, 카메라가 연결되지 않은 상태에서는 노드가 대기하지 않고 즉시 `RuntimeError: Cannot open video source (camera_index=2)`로 종료됨.
- 실행 도중 USB 웹캠 케이블이 뽑혔을 때는 `v1.3.2`(`b08be95`) 콜백 예외 격리로 노드가 죽지 않고 미발행·이력 초기화가 동작하지만, 기존 `cv2.VideoCapture` 핸들을 `release()`하고 다시 여는 재연결 로직이 없어 선을 다시 꽂아도 자동 복구되지 않음.

### 원인

1. `BedExitNode.__init__()`가 웹캠 모드와 MP4 파일 모드를 구분하지 않고, 최초 `cv2.VideoCapture(source).isOpened() == False`일 때 무조건 `RuntimeError`를 발생시켜 노드를 종료함.
2. `process_frame()`에서 웹캠 `read()` 실패 시 `self.capture` 핸들을 닫지 않고 그대로 유지해, 리눅스 V4L2 환경에서 USB 분리 후 재연결 시 핸들이 복구되지 않음.

## 2. 해결방법 안들 설정

| 해결안 | 선택 또는 보류 이유 | 결과 |
| --- | --- | --- |
| 웹캠 모드 최초 미연결 허용 + `reconnect_interval` 쿨다운 재연결 + 읽기 실패 시 즉시 `release()` | 기존 순서도(`PART 05 system design v6`)의 `5.0`/`5.1` 구조를 유지하면서 시작 미연결·실행 중 분리·재연결을 하나의 예외 경로로 통합 가능. MP4 경로 오타 실패 계약과 기존 단위 테스트 호환 유지. | 선택 및 구현 완료 (단위 테스트 51/51 통과) |
| 시작 시 무한 루프로 카메라 연결 대기 (`while not isOpened()` 블로킹) | ROS 노드 초기화와 `rclpy.spin()` 진입을 막아 `Ctrl+C` 종료·파라미터 응답이 지연되므로 보류. | 보류 |
| 매 30 Hz 타이머 틱마다 즉시 `VideoCapture` 재오픈 | 미연결 상태에서 CPU 점유 및 V4L2 오류 로그가 과도하게 발생하므로 쿨다운(`reconnect_interval=1.0s`) 방식을 채택. | 보류 |

## 3. 해결방법 실행

- **코드 수정 (AI 실행)**:
  - `bed_exit_node.py`:
    - `reconnect_interval` ROS 파라미터(기본 `1.0`초, `> 0` 검증) 추가.
    - `__init__()`: `is_video_file=False`(웹캠 모드)일 때 `_open_camera(initial=True)` 실패 시 예외를 던지지 않고 `self.capture = None` 상태로 30 Hz 타이머를 정상 생성 (`is_video_file=True`인 MP4 모드는 기존대로 `RuntimeError` 유지).
    - `_close_capture_handle()`, `_disconnect_camera()`, `_open_camera()`, `_ensure_camera()` 추가.
    - `process_frame()`: 웹캠 모드에서 `read()` 예외 또는 `(False, None)` 발생 시 즉시 `_disconnect_camera()`로 핸들을 해제(`release()`)하고 `history` 초기화·`previous` 유지·미발행 처리. 이후 `reconnect_interval` 경과 시 자동 재연결 시도.
  - `src/part5_bed_exit/README.md`: `reconnect_interval` 파라미터와 실패 정책·시작 오류 표를 최신 동작에 맞게 갱신.
  - `.gitignore` 정리:
    - 루트 `.gitignore`: `rosbag2_*/`, `*.mcap`, `*.db3` 추가.
    - `src/part5_bed_exit/.gitignore`: `/run_data/`, `/config/*.json` + `!/config/*.template.json` 추가.
    - `src/part6_obstacle_classifier/.gitignore`: `/models/` 추가.
- **단위 테스트 추가 (AI 실행)**:
  - `src/part5_bed_exit/test/80805b4_v1_3/unit/test_bed_exit_node.py`에 신규 회귀 단위 테스트 5건 추가:
    - `test_init_stays_alive_when_webcam_unavailable`
    - `test_init_still_raises_when_video_file_missing`
    - `test_init_rejects_invalid_reconnect_interval`
    - `test_process_frame_reconnects_after_cooldown`
    - `test_process_frame_releases_and_reconnects_on_usb_unplug_replug`

### 검증

- 실행한 확인:
  - 단위 테스트: `PART5_PYTHON=/usr/bin/python3 bash src/part5_bed_exit/test/80805b4_v1_3/run_validation.sh unit` (실험명: `camera_reconnect`)
  - Part 3–Part 5 통합 테스트 (수정된 `bed_exit_node.py`가 `--symlink-install`로 반영된 상태): `bash part_integration/part3_part5/run.sh --video-path src/part5_bed_exit/run_data/test_bed.mp4` (실험명: `run_complete`)
- 실제 결과:
  - 단위 테스트: `tests=51, passed=51, failures=0, errors=0, skipped=0, junit_time_s=0.73` (`[RUN END] PASS`)
    - 산출물 위치: `src/part5_bed_exit/results/80805b4_v1_3/20261120_114826_unit_camera_reconnect/` (`unit_tests/README.md`, `unit_tests/pytest.xml`, `summary.json`, `execution.log`)
  - `part3_part5` 통합 테스트: `7 / 7 PASS` (`15.035초`, MCAP `104`건, `part5_mp4` 정상 구동 및 `bed_exit=false -> true -> PAUSE -> SAFE_PAUSE(BED_EXIT)` 검증 완료, 상세 이슈 기록: `docs/troubleshooting/2026-11-20-part3-part5-robot-namespace-mismatch.md`)
- 남은 한계:
  - 실제 USB 웹캠을 물리적으로 분리·재연결하는 현장 실구동 시나리오는 추후 검증 예정.

## 4. 경험의 재해석

### 내가 실제로 경험하고 느낀 것

- 어제 콜백 예외 처리를 추가했으나, 실제 실행 시 카메라가 연결되지 않으면 시작 단계에서 노드가 즉시 종료되는 문제가 확인되어 시작 시 미연결 생존 및 USB 재연결 로직 보강을 요청함.

### AI 해석·제안

- 기존 `PART 05 system design v6` 순서도에서 `5.0`의 끝단에 ROS 타이머 생성을 두고, `5.1`의 `웹캠 검출미흡 예외처리` 박스에 `핸들 해제(release) 후 쿨다운 재연결` 1줄만 추가하면 전체 흐름을 유지한 채 시작 미연결·실행 중 분리·재연결을 통합할 수 있음.
- 추후 실구동 테스트 시 USB 분리 후 다시 꽂았을 때 리눅스 커널이 장치 번호(`/dev/video2`)를 그대로 할당하는지 확인이 필요함 (번호가 바뀌는 환경이라면 udev 고정 심볼릭 링크 또는 인덱스 재탐색 검토 가능).

## 5. 정리

- 현재 결론: 웹캠 미연결 대기 및 쿨다운 자동 재연결 구현과 단위 테스트 51건 전원 통과 완료.
- 다시 발생하면: `execution.log`에서 `Webcam unavailable` / `Webcam reconnected` 로그와 `/dev/video*` 장치 번호 변화를 함께 확인.
- 다음에 반영할 것: 추후 실제 구동 시나리오에서 웹캠 분리·재연결 동작 확인 및 필요 시 ROS 2 루프백 통합 테스트(`test_v3_camera_reconnect_pipeline.py`) 추가.
