# 단계 3 · ROS 2 Jazzy mock 시나리오

> 4J(11/19)부터 namespace가 바뀌었다: 침상형 이송 로봇(Robot A) = `/robot3`, 약품 운송 로봇(Robot B) = `/robot2` (D-29). 아래 기록은 그 전 이름이다.

단계 2b에서 다섯 시나리오를 각각 새 DB로 실행했다. 아래 발췌는 실제 `/fleet/state`와 `/mission/event`에서 관련 필드를 골랐다. 시나리오 5는 `hospital_nav`의 명령과 응답도 함께 기록했다.

## 실행

워크스페이스 루트에서 패키지를 빌드한다.

```bash
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install --packages-select part3_fleet_manager part1_ui_dashboard
```

모든 터미널에서 같은 환경을 설정한다. 아래 도메인은 로컬 mock 전용이다. 기존 셸이 팀의 Fast DDS discovery server를 지정했다면 그 값도 해제한다.

```bash
source /opt/ros/jazzy/setup.bash
source install/local_setup.bash
unset ROS_DISCOVERY_SERVER ROS_STATIC_PEERS
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST ROS_DOMAIN_ID=81
```

기본 실행 명령은 다음과 같다. 기본 속도는 0.2 m/s이며 두 mock 모두 R1을 지원한다. 관제는 `/fleet_manager`, mock은 `/robot2/mock_hospital_nav`와 `/robot3/mock_hospital_nav`이다.

```bash
ros2 launch part3_fleet_manager fleet_mock.launch.py
```

아래 재현에서는 **시나리오마다 기존 launch를 Ctrl+C로 종료**한 뒤 새 DB로 시작한다. 기록에 사용한 mock 속도는 0.6 m/s다. 정지 판정, 교착 확인, 카드 만료 시간은 기본값을 사용했다.

```bash
# 시나리오 1 · 역할별 후속 배차. Robot A는 R1, Robot B는 구형
ros2 launch part3_fleet_manager fleet_mock.launch.py \
  db_path:="$(mktemp -d)/fleet.db" mock_speed:=0.6 robot_b_r1:=false assignment_mode:=ROLE

# 시나리오 2~5 · 기본 배터리 배정으로 각각 새로 시작
ros2 launch part3_fleet_manager fleet_mock.launch.py \
  db_path:="$(mktemp -d)/fleet.db" mock_speed:=0.6
```

다른 터미널에서 필요한 토픽을 구독한다. 두 로봇의 `link_ok`가 모두 `true`이고 `battery_pct`가 A 80, B 60이면 요청을 보낸다. 시나리오 2·3의 서로 다른 응급도 요청은 첫 요청의 배정을 확인하고 둘째를 보낸다. 같은 tick에 접수하면 공용 대기열에서 높은 level이 먼저 배정된다.

```bash
ros2 topic echo /fleet/state std_msgs/msg/String --field data --full-length
ros2 topic echo /mission/event std_msgs/msg/String --field data --full-length
ros2 topic echo /ui/ack std_msgs/msg/String --field data
```

### 발행 명령

아래 Bash 함수는 ICD JSON을 `std_msgs/msg/String.data`에 담는다. 확인 명령은 해당 로봇이 `WAIT_CONFIRM`일 때 한 번씩 실행한다. 이 예시의 임무 ID는 새 DB에 요청을 예시 순서로 보냈을 때의 값이다. 다른 DB에서는 `/ui/ack`의 `mission_id`를 쓴다.

```bash
pub() {
  ros2 topic pub --once -w 1 "$1" std_msgs/msg/String "{data: '$2'}"
}
confirm() {
  pub /ui/operator_cmd "{\"v\":1,\"cmd_id\":\"ok-$1-$2-$3\",\"cmd\":\"CONFIRM_STEP\",\"robot\":\"$1\",\"mission_id\":\"$2\",\"result\":\"OK\",\"by\":\"stage3\"}"
}
go() {
  pub /ui/operator_cmd "{\"v\":1,\"cmd_id\":\"$1\",\"cmd\":\"GO_ZONE\",\"robot\":\"$2\",\"zone\":\"$3\",\"by\":\"stage3\"}"
}
```

## PART 01 화면으로 재현하기 (단계 3D)

