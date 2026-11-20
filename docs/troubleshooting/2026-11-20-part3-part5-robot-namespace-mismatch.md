# part3_part5 통합 테스트 robot2/robot3 네임스페이스 불일치로 TEST 2/7 타임아웃

- 프로젝트: `bad_autonoumous_delivery` (`part_integration/part3_part5`)
- 발생일: 2026-11-20
- 기록일: 2026-11-20
- 상태: 해결 (`7 / 7 PASS` 검증 완료)
- 관련 파일·Issue·PR·커밋:
  - `part_integration/part3_part5/run_part3_part5_ros_experiment.py`
  - `part_integration/part3_part5/run.sh`
  - `src/part3_fleet_manager/config/fleet.yaml`
  - 실패 실행 산출물(로컬): `part_integration/part3_part5/results/80805b4_v1_3/20261120_114957_part3_part5/`
  - 해결 검증 산출물(로컬): `part_integration/part3_part5/results/80805b4_v1_3/20261120_115533_part3_part5_run_complete/`

## 1. 문제정의

### 기술적으로 관찰한 사실

- `bash run.sh --video-path /home/mu-02/rokey_ws/bad_autonoumous_delivery/src/part5_bed_exit/run_data/test_bed.mp4` 실행(`20261120_114957_part3_part5`)에서:
  - `TEST 1/7 mission_ack_ok`는 `0.103초` 만에 `PASS` (`M-0001 접수했습니다`).
  - `TEST 2/7 move_observed`가 `/robot2/hospital_nav/command`에서 `MOVE` 명령을 30.0초 동안 수신하지 못해 `TimeoutError`로 `FAIL` (`move_commands=[]`).
  - `rosbag/metadata.yaml`에서도 `/robot2/hospital_nav/command` 메시지 수가 `0건`이었음.
  - 반면 같은 실행의 `summary.json` `state_transitions`를 보면 `robot_a`는 이미 `HOLD(IDLE) → MOVING(UNDOCKING) → MOVING(LOCALIZING) → MOVING(MOVING)` 상태로 정상 출발 완료된 상태였음.

### 원인

- 2026-11-19 확정(D-29)에 따라 `src/part3_fleet_manager/config/fleet.yaml`에서 두 로봇의 네임스페이스가 변경됨:
  - `robot_a` (침상형 이송 로봇, `PATIENT_TRANSPORT` 담당): `ns: robot3`
  - `robot_b` (약품 운송 로봇, `MED_ROUND` 담당): `ns: robot2`
- 통합 테스트 스크립트(`run_part3_part5_ros_experiment.py`)가 이전 매핑인 `/robot2/hospital_nav/command`를 하드코딩해 구독·녹화·검증하고 있었기 때문에, 실제 `PATIENT_TRANSPORT` 명령이 발행된 `/robot3/hospital_nav/command`를 관측하지 못함.

## 2. 해결방법 안들 설정

| 해결안 | 선택 또는 보류 이유 | 결과 |
| --- | --- | --- |
| `run_part3_part5_ros_experiment.py`의 명령 토픽을 `ROBOT_A_NS = 'robot3'`, `ROBOT_A_COMMAND_TOPIC = f'/{ROBOT_A_NS}/hospital_nav/command'`로 상수화해 일괄 반영 | 현재 확정된 `fleet.yaml` 매핑(D-29)과 즉시 일치시키고, 하드코딩 분산 위치를 한 곳으로 정리. | 선택 및 적용 완료 (`7 / 7 PASS`) |
| `fleet.yaml`의 `robot_a.ns`를 다시 `robot2`로 되돌림 | 실제 현장 로봇 매핑 확정 사항(D-29)을 거스르므로 보류. | 보류 |
| 향후 `fleet.yaml`에서 `robot_a.ns`를 동적으로 파싱 | 네임스페이스 재변경 시 자동 대응 가능하나 이번 긴급 수정은 최소 변경을 우선해 후속 개선 후보로 남김. | 보류 (후속 개선 후보) |

## 3. 해결방법 실행

