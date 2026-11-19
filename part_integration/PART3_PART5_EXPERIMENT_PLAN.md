# PART 03–05 통합 실험 계획

## 1. 목적과 범위

PART 05의 실제 MP4 추론 결과가 `/patient/bed_exit`로 전달되고, PART 03이 환자 운송 중인
Robot A에 `PAUSE`를 발행한 뒤 `/fleet/state`를 `SAFE_PAUSE`로 변경하는지 검증한다.
이번 자동 실험의 PART 04 경계는 `/robot2/hospital_nav/command`를 구독하는 mock이다.
실제 Nav2·로봇 제동 검증은 같은 관측 항목을 유지한 별도 실기 단계에서 수행한다.

## 2. 고정 입력과 환경

- PART 03: `fleet_mock.launch.py`, `assignment_mode=ROLE`, Robot A=`/robot2`
- Mock 프로토콜: `robot_a_r1=true`, `robot_b_r1=true` (`id=...` 요청 ID 허용)
- PART 05 입력: `research/data/videos/test_input_sequence1.mp4`
- PART 05 설정: `research/configs/test_input_sequence1_methods_v1.json`
- MP4 정책: `loop_video=true`
- 임무: `PATIENT_TRANSPORT`, C → A, level 2
- ROS 격리: `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`, 기본 domain 96
- UI는 판정 경로에 포함하지 않고 별도 관찰자로만 사용한다.

## 3. 자동 실험 순서와 합격 기준

| 단계 | 자극 | 관측 | 합격 기준 |
| --- | --- | --- | --- |
| 준비 | PART 03 mock 기동 | `/fleet/state`, `/ui/mission_req` | 발행자·구독자 발견 |
| 이동 | 환자 운송 요청 | ack, command, `/fleet/state` | 요청 승인, `MOVE`, Part 4 `MOVING` 확인 |
| 입력 준비 | PART 05 MP4 loop 기동 | `/patient/bed_exit` | `false` 1회 이상 |
| 이탈 | MP4의 이탈 구간 | `/patient/bed_exit` | `true` 확인 |
| 정지 | PART 03 안전 처리 | command, `/fleet/state` | `PAUSE`, `SAFE_PAUSE`, `BED_EXIT` 원인 |
| 종료 | 실험 프로세스 SIGINT | 프로세스 종료 코드·로그 | 자식 프로세스 정리, 산출물 저장 |

`BED_MONITOR_LOST`만으로 정지한 경우는 합격으로 처리하지 않는다. 최종 상태의
`pause_causes`에 반드시 `BED_EXIT`가 있어야 한다.

## 4. 실행

워크스페이스를 빌드하고 팀 가상환경에서 실행한다.

```bash
cd ~/workspaces/bad_autonoumous_delivery
source /opt/ros/jazzy/setup.bash
colcon build --packages-select part3_fleet_manager part5_bed_exit --symlink-install

bash part_integration/run.sh --domain-id 104
```

기존 domain 96을 사용 중이면 `--domain-id 97`처럼 변경한다.

## 5. 산출물

각 실행은 다음 디렉터리를 새로 만든다.

```text
part_integration/results/
└── YYYYMMDD_HHMMSS_part3_part5/
    ├── summary.json       # 합격 기준별 결과, 명령, 상태 전이
    ├── result.txt         # PASS/FAIL 한 줄 요약
    ├── events.jsonl       # 시간순 토픽 관측값
    ├── part3_launch.log   # fleet manager와 mock 로그
    ├── part5_mp4.log      # MP4 추론·반복 로그
    ├── fleet.db           # 해당 실행 전용 PART 03 DB
    └── ros_logs/          # rclpy 로그
```

## 6. 후속 단계

1. 원격 브라우저 UI를 관찰자로 추가해 침상 이탈 경보와 Robot A 정지 표시를 확인한다.
2. mock PART 04를 실제 `hospital_nav`로 교체해 동일한 `MOVE`·`PAUSE` 계약을 확인한다.
3. 실제 로봇 단계에서는 도크 해제, 저속 제한, 비상정지 담당자 배치 후 물리 정지를
   측정한다. 자동 실험이 실제 로봇을 직접 기동하도록 확장하지 않는다.