위의 PART 03 launch는 관제와 mock 두 대를 실행한다. 화면까지 함께 띄울 때는 같은 환경에서 아래 PART 01 launch를 사용한다. 새 DB를 지정하면 표의 임무 번호 순서로 확인할 수 있다.

```bash
# 시나리오 1의 역할별 후속 대기열과 R1 전후 호환
ros2 launch part1_ui_dashboard demo_mock.launch.py \
  db_path:="$(mktemp -d)/fleet.db" mock_speed:=0.6 assignment_mode:=ROLE robot_b_r1:=false

# 시나리오 2~5의 배터리 우선 배정
ros2 launch part1_ui_dashboard demo_mock.launch.py \
  db_path:="$(mktemp -d)/fleet.db" mock_speed:=0.6
```

[관제 화면](http://127.0.0.1:8090/#/dashboard)을 열고 **로봇 연결 2 / 2**를 확인한다. 오른쪽 아래 **시험 도구**에서 번호를 고르면 시작 요청들이 들어간다. 아래 표의 확인과 결정은 화면에서 누른다. 주소와 포트는 PART 01의 `config/ui.yaml`에서 설정한다.

| 번호 | 화면에서 하는 일 | 종료 확인 |
|---|---|---|
| 1. 동시 임무 | **배정 대기**에서 A와 B의 후속 임무를 확인한다. 각 정류장에서 **확인했습니다**를 체크하고 다음 이동 또는 수령 완료를 누른다. | **이송 작업 → 완료**에 환자 이송 2건, 물품 2건. 두 로봇 대기. |
| 2. 통로 양보 | A의 **통로 양보 중** 배지와 **판단 근거**의 응급도를 확인한다. B 통과 후 두 로봇의 정류장을 확인한다. | 양보 시작·종료 기록, 임무 2건 완료. |
| 3. 교착 | B방·E방 배치 후 뜨는 결정 카드에서 카운트다운 중 **추천**을 누른다. 양보가 끝나면 각 정류장을 확인한다. | 카드 종료 사유 `selected`, 교착 양보 완료, 본 임무 2건 완료. |
| 4. 막힘 | A가 통로에서 멈춘 뒤 뜨는 경보를 **확인**한다. 결정 카드는 **나중에**로 접어 B의 진입 대기를 볼 수 있다. | 통로 막힘 해제, 카드 자동 종료, 두 직접 이동 완료. |
| 5. CALL 세 건 | **배정 대기**에서 세 번째 호출의 미배정을 확인한다. A의 첫 호출부터 수령 완료를 누르고 새 담당 로봇을 확인한다. | A가 세 번째 호출을 맡고, 호출 3건 완료 후 A와 B가 각각 D·F에서 대기. |

ROS 토픽으로 같은 상황을 재현하는 명령과 이전 단계의 토픽 발췌는 아래 각 절에 있다. 단계 3D의 실제 브라우저 캡처와 디자인 비교는 [화면 검증 보고](ui/stage3d-report.md)에 있다.

## 1. 두 로봇 동시 임무와 후속 배차

Robot A의 픽업은 C, 목적지는 A다. Robot B는 F, E, B 순서로 확인한다. 첫 두 임무가 진행 중일 때 후속 두 건을 접수한다.

기본 모드에서도 네 건이 모두 완료됐지만, 두 후속 임무가 기다릴 때 `missions[].robot`은 모두 `null`이었다. 위의 `assignment_mode:=ROLE`로 다시 실행하자 기다리는 동안부터 각각 `robot_a`와 `robot_b`가 표시됐다. 아래는 테스트 26과 같은 ROLE 실행에서 수집한 값이다.

```bash
pub /ui/mission_req '{"v":1,"req_id":"p","type":"PATIENT_TRANSPORT","level":2,"patient":{"name":"모의환자","chart_no":"DEMO"},"pickup":"C","destination":"A","requested_by":"stage3"}'
pub /ui/mission_req '{"v":1,"req_id":"m","type":"MED_ROUND","level":3,"stops":[{"zone":"F","item":"모의물품"},{"zone":"E","item":"모의물품"},{"zone":"B","item":"모의물품"}],"requested_by":"stage3"}'
pub /ui/mission_req '{"v":1,"req_id":"p2","type":"PATIENT_TRANSPORT","level":2,"patient":{"name":"모의환자","chart_no":"DEMO"},"pickup":"D","destination":"A","requested_by":"stage3"}'
pub /ui/mission_req '{"v":1,"req_id":"m2","type":"MED_ROUND","level":3,"stops":[{"zone":"F","item":"모의물품"}],"requested_by":"stage3"}'
```

각 로봇의 도착에 맞춰 아래에서 해당하는 한 줄을 보낸다. 두 로봇 사이의 도착 순서는 조작 시점에 따라 달라진다.

```bash
# Robot A · C, A 확인 후 후속 임무의 D, A 확인
confirm robot_a M-0001 C
confirm robot_a M-0001 A
confirm robot_a M-0003 D
confirm robot_a M-0003 A

# Robot B · F, E, B 확인 후 후속 임무의 F 확인
confirm robot_b M-0002 F
confirm robot_b M-0002 E
confirm robot_b M-0002 B
confirm robot_b M-0004 F
```

**실측 `/fleet/state` 발췌**

```json
{
  "stamp": 1794974832.7073293,
  "robots": {
    "robot_a": {
      "state": "MOVING",
      "part4_state": "MOVING",
      "mission_id": "M-0001"
    },
    "robot_b": {
      "state": "MOVING",
      "part4_state": "MOVING",
      "mission_id": "M-0002"
    }
  },
  "missions": [
    {
      "mission_id": "M-0001",
      "robot": "robot_a",
      "status": "ACTIVE"
    },
    {
      "mission_id": "M-0003",
      "robot": "robot_a",
      "status": "QUEUED"
    },
    {
      "mission_id": "M-0002",
      "robot": "robot_b",
      "status": "ACTIVE"
    },
    {
      "mission_id": "M-0004",
      "robot": "robot_b",
      "status": "QUEUED"
    }
  ]
}
```

**실측 `/mission/event` 발췌** · 한 줄이 이벤트 한 건이다.

```jsonl
{"ts":1794974843.807327,"type":"MISSION_DONE","robot":"robot_b","mission_id":"M-0002","detail":{"reason":"DONE"}}
{"ts":1794974851.9072018,"type":"MISSION_DONE","robot":"robot_b","mission_id":"M-0004","detail":{"reason":"DONE"}}
{"ts":1794974855.807352,"type":"MISSION_DONE","robot":"robot_a","mission_id":"M-0001","detail":{"reason":"DONE"}}
{"ts":1794974861.8074043,"type":"MISSION_DONE","robot":"robot_a","mission_id":"M-0003","detail":{"reason":"DONE"}}
```

## 2. 마주 오는 통로에서 양보

D에서 출발하는 Robot A는 C행 Level 1, F에서 출발하는 Robot B는 A행 Level 3으로 요청한다. 두 명령을 연이어 실행한다. A가 통로에서 멈추고, B가 지난 뒤 다시 출발한다.

```bash
pub /ui/mission_req '{"v":1,"req_id":"p","type":"PATIENT_TRANSPORT","level":1,"patient":{"name":"모의환자","chart_no":"DEMO"},"pickup":"C","destination":"A","requested_by":"stage3"}'
pub /ui/mission_req '{"v":1,"req_id":"m","type":"MED_ROUND","level":3,"stops":[{"zone":"A","item":"모의물품"}],"requested_by":"stage3"}'

# 각 정류장 도착 시
confirm robot_b M-0002 A
confirm robot_a M-0001 C
confirm robot_a M-0001 A
```

**실측 `/fleet/state` 발췌**

```json
{
  "stamp": 1794974833.1527781,
  "robots": {
    "robot_a": {
      "state": "GIVE_WAY",
      "part4_state": "PAUSED",
      "mission_id": "M-0001"
    },
    "robot_b": {
      "state": "MOVING",
      "part4_state": "MOVING",
      "mission_id": "M-0002"
    }
  },
  "resources": {
    "CORRIDOR": {
      "holders": [
        "robot_b",
        "robot_a"
      ],
      "give_way": {
        "yielding": "robot_a",
        "to": "robot_b",
        "since": 1794974833.052737
      },
      "blocked": false,
      "queue": []
    }
  }
}
```

**실측 `/mission/event` 발췌** · 한 줄이 이벤트 한 건이다.

```jsonl
{"ts":1794974833.052737,"type":"CORRIDOR_YIELD_START","robot":"robot_a","mission_id":"M-0001","detail":{"reason":"Robot A 통로 양보 중 (Robot B Level 3)"}}
{"ts":1794974835.7527587,"type":"CORRIDOR_YIELD_END","robot":"robot_a","mission_id":"M-0001","detail":{"reason":"우선 로봇이 지나가고 해제 거리만큼 벌어졌습니다."}}
```

## 3. B방과 E방 맞교환 교착

두 로봇을 먼저 해당 방으로 보낸다. 둘 다 `IDLE`이고 `ROOM_B.holders=["robot_a"]`, `ROOM_E.holders=["robot_b"]`가 되면 다음 두 임무를 등록한다. 카드 추천과 20초 기본 동작을 보려면 카드를 열어 둔다.

```bash
go setup-a robot_a B
go setup-b robot_b E

# 두 로봇의 방 도착을 확인한 뒤 실행
pub /ui/mission_req '{"v":1,"req_id":"p","type":"PATIENT_TRANSPORT","level":2,"patient":{"name":"모의환자","chart_no":"DEMO"},"pickup":"E","destination":"A","requested_by":"stage3"}'
pub /ui/mission_req '{"v":1,"req_id":"m","type":"MED_ROUND","level":3,"stops":[{"zone":"B","item":"모의물품"}],"requested_by":"stage3"}'

# 해소 후 각 정류장 도착 시
confirm robot_b M-0004 B
confirm robot_a M-0003 E
confirm robot_a M-0003 A
```

**실측 `/fleet/state` 발췌**

```json
{
  "stamp": 1794974838.7168348,
  "robots": {
    "robot_a": {
      "state": "HOLD",
      "part4_state": "COMPLETE",
      "mission_id": "M-0003"
    },
    "robot_b": {
      "state": "HOLD",
      "part4_state": "COMPLETE",
      "mission_id": "M-0004"
    }
  },
  "resources": {
    "ROOM_B": {
      "capacity": 1,
      "holders": [
        "robot_a"
      ]
    },
    "ROOM_E": {
      "capacity": 1,
      "holders": [
        "robot_b"
      ]
    }
  },
  "open_cards": [
    "K-af1f194562564ae4b1daba17f504381d"
  ]
}
```

**실측 `/mission/event` 발췌** · 한 줄이 이벤트 한 건이다.

```jsonl
{"ts":1794974838.7168348,"type":"DEADLOCK_DETECTED","robot":null,"mission_id":null,"detail":{"reason":"robot_a은 robot_b이 보유한 ROOM_E을 기다림, robot_b은 robot_a이 보유한 ROOM_B을 기다림."}}
{"ts":1794974858.8165734,"type":"CARD_CLOSED","robot":null,"mission_id":null,"detail":{"reason":"timeout","choice":"yield_a"}}
{"ts":1794974858.8165734,"type":"YIELD_START","robot":"robot_a","mission_id":"M-0003","detail":{"reason":"robot_a이 ROOM_B을 비우기 위해 D행 허가를 요청합니다.","destination":"D"}}
{"ts":1794974864.816646,"type":"YIELD_DONE","robot":"robot_a","mission_id":"M-0003","detail":{"reason":"robot_a이 D에 도착하여 원래 구간을 다시 요청합니다.","destination":"D"}}
```

## 4. 통로 막힘과 자동 해제

`stall_at`의 세 값은 map 좌표 x, y와 정지 시간(초)이다. mock은 해당 위치에 처음 접근할 때 한 번 멈춘다. 10초에 짧은 멈춤, 30초에 장기 막힘과 카드가 기록된다. B의 발행 명령은 A가 출발한 직후 아래처럼 이벤트를 기다리게 두면 막힘이 풀리기 전에 전송된다.

```bash
ros2 param set /robot2/mock_hospital_nav stall_at '[-1.545, -2.375, 36.0]'
go stall-a robot_a C
ros2 topic echo --once --field data --filter "'\"type\":\"BLOCKED\"' in m.data" \
  /mission/event std_msgs/msg/String && go after-block robot_b A
```

36초 뒤 A가 다시 움직이면 `UNBLOCKED`와 카드 종료가 발행된다. 이어서 B가 통로를 허가받고 두 이동이 완료된다.

**실측 `/fleet/state` 발췌**

```json
{
  "stamp": 1794974869.3491445,
  "robots": {
    "robot_a": {
      "state": "MOVING",
      "part4_state": "MOVING",
      "mission_id": "M-0001"
    },
    "robot_b": {
      "state": "HOLD",
      "part4_state": "IDLE",
      "mission_id": "M-0002"
    }
  },
  "resources": {
    "CORRIDOR": {
      "holders": [
        "robot_a"
      ],
      "give_way": null,
      "blocked": true,
      "queue": [
        {
          "robot": "robot_b",
          "level": 1,
          "since": 1794974869.3491445
        }
      ]
    }
  }
}
```

**실측 `/mission/event` 발췌** · 한 줄이 이벤트 한 건이다.

```jsonl
{"ts":1794974849.2489624,"type":"STALL_SHORT","robot":"robot_a","mission_id":"M-0001","detail":{"reason":"robot_a이 10.0997초 동안 진행하지 못했습니다."}}
{"ts":1794974869.249112,"type":"STALL_LONG","robot":"robot_a","mission_id":"M-0001","detail":{"reason":"robot_a이 30.0999초 동안 진행하지 못했습니다."}}
{"ts":1794974869.249112,"type":"BLOCKED","robot":"robot_a","mission_id":"M-0001","detail":{"reason":"robot_a이 30.0999초 동안 진행하지 못했습니다."}}
{"ts":1794974874.5491295,"type":"UNBLOCKED","robot":"robot_a","mission_id":null,"detail":{"reason":"막힘이 해제됐습니다."}}
{"ts":1794974874.5491295,"type":"CARD_CLOSED","robot":"robot_a","mission_id":null,"detail":{"reason":"resolved","choice":null}}
```

## 5. CALL 세 건 · 수령 완료, 복귀 중 재배정

새 DB, 기본 배정 모드에서 A·B·E행 호출을 순서대로 보낸다. 셋째의 대기를 확인할 때까지 수령 완료를 누르지 않는다. 로봇별 초기 배터리를 명시하려면 launch 인자로 `robot_a_battery:=80 robot_b_battery:=60`을 준다.

```bash
pub /ui/mission_req '{"v":1,"req_id":"call-1","type":"CALL","level":1,"destination":"A","item":"거즈","requested_by":"stage2b"}'
pub /ui/mission_req '{"v":1,"req_id":"call-2","type":"CALL","level":1,"destination":"B","item":"거즈","requested_by":"stage2b"}'
pub /ui/mission_req '{"v":1,"req_id":"call-3","type":"CALL","level":1,"destination":"E","item":"거즈","requested_by":"stage2b"}'
```

`M-0003`이 공용 대기열에 있고 A가 `WAIT_CONFIRM`일 때 첫 줄을 실행한다. A가 셋째 호출을 맡은 뒤 B와 A 각각의 정류장 도착에 맞춰 나머지 두 줄을 보낸다. 수령 확인을 마친 뒤에는 두 로봇 모두 자기 홈에 도착할 때까지 관측한다.

```bash
confirm robot_a M-0001 A
confirm robot_b M-0002 B
confirm robot_a M-0003 E
```

명령 순서는 별도 터미널에서 아래 두 토픽을 구독해 확인할 수 있다. 복귀 중에는 완료된 호출의 DB 행이 `DONE`이고, 로봇의 `state`는 `RETURNING`이다.

```bash
ros2 topic echo /robot2/hospital_nav/command std_msgs/msg/String --field data
ros2 topic echo /robot2/hospital_nav/status std_msgs/msg/String --field data --full-length
```

**실측 `/fleet/state` 발췌** · 공용 대기, A의 첫 복귀, 두 로봇의 최종 도착 순이다.

```jsonl
{"stamp":1794974829.1497655,"robots":{"robot_a":{"state":"MOVING","battery_pct":80.0,"mission_id":"M-0001"},"robot_b":{"state":"MOVING","battery_pct":60.0,"mission_id":"M-0002"}},"missions":[{"mission_id":"M-0003","robot":null,"status":"QUEUED","wait_reason":"쉬는 로봇 없음"},{"mission_id":"M-0001","robot":"robot_a","status":"ACTIVE"},{"mission_id":"M-0002","robot":"robot_b","status":"ACTIVE"}]}
{"stamp":1794974830.8499112,"robots":{"robot_a":{"state":"RETURNING","leg":{"from":"A","to":"D"},"mission_id":null},"robot_b":{"state":"MOVING","leg":{"from":"F","to":"B"},"mission_id":"M-0002"}}}
{"stamp":1794974844.4500291,"robots":{"robot_a":{"state":"IDLE","zone":"D"},"robot_b":{"state":"IDLE","zone":"F"}}}
```

**실측 `/mission/event` 발췌**

```jsonl
{"ts":1794974827.9497614,"type":"MISSION_ASSIGNED","robot":"robot_a","mission_id":"M-0001","detail":{"reason":"배터리와 첫 정류장 거리로 배정","battery_pct":80.0}}
{"ts":1794974828.0497358,"type":"MISSION_ASSIGNED","robot":"robot_b","mission_id":"M-0002","detail":{"reason":"배터리와 첫 정류장 거리로 배정","battery_pct":60.0}}
{"ts":1794974830.8499112,"type":"MISSION_DONE","robot":"robot_a","mission_id":"M-0001","detail":{"reason":"DONE"}}
{"ts":1794974830.9498603,"type":"LEG_START","robot":"robot_a","mission_id":null,"detail":{"reason":"D행 구간 수락","destination":"D","return_home":true}}
{"ts":1794974830.9498603,"type":"MISSION_ASSIGNED","robot":"robot_a","mission_id":"M-0003","detail":{"reason":"배터리와 첫 정류장 거리로 배정","battery_pct":80.0}}
{"ts":1794974832.1499135,"type":"LEG_START","robot":"robot_a","mission_id":"M-0003","detail":{"reason":"E행 구간 수락","destination":"E","return_home":false}}
```

**Robot A의 실제 명령과 취소 응답** · 상태 응답은 구간 ID와 이벤트만 발췌했다.

```text
/robot2/hospital_nav/command  MOVE A 1 id=L-0001-1
/robot2/hospital_nav/command  MOVE D 1 id=L-H-M-0001-1
/robot2/hospital_nav/command  CANCEL
/robot2/hospital_nav/status   {"mission_id":"L-H-M-0001-1","event":"MISSION_CANCELED"}
/robot2/hospital_nav/command  MOVE E 1 id=L-0003-1
/robot2/hospital_nav/command  MOVE D 1 id=L-H-M-0003-1
```

복귀 구간은 기본 Level 1(`mission.return_level`)로 자원을 요청한다. 운영자가 직접 보낸 `GO_ZONE`은 지정한 구역에서 대기하므로 시나리오 3·4의 시작 위치를 잡는 데 사용할 수 있다. 복귀 취소와 홈 도착이 경합하는 경우는 가짜 시계로 검증했다. [취소 확인 회귀 테스트와 처리 기준](troubleshooting/2026-11-18-home-cancel-arrival.md)

## mock 시험 파라미터

| ROS 파라미터 | 동작 |
|---|---|
| `battery_pct` | 로봇별 0~100%. mock은 매초 `battery_state`의 `percentage`에 0~1 값으로 발행한다. 예를 들어 `ros2 param set /robot2/mock_hospital_nav battery_pct 20.0`으로 하한을 시험할 수 있다. |
| `supports_request_id` | `true`이면 R1의 `id=`와 `COMMAND_REJECTED`를 지원한다. `false`이면 구형 문법을 쓰고 거절은 로봇 로그에 남긴다. 관제의 자동 판별은 launch를 새로 시작할 때 초기화된다. |
| `stall_at` | `[x, y, seconds]`를 ROS 실수 배열로 지정한다. 세 번째 값이 0이면 꺼진다. `stall_radius_m`는 좌표와 이동 선분의 허용 거리이며 기본은 0.05 m다. |
| `fail_next` | `true`이면 다음에 수락한 MOVE가 `MISSION_FAILED`로 끝난다. |
| `reject_next` | `true`이면 다음 MOVE 한 건을 응답 없이 버려 수락 타임아웃을 시험한다. |
| `drop_link` | `true`인 동안 1 Hz `STATE`를 중단한다. `false`로 바꾸면 다시 발행한다. |
