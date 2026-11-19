# PART 03–05 통합 실험

## 무엇을 실행하는가

`part_integration/run.sh`는 실제 로봇이 아닌 PART 03 mock과 PART 05 MP4 노드를
하나의 ROS 2 domain에 기동한다. 실행 순서는 다음과 같다.

1. `part3_fleet_manager fleet_mock.launch.py`를 기동한다.
2. `/ui/mission_req`로 `PATIENT_TRANSPORT`, C → A 임무를 발행한다.
3. `/ui/ack`의 성공 응답과 `/robot2/hospital_nav/command`의 `MOVE`를 확인한다.
4. `/fleet/state`에서 Robot A의 PART 04 상태가 `MOVING`인지 확인한다.
5. PART 05에 예제 MP4를 `loop_video=true`로 입력한다.
6. `/patient/bed_exit=true`를 관측한다.
7. `/robot2/hospital_nav/command`의 `PAUSE`를 확인한다.
8. `/fleet/state`에서 `SAFE_PAUSE`, `pause_causes=[BED_EXIT]`, PART 04 `PAUSED`를 확인한다.
9. 실험이 성공하거나 실패하면 자식 프로세스를 종료하고 로그를 저장한다.

이 실험은 PART 03과 PART 05 사이의 ROS 계약을 검증한다. PART 04는
`fleet_mock.launch.py`에 포함된 `mock_hospital_nav`로 대체하므로 실제 Nav2·모터·물리 제동을
검증하지 않는다.

## 어떤 프로그램이 실행되는가

- 진입점: Bash `part_integration/run.sh`
- 실험 제어·토픽 관측: Python 3 + `rclpy`
- PART 03: `ros2 launch part3_fleet_manager fleet_mock.launch.py`
- PART 05: `ros2 run part5_bed_exit bed_exit`
- 추론: Ultralytics Segmentation, CPU
- 기본 Python: `/home/hyunwoong/venvs/rokey_venv/bin/python3`

Python 경로가 다르면 다음처럼 지정한다.

```bash
export PART_INTEGRATION_PYTHON=/absolute/path/to/python3
```

## 사전 조건

```bash
cd ~/workspaces/bad_autonoumous_delivery
source /opt/ros/jazzy/setup.bash
colcon build --packages-select part3_fleet_manager part5_bed_exit --symlink-install
```

다음 입력이 존재해야 한다.

```text
src/part5_bed_exit/research/data/videos/test_input_sequence1.mp4
src/part5_bed_exit/research/configs/test_input_sequence1_methods_v1.json
src/part5_bed_exit/models/yolo11n-seg.pt
```

## 실행 명령

기본 ROS domain 96을 사용한다.

```bash
cd ~/workspaces/bad_autonoumous_delivery
bash part_integration/run.sh
```

다른 ROS 실험과 격리하려면 domain을 지정한다.

```bash
bash part_integration/run.sh --domain-id 104
```

실행하면 mock 로봇이 최대 수십 초 동안 이동 상태를 모사하고 CPU 모델 추론을
수행한다. 실행 전에 기존 ROS 노드와 domain 충돌 여부를 확인해야 한다.

## 합격 기준

다음 조건이 모두 `true`일 때만 PASS다.

- `mission_ack_ok`
- `move_observed`
- `part4_moving`
- `bed_exit_true_observed`
- `pause_observed`
- `safe_pause_with_bed_exit`

Fleet 내부 상태만 `SAFE_PAUSE`이고 wire 명령 `PAUSE`가 없으면 실패다.

## 결과 위치

`part_integration/results/`는 실행 코드가 아니라 로컬 실험 산출물 폴더다.

```text
part_integration/results/YYYYMMDD_HHMMSS_part3_part5/
├── summary.json
├── result.txt
├── events.jsonl
├── part3_launch.log
├── part5_mp4.log
├── fleet.db
└── ros_logs/
```

`summary.json`이 판정 근거이며 `result.txt`는 PASS/FAIL 요약이다.

세부 실험 순서와 합격 기준은
[`PART3_PART5_EXPERIMENT_PLAN.md`](PART3_PART5_EXPERIMENT_PLAN.md)에 정의한다.