- **원인 분석 (AI 실행)**: `20261120_114957_part3_part5/summary.json`, `rosbag/metadata.yaml`, `fleet.yaml`을 대조해 `robot_a`가 `robot3`로 정상 이동 중이나 스크립트가 `/robot2/hospital_nav/command`만 감시함을 확인.
- **코드 수정 및 재실행 (사용자 실행)**:
  - `part_integration/part3_part5/run_part3_part5_ros_experiment.py`에 `ROBOT_A_NS = 'robot3'`, `ROBOT_A_COMMAND_TOPIC = f'/{ROBOT_A_NS}/hospital_nav/command'`를 도입하고 구독, `BAG_TOPICS`, `rosbag` 준비 확인, `TEST 2(MOVE)`·`TEST 5(PAUSE)`·`TEST 7(rosbag_evidence)` 검증 토픽을 일괄 교체.
  - `part_integration/part3_part5/run.sh`의 기본 Python 인터프리터 경로를 현재 환경(`/home/mu-02/venvs/rokey_venv/bin/python3`)에 맞게 갱신.
  - 수정된 `part5_bed_exit/bed_exit_node.py`(카메라 미연결 대기·재연결 보강)가 `--symlink-install`로 반영된 상태에서 `part3_part5` 통합 테스트 재실행 (`20261120_115533_part3_part5_run_complete`).

### 검증

- 실행한 확인:
  - `bash part_integration/part3_part5/run.sh --video-path /home/mu-02/rokey_ws/bad_autonoumous_delivery/src/part5_bed_exit/run_data/test_bed.mp4` (실험명: `run_complete`)
- 실제 결과 (`20261120_115533_part3_part5_run_complete/summary.json` 및 `bag_evidence.json`):
  - **전체 상태**: **`PASS` (`7 / 7`, 총 `15.035초`, `part3_launch=0`, `rosbag_recorder=0`, `part5_mp4=0`)**
  - 단계별 통과 내역:
    1. `mission_ack_ok`: PASS (`0.103s`, `M-0001` 접수)
    2. `move_observed`: PASS (`1.102s`, `/robot3/hospital_nav/command` → `"MOVE C 2 id=L-0001-1"`)
    3. `part4_moving`: PASS (`4.196s`, `robot_a(ns=robot3).part4_state=MOVING`)
    4. `bed_exit_true_observed`: PASS (`5.479s`, `33`샘플: `false=32`, `true=1`, `false → true` 순서)
    5. `pause_observed`: PASS (`0.021s`, `/robot3/hospital_nav/command` → `"PAUSE"`)
    6. `safe_pause_with_bed_exit`: PASS (`0.198s`, `state=SAFE_PAUSE`, `part4_state=PAUSED`, `pause_causes=["BED_EXIT"]`)
    7. `rosbag_evidence`: PASS (`0.015s`, MCAP 총 `104`건 — `/ui/mission_req: 1`, `/ui/ack: 1`, `/robot3/hospital_nav/command: 2`, `/fleet/state: 58`, `/patient/bed_exit: 42`, `bed_exit` 수신율 `19.2 Hz`)
  - MCAP 인과 시각 오프셋:
    - `mission_req (0.000s)` → `ack (+0.102s)` → `move (+1.204s)` → `moving (+5.401s)` → `bed_exit_false (+9.224s)` → `bed_exit_true (+10.881s)` → `pause (+10.903s, +21.5ms)` → `safe_pause (+11.100s, +219.0ms)`
- 남은 한계:
  - `part_integration/part3_part5/README.md` 및 `src/part5_bed_exit/README.md`의 예시 문서에 남아 있는 `/robot2/hospital_nav/command` 표기는 필요 시 `/robot3/hospital_nav/command`로 후속 동기화 필요.

## 4. 경험의 재해석

### 내가 실제로 경험하고 느낀 것

- 통합 테스트에서 `TEST 2/7` 타임아웃이 발생해 원인을 확인했고, `robot3` 매핑 수정 후 전체 `7/7 PASS`로 정상 완주함.

### AI 해석·제안

- Part 3 `fleet.yaml`처럼 다른 파트와 공유하는 로봇 네임스페이스가 변경될 때 통합 테스트 스크립트가 하드코딩되어 있으면 실제 기능은 정상인데도 테스트가 타임아웃으로 실패할 수 있음.
- 추후 여유가 있을 때 `run_part3_part5_ros_experiment.py`가 `fleet.yaml`에서 `robots.robot_a.ns`를 직접 읽도록 보강하면 향후 로봇 번호가 다시 바뀌어도 재발을 막을 수 있음.

## 5. 정리

- 현재 결론: `part3_part5` 통합 테스트 네임스페이스 불일치 해결 및 `7 / 7 PASS` (`15.035초`, MCAP `104건`) 검증 완료.
- 다시 발생하면: `summary.json`의 `state_transitions`에서 `robot_a`가 실제 이동 중인지와 `src/part3_fleet_manager/config/fleet.yaml`의 `robots.robot_a.ns` 값을 먼저 대조.
- 다음에 반영할 것: 필요 시 `part_integration/part3_part5/README.md`의 `/robot2` 문서 표기 갱신 및 `fleet.yaml` 동적 로딩 검토.
