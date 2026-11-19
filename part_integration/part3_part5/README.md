# part3_part5 통합 실험

## 무엇을 실행하는가

`part_integration/part3_part5/run.sh`는 실제 로봇이 아닌 PART 03 mock과 PART 05 MP4 노드를
하나의 ROS 2 domain에 기동한다. 실행 순서는 다음과 같다.

1. `part3_fleet_manager fleet_mock.launch.py`를 기동한다.
2. 핵심 5개 토픽의 MCAP rosbag 기록을 시작한다. 임무 발행 전 mission·ack·command·fleet
   토픽의 recorder 구독을 모두 확인한다.
3. `/ui/mission_req`로 `PATIENT_TRANSPORT`, C → A 임무를 발행한다.
4. `/ui/ack`의 성공 응답과 `/robot2/hospital_nav/command`의 `MOVE`를 확인한다.
5. `/fleet/state`에서 Robot A의 PART 04 상태가 `MOVING`인지 확인한다.
6. PART 05에 예제 MP4를 `loop_video=true`로 입력한다.
7. `/patient/bed_exit=true`를 관측한다.
8. `/robot2/hospital_nav/command`의 `PAUSE`를 확인한다.
9. `/fleet/state`에서 `SAFE_PAUSE`, `pause_causes=[BED_EXIT]`, PART 04 `PAUSED`를 확인한다.
10. 최종 `SAFE_PAUSE + PAUSED + BED_EXIT` 상태를 3회 수신해 recorder가 마지막 상태를
    기록할 시간을 확보한다.
11. rosbag을 정상 종료한 뒤 다시 읽어 전체 메시지 순서를 독립 검증한다.
12. 실험이 성공하거나 실패하면 자식 프로세스를 종료하고 로그를 저장한다.

이 실험은 PART 03과 PART 05 사이의 ROS 계약을 검증한다. PART 04는
`fleet_mock.launch.py`에 포함된 `mock_hospital_nav`로 대체하므로 실제 Nav2·모터·물리 제동을
검증하지 않는다.

## 어떤 프로그램이 실행되는가

- 진입점: Bash `part_integration/part3_part5/run.sh`
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
bash part_integration/part3_part5/run.sh
```

다른 ROS 실험과 격리하려면 domain을 지정한다.

```bash
bash part_integration/part3_part5/run.sh --domain-id 104
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
- `rosbag_evidence`

`rosbag_evidence`는 다음 토픽을 기록한 MCAP을 종료 후 다시 읽어 판정한다.

```text
/ui/mission_req
/ui/ack
/robot2/hospital_nav/command
/fleet/state
/patient/bed_exit
```

bag 안에서 `MISSION → ACK/MOVE → MOVING → false → true` 순서와, `true` 이후의
`PAUSE` 및 `SAFE_PAUSE(BED_EXIT)`가 모두 확인되어야 한다. 서로 다른 토픽의 DDS 수신
시각은 미세하게 뒤바뀔 수 있으므로 `PAUSE`와 `SAFE_PAUSE` 상호 순서는 참고값으로
기록한다. 실시간 probe가 PASS여도 bag에 토픽이 없거나 인과 흐름이 맞지 않으면 전체
결과는 FAIL이다. Fleet 내부 상태만 `SAFE_PAUSE`이고 wire 명령 `PAUSE`가 없는 경우도
실패다.

## 결과 위치

`part_integration/part3_part5/results/`는 실행 코드가 아니라 로컬 실험 산출물 폴더다. 실행 종료 후
선택적으로 실험명을 입력하면 실행 디렉터리 이름 뒤에 접미사로 추가된다.

```text
part_integration/part3_part5/results/80805b4_v1_3/YYYYMMDD_HHMMSS_part3_part5[_실험명]/
├── summary.json
├── bag_evidence.json
├── result.txt
├── events.jsonl
├── execution.log
├── part3_launch.log
├── part5_mp4.log
├── rosbag_recorder.log
├── rosbag/
│   ├── metadata.yaml
│   └── *.mcap
├── fleet.db
└── ros_logs/
```

`rosbag/`이 ROS 통신의 원본 증거이고, `bag_evidence.json`과 `summary.json`의
`bag_evidence`가 bag을 다시 읽어 도출한 토픽별 메시지 수·상태 전이 시각·상세 원문과
판정 결과다. `result.txt`는 PASS/FAIL 요약이다.
콘솔과 `execution.log`에는 7개 합격 기준별 `START`, `EXPECTED`, `PROGRESS`,
`OBSERVED`, `END`와 전체 정량 결과가 기록된다. 콘솔의 `OBSERVED`는 핵심 필드와 8단계
인과 흐름만 한 줄씩 표시하며 전체 `/fleet/state` 원문은 출력하지 않는다.

`[BAG SYNC START]`와 `[BAG SYNC END]`는 실시간 probe만 마지막 상태를 받고 recorder가
종료되는 경쟁 상태를 방지하는 구간이다. 최종 안전 상태가 3회 확인된 뒤에만 bag 기록을
종료한다.

세부 실험 순서와 합격 기준은
[`PART3_PART5_EXPERIMENT_PLAN.md`](PART3_PART5_EXPERIMENT_PLAN.md)에 정의한다.
