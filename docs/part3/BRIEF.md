# PART 03 중앙 관제(`fleet_manager`) 사양

- 담당 김준성 · 2026-11-19 정리본 · 대상: PART 01·03 코드를 쓰는 AI 에이전트와 사람
- 이 문서에는 **지금 시스템이 하는 일**만 적는다.
  - 단계별 작업 기록과 남은 일: [`STAGES.md`](STAGES.md)
  - 바꾼 이유와 결정 기록: [`DECISIONS.md`](DECISIONS.md)
  - 화면·DB·화면 로직: [`WEB_DB_DESIGN.md`](WEB_DB_DESIGN.md)
  - 문서 목록: [`README.md`](README.md)
- 절 번호는 코드 주석과 테스트가 가리키므로 바꾸지 않는다. 특히 테스트 32가 4.8절의 `이벤트 종류:` 줄과 5.1절 상태 표를 직접 읽는다.

---

## 0. 읽는 법과 작업 규칙

### 0.1 기준 문서 우선순위

1. **이 문서**: 관제 판단, 인터페이스, 설정, 테스트.
2. **`WEB_DB_DESIGN.md`**: 화면 페이지, DB 테이블, 화면 로직. 둘이 다르면 그 문서를 따르고 보고한다.
3. 팀 Figma 보드 **"04-B. 인터페이스 규격서 v0.3"**: PART 01·PART 05와 주고받는 토픽.
4. **PART 04 실제 코드** (main `src/part4_amr_nav/hospital_nav/`): 로봇과 주고받는 토픽은 ICD가 아니라 이 코드를 따른다 (3.3절).
5. 레포 `AGENTS.md`: 파트 경계, Git 규칙.

노션은 업데이트가 멈춰 있어 보지 않는다. Figma의 "B.A.D 시스템 아키텍처 v0.3.1"은 카메라 경로가 빠진 판이라 보지 않는다.

### 0.2 할 수 있는 것과 금지

- PART 01(화면)과 PART 03(관제)은 둘 다 김준성 담당이다.
- 수정 가능: `src/part3_fleet_manager/`, `src/part1_ui_dashboard/`, `docs/part3/`
- 금지
  - 다른 파트 패키지 수정 (`part2`, `part4`, `part5`, `part6`). 다른 파트 코드를 import하지 않고 토픽으로만 통신한다. (preflight가 PART 04 `goal_manager.py`를 글자로 읽는 것만 예외. 실행하지 않는다)
  - 커스텀 msg/srv. 표준 메시지와 `std_msgs/String`(JSON)만 쓴다.
  - pip 설치. 외부 패키지는 아래 apt 패키지만 쓴다. 더 필요하면 쓰기 전에 보고한다.
    - `python3-yaml`, `python3-networkx`, `python3-shapely`, `python3-psutil`, `python3-flask`, `ros-jazzy-nav2-msgs`, `ros-jazzy-irobot-create-msgs`(없어도 노드는 뜬다)
  - 사용자 확인 없는 커밋·푸시. `.vscode/` 커밋.
  - `README.md`, `AGENTS.md` 수정 (팀 문서. 담당자가 직접 고친다).
- 환경: Ubuntu 24.04, ROS 2 Jazzy, Python 3.12, `colcon build --symlink-install`

### 0.3 막힐 때

- 이 문서에 없는 결정이 필요하면 코드에 박지 말고 `config/fleet.yaml` 값으로 빼고, 보고할 때 "임의로 정한 값" 목록에 적는다.
- 작업이 끝나면 멈추고 보고한다: 만든·바꾼 파일, 테스트 결과(통과/실패 수), 임의로 정한 값, 막힌 것.
- 이 문서와 코드가 다르면 코드를 이 문서에 맞추지 말고 먼저 보고한다. 문서가 틀렸을 수도 있다.

### 0.4 에이전트에게 처음 보내는 지시 (틀)

레포 루트 `AGENTS.md`에는 PART 03 내용이 없다. 에이전트가 이 문서를 자동으로 읽지 않으므로 처음에 직접 가리킨다.

```
너는 ROS 2 Jazzy 워크스페이스 bad_ws에서 PART 03 중앙 관제(src/part3_fleet_manager)와 PART 01 관제 화면(src/part1_ui_dashboard)을 맡는다.
1. docs/part3/BRIEF.md와 docs/part3/WEB_DB_DESIGN.md를 읽어라. 이 둘이 작업 기준이다. 레포의 AGENTS.md 규칙도 지켜라.
2. 이번 작업: <할 일과 끝났다는 기준>
3. 수정은 src/part3_fleet_manager/, src/part1_ui_dashboard/, docs/part3/ 안에서만 한다.
4. pip 설치, git commit, git push를 하지 마라. 필요한 apt 패키지가 있으면 이름만 알려라.
5. 끝나면 멈추고 보고해라: 만든·바꾼 파일, pytest 명령과 결과(통과/실패 수), 임의로 정한 값, 막힌 것. 보고서는 docs/part3/ui/<단계>-report.md로 남겨라.
```

- 에이전트가 바꾼 내용은 커밋 전에 `git status`, `git diff`로 사람이 확인한다.
- 끝난 작업은 `STAGES.md`에 한 줄 더한다.

---

## 1. 무엇을 만드나

**한 줄 요약**: TurtleBot4 2대가 병원 6구역(A~F)을 오갈 때, 가운데 복도와 막다른 방(B, E)을 누가 언제 쓸지 관제가 정한다. 멈춤·교착·침상 이탈을 감지해 처리하고, 사람이 판단할 일은 결정 카드로 화면에 올린다.

- 로봇은 판단하지 않는다. 관제가 허가한 목적지로만 간다.
- 응급도(Level 1~3)는 사람이 임무를 등록할 때 정한다. 관제는 그 값으로 순서를 정한다.
- 관제(PART 03)와 관제 화면(PART 01)은 같은 담당자지만, 정해진 토픽으로만 통신한다. 화면은 지난 기록을 볼 때만 `bad.db`를 읽기 전용으로 연다.

### 1.1 로봇과 임무

| 논리 이름 | 실제 namespace | 화면 이름 | 홈 구역 |
|---|---|---|---|
| `robot_a` | `robot2` | Robot A | D |
| `robot_b` | `robot3` | Robot B | F |

- 관제 안에서는 `robot_a`, `robot_b`만 쓴다. 실제 namespace는 `fleet.yaml`의 `robots.<id>.ns` 한 줄로만 연결한다. namespace가 다르면 그 줄만 바꾼다.
- 로봇에 정해진 역할은 없다. 기본 배정(`IDLE_BEST_BATTERY`)은 쉬는 로봇 중 배터리가 많은 로봇에게 아무 임무나 준다 (5.1절). 역할로 나누는 `ROLE` 모드는 설정으로만 남아 있다 (`robots.<id>.role`, `type_to_robot`).
- 임무 종류
  - `PATIENT_TRANSPORT` 환자 이송: 픽업 구역 → (탑승 확인) → 목적지 구역 → (인계 확인) → 완료
  - `MED_ROUND` 물품 전달: 정류장 1 → (수령 확인) → 정류장 2 → … → 완료 (정류장 1~6개)
  - `CALL` 로봇 호출: 목적지 한 곳 → ([수령 완료]) → 완료
- 정류장 확인은 화면 버튼([확인]/[불일치])이다. QR(PART 02)은 범위 밖이다.

### 1.1a 팀 기본 흐름(11/18 Figma)과의 대응

팀의 13단계 기본 흐름을 관제가 그대로 지원한다. 다만 DB를 게시판처럼 1초마다 읽어 주고받지 않고, 토픽으로 주고받는다 (이유는 `DECISIONS.md` D-01).

| # | 기본 흐름 | 관제에서 하는 곳 |
|---|---|---|
| 1 | 간호사가 화면에서 호출 버튼 클릭 | 화면 → `/ui/mission_req` (`CALL` 등, 4.1절) |
| 2 | 임무 접수, 배정 대기로 기록 | `mission`: 검증 → DB `MISSION_CREATED` → 배정 대기열 |
| 3 | 쉬는 로봇이 있나? 없으면 배정 대기 유지 | `mission`: 배정 가능한 로봇이 없으면 대기열에 둔 채 매 tick 다시 본다 (5.1절 배정) |
| 4 | 로봇 정하기: 배터리 많은 로봇 우선, 이동 중 기록 | `mission`: `assignment.mode: IDLE_BEST_BATTERY` (5.1절 배정) |
| 5 | ROS 노드가 1초마다 할당된 임무 확인 | 폴링 없음. 10 Hz tick이 바로 판단한다 |
| 6 | 로봇에 이동 명령, 이동 중 기록 | 자원 허가 → `MOVE` → `LEG_START` |
| 7 | Nav2로 목적지까지 주행 | PART 04 |
| 8 | 도착 알림 | PART 04 `ARRIVED` 이벤트 |
| 9 | 도착으로 기록 | `ARRIVE` → `WAIT_CONFIRM` |
| 10·11 | 새로고침하면 DB를 읽어 도착 상태와 [수령 완료] 버튼 표시 | 화면이 `/fleet/state`(5 Hz)를 받아 바로 표시한다. 새로고침 필요 없음 |
| 12 | 간호사가 [수령 완료] 클릭 | 화면 → `/ui/operator_cmd` `CONFIRM_STEP OK` |
| 13 | 완료 처리, 로봇 복귀 중 기록, 복귀 신호 | `MISSION_DONE` → 홈 구역으로 `MOVE` → `RETURNING` → 도착하면 `IDLE` (5.1절 복귀) |

### 1.2 범위에서 뺀 것

로봇 오류 시 대타, 진행 중 응급 격상, 배터리 부족 대응, 관제 통신 두절, QR, 맵 개조, 로그인.

---

## 2. 전체 구조

```
 [운영자 브라우저] ⇄ HTTP/SSE ⇄ [ui_server · PART 01 (WEB_DB_DESIGN.md)]
                                      │  /ui/mission_req · /ui/operator_cmd · /ui/decision (String JSON)
                                      │  /robot/pause · /robot/resume (Trigger, 서버 = 관제, ROS 명령줄용)
                                      ▼
 [bed_exit · PART 05] ─ /patient/bed_exit (Bool) ─▶ [fleet_manager · PART 03, 이 문서]
                                      │  /fleet/state 5 Hz · /fleet/decision_req · /mission/event · /ui/ack ─▶ 화면
                                      │  bad.db (SQLite WAL. 관제만 쓴다. 화면은 읽기 전용)
                                      ▼  /<ns>/hospital_nav/command (String)
 [hospital_nav · PART 04, 로봇마다] ─ /<ns>/hospital_nav/status (String JSON) ─▶ 관제
                                     (+ 관제가 직접 구독: /<ns>/amcl_pose, battery_state, hazard_detection, dock_status)
 [obstacle_classifier · PART 06, 로봇마다] ◀─ /<ns>/obstacle/inspect_image ─ 관제
                                           ─ /<ns>/obstacle_classifier/result ─▶ 관제
```

- 모든 노드가 관제 PC에서 띄운 Fast DDS 발견 서버 하나로 서로를 찾는다 (10장).
- `fleet_manager`는 **ROS 노드 1개**다. 판단 모듈(`mission`, `route_planner`, `resource_manager`, `deadlock`, `stall`, `obstacle`, `decisions`, `safety`, `store`)은 ROS를 모르는 순수 파이썬 클래스다 (`core/`).
- 핵심 규칙: **콜백은 저장만, 판단은 10 Hz tick 한 곳에서.** 구독 콜백은 최신값과 받은 시각만 입력 큐에 넣는다. `tick(now)` 하나가 모든 판단과 발행을 한다. 서비스 콜백도 요청을 큐에 넣고 바로 답할 수 있는 것만 답한다 (4.7절). 그래서 같은 입력을 core에 넣는 것만으로 노드 동작을 테스트할 수 있다.

---

## 3. 인터페이스

### 3.1 화면(PART 01) ↔ 관제

| ICD ID | 방향 | 이름 | 타입 | 시점 |
|---|---|---|---|---|
| IF-P1-01 | 화면 → 관제 | `/ui/mission_req` · `/ui/operator_cmd` · `/ui/decision` | `std_msgs/String` JSON | 임무 등록 · 직접 명령 · 카드 선택 시 |
| IF-P1-02a | 누구나 → 관제 | `/robot/pause` | `std_srvs/Trigger` (서버 = 관제) | ROS 명령줄 긴급 정지 |
| IF-P1-02b | 누구나 → 관제 | `/robot/resume` | `std_srvs/Trigger` (서버 = 관제) | ROS 명령줄 재개 |
| IF-P3-01 | 관제 → 화면 | `/fleet/state`(5 Hz) · `/fleet/decision_req` · `/mission/event` · `/ui/ack` | `std_msgs/String` JSON | state 5 Hz, 나머지는 생길 때 |
| IF-P3-04 | 관제 → DB | `bad.db` | SQLite WAL | 관제(`store`)만 쓴다. 화면은 읽기 전용 |

- 화면 버튼의 전체 정지·재개는 `/ui/operator_cmd`의 `PAUSE_ALL`·`RESUME_ALL`로 보낸다. 누른 사람을 남기기 위해서다 (4.7절).
- ICD v0.3의 "`/robot/resume` 미사용인지 검토 필요"는 오해다. 화면 → 관제 재개는 쓴다. 안 쓰는 것은 관제 → 로봇 resume 서비스다 (로봇 재개는 `RESUME` 명령, 3.3절).
- JSON 내용은 4장에 정했다. 바꾸면 화면과 관제를 같이 바꾼다.

### 3.2 PART 05 → 관제

| ICD ID | 이름 | 타입 | 뜻 |
|---|---|---|---|
| IF-P5-01 | `/patient/bed_exit` | `std_msgs/Bool` | `true` = 침상 이탈, `false` = 정상. 웹캠 30 Hz, 최근 5프레임 중 3번 이상 true면 true |

- PART 05는 타이머가 `capture_fps`(기본 30 Hz)마다 한 프레임을 처리하고, 판정되면 **매 프레임** 발행한다.
- 카메라 오류나 판정 불가일 때는 5프레임 이력을 비우고 **그 프레임은 발행하지 않는다** (거짓 정상 금지). 그래서 "안 들어옴"은 정상이 아니라 "모름"이다.
- 그래서 관제는 **이 토픽이 끊기면 감시가 끊긴 것**으로 본다 (5.6절 침상 감시 끊김).
- 남은 빈틈: 이력을 비운 직후 5프레임이 모이기 전에는 `False`(정상)를 발행한다. 카메라가 몇 프레임마다 실패하면 환자가 침대를 떠나도 `False`만 나간다. PART 05에 고쳐 달라고 요구했다 (11장 P5-R1).

### 3.2a PART 06 장애물 분류 ↔ 관제

기준 코드: main `src/part6_obstacle_classifier/`. 로봇 OAK-D 카메라로 한 번 찍어 YOLO11n으로 분류한다. PART 06은 자동 정지·재개를 하지 않는다. 언제 찍을지, 결과로 무엇을 할지는 관제가 정한다 (5.5절).

| 방향 | 토픽 | 타입 | 뜻 |
|---|---|---|---|
| 관제 → PART 06 | `/<ns>/obstacle/inspect_image` | `std_msgs/Empty` | 한 번 보내면 RGB·깊이 한 쌍을 받아 한 번 분류한다 (촬영 제한 3초) |
| PART 06 → 관제 | `/<ns>/obstacle_classifier/result` | `std_msgs/String` (JSON) | 아래 |

```json
{"status": "ok", "obstacle_present": true, "header": {"stamp": 1794985000.1, "frame_id": "oakd_rgb_camera_optical_frame"},
 "detections": [{"label": "person", "confidence": 0.82, "bbox": [210, 80, 400, 470], "pose": {"x": -1.52, "y": -2.31, "z": 0.0}},
                {"label": "chair", "confidence": 0.41, "bbox": [10, 200, 90, 300]}]}
```

- `status`: `ok` | `image_timeout` | `processing_error`. `ok`가 아니면 `obstacle_present`는 `null`이다 ("모름").
- `obstacle_present: false`는 "찍었는데 아무것도 없음"이다. "모름"과 다르다.
- `label`은 COCO 영어 이름이다 (`person`, `chair`, `suitcase` …). `pose`는 깊이가 유효할 때만 있고 map 좌표다.
- 요청 ID가 없다. 관제는 로봇마다 한 번에 요청 하나만 내고, 요청 뒤 `obstacle.timeout_s`(6초) 안에 온 첫 결과를 그 답으로 본다. 늦게 온 결과는 기록만 하고 판단에 쓰지 않는다.
- PART 06이 안 떠 있으면 결과가 오지 않는다. 그때는 "분류 불가"로 보고 5.5절의 시간 규칙대로만 한다.
- PART 06은 상자를 그린 결과 그림도 `/<ns>/obstacle_classifier/image/compressed`로 낸다. 관제는 쓰지 않는다.

### 3.3 관제 ↔ PART 04 — 실제 코드 기준

PART 04는 로봇마다 `hospital_nav`(CommandReceiver + MissionManager + NavigationManager)를 띄운다. 관제는 아래 두 토픽으로만 로봇을 움직이고 상태를 받는다.

**명령: 관제 → `/<ns>/hospital_nav/command` (`std_msgs/String`, 텍스트)**

| 명령 | 뜻 | PART 04가 받는 조건 |
|---|---|---|
| `MOVE <구역> <응급도>` 예: `MOVE F 3` | 구역 A~F로 이동 시작. 응급도 1~3 | 로봇 상태가 `IDLE`·`COMPLETE`·`FAILED`·`CANCELED`이고 주행 중이 아닐 때만. **`PAUSED`에서는 거절된다** |
| `PAUSE` | 주행 취소 후 정지 → `PAUSED` | 상태가 `MOVING`일 때만 |
| `RESUME` | 같은 목적지로 다시 출발 | 상태가 `PAUSED`일 때만 |
| `CANCEL` | 임무 취소 → `CANCELED` | 끝난 상태가 아닐 때. 끝난 상태에서 받은 `CANCEL`은 이벤트 없이 버린다 |

- 목적지는 **구역 이름(A~F)만** 된다. 좌표나 복도 중간 지점은 보낼 수 없다.
- `PAUSED`인 로봇을 다른 곳으로 보내려면 `CANCEL` → `CANCELED` 확인 → `MOVE` 순서로 보낸다.
- 도킹 상태는 보지 않는다. 도크에 붙은 채 `MOVE`를 받으면 언도킹 없이 바로 Nav2 주행을 시작한다 (R3 요청 중). 시험 전에는 로봇을 도크에서 떼어 둔다.
- PART 04에 요구한 R1(11장)이 들어오기 전과 후가 다르다. `robot_link`는 **두 경우를 모두** 처리한다.

| | R1 반영 전 (지금 main) | R1 반영 후 |
|---|---|---|
| 명령 형식 | `MOVE F 3` | `MOVE F 3 id=<관제 구간 ID>` (구간 ID 예: `L-0007-2` = 임무 M-0007의 2번째 구간) |
| 거절 알림 | 없음 (로그만). `accept_timeout_s`(3초) 안에 `MISSION_ACCEPTED`가 안 오면 거절로 본다 | `COMMAND_REJECTED` 이벤트 (`command`, `reason`, `request_id`, `state`). 타임아웃은 안전장치로 남긴다 |
| 임무 ID 짝짓기 | PART 04가 만든 12자리 ID. `MOVE` 직후 처음 오는 `MISSION_ACCEPTED`의 ID를 그 구간 ID로 기억한다 (로봇당 한 번에 한 구간만 보낸다) | 이벤트의 `mission_id`가 관제 구간 ID와 같다 |

- `robot_link.request_id: auto`(기본)면 로봇마다 이렇게 판단한다.
  1. 처음에는 `id=`를 붙여 보낸다.
  2. `MISSION_ACCEPTED`가 오고 그 `mission_id`가 보낸 ID와 같으면 R1 반영으로 기억한다.
  3. 3초 안에 아무것도 안 오면 (반영 전 코드는 `id=`가 붙은 명령을 문법 오류로 버린다) `id=` 없이 한 번 다시 보낸다. 그게 받아지면 그 로봇에는 이후 `id=`를 붙이지 않는다.
  4. 노드를 다시 켜면 처음부터 다시 판단한다.

**상태: `/<ns>/hospital_nav/status` → 관제 (`std_msgs/String`, JSON)**

1초마다 `STATE` 한 번, 그리고 일이 생길 때마다 `STATUS` 이벤트가 온다.

```json
{"type": "STATE", "robot_id": "robot2", "mission_id": "9f2c1a0b3d4e", "state": "MOVING",
 "destination": "F", "priority": 3, "reason": "", "pose": {"x": -1.52, "y": -2.31},
 "remaining_m": 2.84, "percent": 41.3}
```

```json
{"type": "STATUS", "event": "ARRIVED", "robot_id": "robot2", "mission_id": "9f2c1a0b3d4e",
 "destination": "F", "percent": 100.0, "pose": {"x": -0.81, "y": -5.08}, "goal_error_m": 0.03}
```

| `state` 값 | 뜻 |
|---|---|
| `IDLE` | 시작 후 임무 없음 |
| `MOVING` | 주행 중 |
| `STOPPING_PAUSE` → `PAUSED` | 일시정지 중 → 정지 완료 |
| `STOPPING_CANCELED` → `CANCELED` | 취소 중 → 취소 완료 |
| `COMPLETE` | 도착 (이벤트는 `ARRIVED`) |
| `FAILED` | 실패 (`reason`에 `"Nav2: ABORTED"` 등) |

| `event` 값 | 함께 오는 값 |
|---|---|
| `MISSION_ACCEPTED` | `destination`, `priority` |
| `NAVIGATING` | `destination` |
| `PROGRESS` (주행 중 1초마다) | `destination`, `remaining_m`, `percent` |
| `PAUSED` | `reason`, `remaining_m`, `percent` |
| `RESUMED` | — |
| `ARRIVED` | `destination`, `percent`, `pose`, `goal_error_m` |
| `MISSION_FAILED` · `MISSION_CANCELED` | `reason`, `remaining_m`, `percent` |
| `COMMAND_REJECTED` (R1 반영 후) | `command`, `reason`, `request_id`, `state` |
| `UNDOCKING` · `UNDOCKED` (R3 반영 후) | — (R3 반영 후 STATE에 `docked`가 더해지고 상태 `UNDOCKING`이 생긴다) |

- 모르는 `state`·`event` 값이 와도 죽지 않는다. 로그를 남기고 화면에 그대로 보여 준다.
- `pose`에는 방향(yaw)이 없다. 위치 판정에는 x, y만 쓴다.
- 팀 Figma 플로우차트 v0.4에는 PART 04 상태가 `RUNNING`·`PAUSE`로 그려져 있다. 코드는 아직 `MOVING`·`PAUSED`다. 이름이 바뀌면 `core/robot_protocol.py`만 고친다.

**관제가 직접 구독하는 표준 토픽** (PART 04 변경 없이 받는 것)

| 토픽 | 타입 | QoS | 용도 | 없으면 |
|---|---|---|---|---|
| `/<ns>/amcl_pose` | `geometry_msgs/PoseWithCovarianceStamped` | RELIABLE, TRANSIENT_LOCAL, depth 1 (PART 04와 같게) | 자원 진입·해제 판정. STATE의 pose(1 Hz)보다 촘촘하다 | STATE의 `pose`만 쓴다 |
| `/<ns>/battery_state` | `sensor_msgs/BatteryState` | sensor data(best effort) | 배정 순서, 화면 표시 | 배터리 "모름" |
| `/<ns>/hazard_detection` | `irobot_create_msgs/HazardDetectionVector` | sensor data(best effort) | 범퍼 충돌 경고 (5.6절) | 범퍼 경고 없음 |
| `/<ns>/dock_status` | `irobot_create_msgs/DockStatus` | sensor data(best effort) | 도킹 여부 표시 | R3 반영 후 STATE의 `docked`를 쓴다 |

- **amcl_pose는 로봇이 움직일 때만 새로 온다.** 그래서 "로봇과 연결되어 있나"는 pose 나이가 아니라 **STATE 수신 나이**로 판단한다 (`link_timeout_s`). 위치값은 amcl_pose와 STATE pose 중 더 최근 것을 쓴다.
- `hazard_detection`에는 범퍼 말고도 `BACKUP_LIMIT` 같은 항목이 주기적으로 섞여 온다. 관제는 `type == BUMP`만 범퍼 충돌로 본다.
- `irobot_create_msgs`가 없는 환경에서도 노드가 뜬다. import에 실패하면 범퍼·도킹 두 토픽만 끄고 경고 로그를 남긴다.

### 3.4 로봇 연결은 어댑터 하나에 모은다

- 로봇과 통신하는 부분은 `robot_link.py` 하나에만 둔다. `robot_link.mode: HOSPITAL_NAV`(3.3절)만 동작한다. `ICD_GOAL_POSE`는 자리만 있고 고르면 관제가 시작하지 않는다.
- 판단 모듈은 `robot_link`가 넘겨주는 공통 형식(`RobotReport`: link_ok, part4_state, pose, battery, last_event)만 본다. 명령도 공통 형식(`go(robot, zone, level)`, `pause`, `resume`, `cancel`)으로만 낸다.
- PART 04와 주고받는 **규칙**은 `core/robot_protocol.py`(순수 파이썬)에 둔다: 수락 대기와 재전송, `PAUSED`면 `CANCEL` → `CANCELED` 확인 → `MOVE` 순서, `id=` 지원 여부 자동 판단, 이벤트와 구간 짝짓기. `robot_link.py`는 토픽을 구독·발행하고 문자열을 이 모듈에 넘기기만 한다. 그래서 테스트 11·12·19를 ROS 없이 돌린다.

---

## 4. JSON 형식 (화면과 맞출 것)

모든 메시지에 `"v": 1`을 넣는다. 시각은 epoch 초(float)다.

### 4.1 `/ui/mission_req`

```json
{"v": 1, "req_id": "ui-20261118-0007", "type": "PATIENT_TRANSPORT", "level": 2,
 "patient": {"name": "홍길동", "chart_no": "P-0012"},
 "pickup": "A", "destination": "C", "requested_by": "nurse01"}
```

```json
{"v": 1, "req_id": "ui-20261118-0008", "type": "MED_ROUND", "level": 1,
 "stops": [{"zone": "A", "item": "인슐린"}, {"zone": "B", "item": "진통제"}], "requested_by": "nurse01"}
```

```json
{"v": 1, "req_id": "ui-20261118-0009", "type": "CALL", "level": 1,
 "destination": "A", "item": "거즈", "requested_by": "nurse01"}
```

- `CALL`은 팀 기본 흐름의 "호출"이다. 로봇이 목적지 한 곳으로 가서 [수령 완료]를 기다리고, 확인되면 복귀한다. `item`은 선택이다.
- 검증: `type`은 `PATIENT_TRANSPORT`·`MED_ROUND`·`CALL` 중 하나, `level`은 1~3, 구역은 A~F, `MED_ROUND`의 `stops`는 1~6개. 실패하면 `/ui/ack`에 `ok:false, code:"E-400"`.
- `patient.chart_no`는 필수, `patient.name`은 선택이다. 이름이 없으면 비워 두고 화면은 차트번호를 보여 준다. 이름 칸에 차트번호를 대신 넣지 않는다.
- `requested_by`·`by`는 화면의 조작자 이름 칸 값이다 (로그인 없음).
- 로봇 배정은 5.1절 "배정"을 따른다.

### 4.2 `/ui/operator_cmd`

```json
{"v": 1, "cmd_id": "c-31", "cmd": "CONFIRM_STEP", "robot": "robot_a", "mission_id": "M-0007", "result": "OK", "by": "nurse01"}
```

| `cmd` | 추가 필드 | 동작 |
|---|---|---|
| `CONFIRM_STEP` | `robot`, `mission_id`, `result`: `OK`/`MISMATCH` | 정류장 확인. `MISMATCH`면 보류 카드 |
| `CANCEL_MISSION` | `mission_id` | 임무 취소 (5.1절 복도 안 규칙) |
| `RETRY` | `robot` | 실패한 구간을 다시 시도 |
| `PAUSE_ROBOT` · `RESUME_ROBOT` | `robot` | 로봇 한 대만 정지·재개 |
| `PAUSE_ALL` · `RESUME_ALL` | 없음 | 화면의 [안전 정지 요청]·[전체 재개]. `/robot/pause`·`/robot/resume`과 같은 동작·같은 거절 조건이고, `by`를 기록에 남긴다 |
| `GO_ZONE` | `robot`, `zone` | 지정 구역으로 보내기 (임무 없는 로봇만. 자원 규칙은 똑같이 적용) |
| `RESOURCE_CLEAR_CONFIRM` | `resource` | 점유 불명(`UNKNOWN`) 자원을 사람이 확인하고 비움 |

모든 명령에 `/ui/ack`로 답한다.

### 4.3 `/ui/decision`

```json
{"v": 1, "card_id": "K-0e3a…", "choice": "yield_a", "by": "nurse01"}
```

### 4.4 `/ui/ack`

```json
{"v": 1, "ref": "ui-20261118-0007", "ok": true, "code": null, "message": "M-0007 접수 · Robot A 배정", "mission_id": "M-0007"}
```

- `ref`는 받은 `req_id`·`cmd_id`·`card_id`다. JSON이 깨져 ID를 읽을 수 없으면 `ref: null`.
- 에러 코드: `E-400` 잘못된 요청, `E-409` 지금 상태에서 할 수 없음, `E-423` 자원 잠김·점유 불명.

### 4.5 `/fleet/state` (5 Hz)

```json
{"v": 1, "stamp": 1794963604.2,
 "robots": {
   "robot_a": {"label": "Robot A", "ns": "robot2", "link_ok": true, "state": "GIVE_WAY", "state_since": 1794963601.5,
               "part4_state": "PAUSED", "region": "CORRIDOR", "zone": null, "pose": {"x": -1.62, "y": -2.90},
               "battery_pct": 82, "mission_id": "M-0007", "leg": {"from": "C", "to": "A"},
               "route": [[-1.62, -2.90], [-1.37, -0.55], [-2.20, 0.50]],
               "wait_reason": "Robot A 복도 양보 중 (Robot B Level 3)", "progress_pct": 38.0,
               "remaining_m": 3.1, "stall_s": 0, "paused_by": "CORRIDOR_YIELD", "obstacle": null},
   "robot_b": {"...": "같은 형식"}},
 "resources": {
   "CORRIDOR": {"state": "OCCUPIED", "capacity": 2, "holders": ["robot_b", "robot_a"], "holder": "robot_b",
                "blocked": false, "queue": [], "queue_detail": [],
                "reason": "복도 비어 있음 + Level 3 > Level 2 → Robot B 허가", "decided_at": 1794963590.0,
                "give_way": {"yielding": "robot_a", "to": "robot_b", "since": 1794963601.5}},
   "ROOM_B": {"state": "FREE", "capacity": 1, "holders": [], "holder": null, "blocked": false, "queue": [], "give_way": null},
   "ROOM_E": {"state": "GRANTED", "capacity": 1, "holders": ["robot_b"], "holder": "robot_b", "blocked": false,
              "queue": [], "give_way": null}},
 "missions": [{"mission_id": "M-0007", "type": "PATIENT_TRANSPORT", "level": 2, "robot": "robot_a",
               "status": "ACTIVE", "stops": ["A", "C"], "stop_index": 0}],
 "last_decision": {"type": "RESOURCE_GRANT", "ts": 1794963590.0, "robot": "robot_b", "detail": {"...": "4.8절"}},
 "open_cards": ["K-0e3a…"],
 "bed_exit": {"value": false, "since": 1794963000.0,
               "monitor": {"state": "OK", "last_rx": 1794963604.1, "rate_hz": 29.8, "since": 1794963000.0, "bypass": false}},
 "kpi": {"hold_count": 3, "deadlock_count": 1, "deadlock_resolve_s_avg": 14.2, "pause_count": 0}}
```

화면이 쓰는 필드:

- `robots.<id>.route`: 지금 구간의 남은 계획 경로 `[[x, y], ...]` (map 좌표). 지금 위치 → 지나갈 입구 중심점(`mouths`) → 목적지 구역. 구간이 없으면 `[]`.
- `robots.<id>.state_since`: 지금 상태가 된 시각. "진입 전 대기 · 00:18" 같은 경과 시간에 쓴다.
- `robots.<id>.obstacle`: 이번 멈춤에서 받은 장애물 분류. 멈춤이 없으면 `null`. `{"kind": "PERSON"|"OBJECT"|"NONE"|"UNKNOWN", "labels": ["person"], "detections": [{"label", "confidence", "pose"}], "at": …}`
- `resources.<id>.reason`·`decided_at`: 그 자원에 대한 가장 최근 판단 문장과 시각 (허가·대기·양보·점유 불명·해제). `resource_manager`가 만든 문장을 그대로 넣는다.
- `resources.<id>.queue_detail`: `[{"robot": "robot_a", "level": 1, "mission_id": "M-0005", "since": …}]`. `queue`(이름 목록)도 그대로 둔다.
- `missions[]`: 위 필드에 `patient`, `items`(정류장별 품목), `requested_by`, `created_at`, `finished_at`, `wait_reason`이 더 있다. 끝난 임무도 최근 20건까지 넣는다 (`status`로 구분).
- `last_decision`: 가장 최근 통행 판단 하나. 4.8절 판단 이벤트의 `type`, `ts`, `robot`, `detail`을 그대로 담는다. 없으면 `null`.

### 4.6 `/fleet/decision_req`

```json
{"v": 1, "card_id": "K-0e3a…", "kind": "DEADLOCK", "robots": ["robot_a", "robot_b"],
 "title": "교착: 서로 상대 방으로 가려고 함",
 "reason": "Robot A는 B방에서 E방으로, Robot B는 E방에서 B방으로 가려 함. 서로 상대가 쥔 방을 기다림",
 "options": [{"id": "yield_a", "label": "A가 D 구역으로 비킴"}, {"id": "yield_b", "label": "B가 F 구역으로 비킴"}],
 "recommended": "yield_a", "timeout_s": 20, "default": "yield_a", "created_at": 1794963604.2}
```

- 카드 ID는 `K-` 뒤에 UUID다.
- `default`가 `null`인 카드는 시간이 지나도 저절로 실행하지 않는다.
- 카드가 닫히면(선택·시간 초과·상황 해소) `/mission/event`에 `CARD_CLOSED`를 보낸다.

### 4.7 `/robot/pause`, `/robot/resume` (Trigger)

- ROS 명령줄용이다. 화면 버튼은 4.2절 `PAUSE_ALL`·`RESUME_ALL`을 쓴다 (`Trigger`는 인자가 없어 누른 사람을 남길 수 없다). 이 경로의 `by`는 "ROS 서비스"다.
- `pause`: 항상 `success:true`로 바로 답하고, 실제 정지는 다음 tick에 한다.
- `resume`: 침상 이탈이 아직 `true`이거나 침상 감시가 끊겼으면 `success:false`와 이유 문장. 아니면 `success:true`로 답하고 다음 tick에 재개한다.

### 4.8 `/mission/event`

```json
{"v": 1, "ts": 1794963604.2, "type": "RESOURCE_GRANT", "robot": "robot_b", "mission_id": "M-0006",
 "detail": {"resources": ["CORRIDOR", "ROOM_E"], "reason": "복도 비어 있음 + Level 3 > Level 2 → Robot B 허가"}}
```

이벤트 종류: `MISSION_CREATED`, `MISSION_REJECTED`, `LEG_START`, `ARRIVE`, `CONFIRM_STEP`, `MISSION_DONE`, `MISSION_CANCELED`, `RESOURCE_REQUEST`, `RESOURCE_GRANT`, `RESOURCE_HOLD`, `RESOURCE_ENTER`, `RESOURCE_RELEASE`, `RESOURCE_UNKNOWN`, `RESOURCE_CLEAR_CONFIRM`, `DEADLOCK_DETECTED`, `YIELD_START`, `YIELD_DONE`, `CORRIDOR_YIELD_START`, `CORRIDOR_YIELD_END`, `CORRIDOR_YIELD_TIMEOUT`, `STALL_SHORT`, `STALL_LONG`, `BLOCKED`, `UNBLOCKED`, `PAUSE`, `RESUME`, `RESUME_REJECTED`, `BED_EXIT`, `BED_EXIT_CLEAR`, `BUMP`, `MOVE_REJECTED`, `NAV_FAILED`, `FAULT`, `CARD_OPEN`, `CARD_CLOSED`, `LINK_LOST`, `LINK_BACK`, `WARNING`, `BED_MONITOR_WARN`, `BED_MONITOR_LOST`, `BED_MONITOR_BACK`, `BED_MONITOR_BYPASS`, `OBSTACLE_INSPECT`, `OBSTACLE_RESULT`.

`detail.reason` 규칙:

- `reason`은 화면에 그대로 보이는 한국어 문장이다. `OK`·`MISMATCH`·`DONE`·`selected`·`timeout`·`resolved` 같은 코드 값은 `reason`에 넣지 않고 `detail.result`·`detail.choice`·`detail.closed_by` 같은 별도 칸에 둔다.
- 문장 속 로봇은 표시 이름(`Robot A`)으로 쓴다. `robot_a` 같은 ID를 쓰지 않는다. 로봇 이름 뒤에 조사를 붙이지 않는다. 예: "Robot A — A 구역으로 가는 구간 요청", "복도 비어 있음 + 실효 Level 1, 먼저 요청 → Robot A 허가".
- 자원은 "복도", "{구역 이름} 출입"으로 쓴다. "통로"·"공용도로"는 쓰지 않는다.
- 이 규칙은 `/ui/ack`의 `message`, 카드 제목·이유, `wait_reason`에도 똑같이 쓴다.

통행 판단 이벤트(`RESOURCE_GRANT`, `RESOURCE_HOLD`, `CORRIDOR_YIELD_START`)의 `detail`에는 판단 근거를 같이 넣는다:

```json
{"resources": ["CORRIDOR"], "reason": "복도 비어 있음 + Level 3 > Level 1 → Robot B 허가",
 "rule": "LEVEL", "state_before": "FREE",
 "contenders": [{"robot": "robot_b", "level": 3, "mission_id": "M-0006", "type": "MED_ROUND", "requested_at": 1794963590.0},
                {"robot": "robot_a", "level": 1, "mission_id": "M-0005", "type": "PATIENT_TRANSPORT", "requested_at": 1794963588.2}]}
```

- `rule`: 순서를 정한 기준. `ONLY`(경쟁 없음), `LEVEL`, `REQUEST_TIME`, `SUBMIT_ORDER`, `ENTRY_ORDER`(복도 양보: 나중에 들어온 쪽), `ROBOT_ID`(마지막 동점 처리), `CAPACITY`(자리 없음), `NO_PREEMPT`(이미 허가된 로봇 보호), `BLOCKED`, `UNKNOWN`.
- `state_before`: 판단 직전 그 자원의 상태.
- 문장(`reason`)은 `resource_manager`가 만든다. 화면은 문장을 고치지 않는다.

---

## 5. 판단 규칙

### 5.0 tick 순서 (10 Hz)

1. 입력 큐 비우기: 화면 요청, 서비스 요청, 로봇 상태, bed_exit, 장애물 분류 결과를 반영한다
2. `safety`: 침상 이탈·침상 감시 끊김·수동 정지·범퍼 → 대상 로봇 정지
3. `stall`: 진행 없음 시간 갱신 → 장애물 촬영 요청 → 잠깐 막힘 기록 / 오래 막힘이면 막힘 표시 + 카드
4. `mission`: 로봇별 상태머신 진행 (배정, 도착, 확인, 다음 구간, 복귀)
5. `route_planner`: 다음 구간에 필요한 자원 계산
6. `resource_manager`: 자원 진입·해제 판정 → 대기 요청 중 허가 → 복도 양보 판정 (5.3절 11번)
7. `deadlock`: 대기 그래프에 순환이 있으면 비킬 로봇을 정함
8. `decisions`: 카드 시간 초과 처리
9. `robot_link`: 이번 tick에 정한 명령 보내기
10. `store`: 이벤트·지표 기록, `/fleet/state`는 5 Hz로 발행

모든 모듈은 `now`를 인자로 받는다 (테스트에서 가짜 시계를 쓰기 위해서). 모듈 안에서 `time.time()`을 직접 부르지 않는다.

### 5.1 `mission` — 임무와 로봇 상태머신

관제 쪽 로봇 상태:

| 상태 | 뜻 |
|---|---|
| `IDLE` | 임무 없음 |
| `RETURNING` | 임무를 마치고 홈 구역으로 가는 중. 새 임무를 받을 수 있다 |
| `HOLD` | 다음 구간 자원을 기다림 (`wait_reason` 표시) |
| `SENDING` | `MOVE`를 보냈고 `MISSION_ACCEPTED`를 기다림 |
| `MOVING` | 주행 중 |
| `WAIT_CONFIRM` | 정류장 도착, 화면 확인 대기 |
| `YIELDING` | 교착 해소를 위해 비키는 중 |
| `GIVE_WAY` | 복도에서 마주 오는 우선 로봇을 위해 잠깐 멈춤 (5.3절 11번) |
| `SAFE_PAUSE` | 침상 이탈·침상 감시 끊김·수동 정지·범퍼로 멈춤 |
| `FAULT` | 실패. 카드로 사람 판단 대기 |
| `OFFLINE` | STATE가 `link_timeout_s` 넘게 안 옴 |

- **두 로봇은 각자 따로 움직인다.** 로봇마다 상태머신이 따로 있고, 매 tick 두 로봇을 모두 판단한다. 한 로봇이 주행·대기·확인 중이어도 다른 로봇은 자기 임무를 계속한다. 서로 영향을 주는 것은 자원(복도, B·E 방)과 복도 양보뿐이다.
- **배정** (`assignment.mode`)
  - `IDLE_BEST_BATTERY` (기본): 임무는 공용 배정 대기열에 들어간다. 매 tick 대기열 맨 앞 임무(level 높은 순 → 먼저 온 순)를 배정 가능한 로봇에 준다.
    - 배정 가능: 상태가 `IDLE` 또는 `RETURNING`, 연결 살아 있음, `SAFE_PAUSE`·`FAULT` 아님, 배터리가 `min_battery_pct`(20%)보다 많거나 배터리를 모름.
    - 고르는 순서: 배터리 많은 로봇 → 배터리를 모르는 로봇은 뒤로 → 같으면 첫 정류장에 가까운 로봇 → 그래도 같으면 `robot_a`.
    - 배정 가능한 로봇이 없으면 대기열에 그대로 둔다 (`wait_reason`: "쉬는 로봇 없음").
  - `ROLE`: 임무 종류로 로봇을 정한다 (`type_to_robot`). 그 로봇이 바쁘면 그 로봇 대기열에서 기다린다. 다른 로봇이 놀고 있어도 대신 가지 않는다.
- **구간(leg)**: 지금 구역 → 다음 정류장. 구간마다 `route_planner`로 필요한 자원을 구하고, 자원이 필요 없으면 바로 `MOVE`, 필요하면 `HOLD`에서 요청 → 허가 → `MOVE`.
- **보내기**: `MOVE <구역> <level>` → `SENDING`. `accept_timeout_s`(3초) 안에 `MISSION_ACCEPTED`가 오면 `MOVING`. 안 오면 한 번 다시 보낸다. 그래도 안 오면 `MOVE_REJECTED` 이벤트 + `FAULT` + 카드.
  - 로봇 쪽 상태가 `PAUSED`면 먼저 `CANCEL`을 보내고 `CANCELED`를 확인한 뒤 `MOVE`를 보낸다.
- **도착**: `ARRIVED` 이벤트(목적지 일치) → `WAIT_CONFIRM`. 화면이 `CONFIRM_STEP OK`를 보내면 다음 구간. 마지막이면 `MISSION_DONE` → 복귀.
  - `MISMATCH`면 그 자리에 둔 채 카드 `STOP_MISMATCH`: [다시 확인] [이 정류장 건너뛰기] [임무 취소], `default: null`.
- **복귀** (`mission.return_home: true`): 마지막 확인이 끝나면 로봇을 홈 구역(`robots.<id>.start_zone`)으로 `MOVE` → `RETURNING` → 도착하면 `IDLE`.
  - 복귀 이동도 자원 규칙(B·E 방, 복도 양보)을 똑같이 따른다. 복귀의 자원 요청 응급도는 `mission.return_level`(1)이다. 끝낸 임무의 응급도를 물려받지 않는다.
  - 복귀 중인 로봇에 새 임무가 배정되면 `CANCEL`을 보내고 (`mission.return_interruptible: true`), 아래 셋 중 먼저 오는 것을 보고 바로 새 임무 `MOVE`를 보낸다.
    - `MISSION_CANCELED`
    - 복귀 구간의 `ARRIVED`, 또는 STATE의 `state`가 `IDLE`·`COMPLETE`·`FAILED`·`CANCELED` 중 하나. 로봇이 `CANCEL`을 받기 전에 홈에 도착하면 PART 04는 그 `CANCEL`을 조용히 버리므로, 취소 확인을 기다리면 안 된다.
    - R1 반영 후의 `COMMAND_REJECTED`(CANCEL 거절)
    - 3초(`accept_timeout_s`) 안에 셋 다 없을 때만 `NAV_FAILED` 카드.
  - 도킹(충전대에 붙기)은 하지 않는다 (`return_to_dock: false`). PART 04 도킹 명령이 생기면 켠다.
- **운영자 이동(`GO_ZONE`)**: 응급도가 없으므로 `mission.manual_level`(1)로 보낸다. 도착하면 그 구역에서 기다린다 (홈으로 복귀하지 않는다). 임무가 아니라서 `missions`에 쓰지 않는다.
- **실패**: `MISSION_FAILED`(예: `Nav2: ABORTED`) → `FAULT` + 카드 `NAV_FAILED`: [다시 시도] [이전 구역으로 되돌아가기] [임무 취소], `default: null`.
  - [되돌아가기]로 이전 구역에 도착하면 `FAULT`로 기다린다. 원래 구간은 운영자의 `RETRY`로 다시 시작한다 (`mission.retry_after_return: false`).
- **요청하지 않은 `MISSION_CANCELED`**: `WARNING` 이벤트 + `FAULT`.
- **임무 취소(`CANCEL_MISSION`)**
  - 대기열 임무나 아직 자원에 안 들어간 구간은 바로 취소한다. 허가만 받은 상태면 허가를 반납한다.
  - 로봇이 `CORRIDOR`를 점유한 채 복도 안(+여유)에 있으면 **거절**한다 (`E-423`, "Robot A 복도 통과 중 — 통과 후 취소하세요"). 복도 한가운데에 로봇을 세워 두고 잠금을 영영 쥐게 만들지 않기 위해서다.
  - 예외: `FAULT`인 로봇은 취소를 허용하고 잠금은 유지한다. 사람이 로봇을 꺼내 위치가 복도 밖으로 나가면 일반 해제 규칙으로 풀린다.
  - 취소된 임무의 로봇은 홈으로 복귀한다.

### 5.2 맵·구역·자원 (`route_planner`)

`main_map/main_maps.pgm` 분석 결과다.

- 맵이 map 좌표축에 대해 약 4°(화면용 자동 보정값 4.1°) 기울어 있다. 영역 판정은 축 정렬 사각형이 아니라 **기울어진 다각형**(점-다각형 포함)으로 한다. 다각형 판정과 여유 거리(`clear_margin_m`)는 shapely로 한다 (`Polygon.contains`, `buffer`).
- 영역 사이 연결(TOP·BOTTOM·ROOM_B·ROOM_E가 CORRIDOR로 이어짐)은 networkx 그래프로 두고, 구간에 필요한 자원은 그래프 경로로 구한다. 아래 표는 그 결과를 확인하는 테스트 기준이다.
- 위쪽 홀(A·D)과 아래쪽 홀(C·F)은 각각 칸막이 없는 한 공간이다.
- B·E 방은 복도 옆 문으로만 드나드는 막다른 방이다. 같은 홀 안 이동(A↔D, C↔F) 말고는 전부 복도를 지난다.
- 복도 폭은 벽에서 벽까지 약 0.9 m다. 11/18 실제 시험에서 두 로봇이 마주쳐도 Nav2가 서로 비켜 지나갔다 (B·E 문 앞에서 한 대가 문 쪽으로 비켜섬). 그래서 복도는 **두 대가 같이 쓰는 것이 기본**이다 (`corridor.mode: SHARED_YIELD`). 비켜 지나가는 것은 Nav2가 하고, 마주 올 때 **누가 잠깐 설지는 관제가 우선순위로 정한다** (5.3절 11번). 한 대씩만 쓰는 `EXCLUSIVE`는 설정으로 남긴다.
- B·E 문이 복도 한가운데에 있어서 모든 복도 이동이 가운데를 지난다. 복도는 둘로 쪼개지 않고 자원 하나(`CORRIDOR`)로 둔다.
- 자원마다 동시에 쓸 수 있는 대수(`capacity`)가 있다. `CORRIDOR`는 `SHARED_YIELD`면 2, `EXCLUSIVE`면 1이다. `ROOM_B`·`ROOM_E`는 막다른 방이라 항상 1이다.

영역 판정 (복도 다각형 네 변을 연장한 직선으로 나눈다):

1. 복도 다각형 안 (경계 포함, `corridor.include_boundary`) → `CORRIDOR`
2. 윗변(TL–TR) 연장선보다 위 → `TOP`
3. 아랫변(BL–BR) 연장선보다 아래 → `BOTTOM`
4. 그 사이에서 왼변보다 왼쪽 → `ROOM_B`, 오른변보다 오른쪽 → `ROOM_E`

구역 goal 좌표는 PART 04 `goal_manager.py`와 같은 값이다 (PART 04가 RViz로 고치면 같이 고친다. preflight가 둘을 비교한다):

| 구역 | x | y | 영역 |
|---|---|---|---|
| A | -2.20 | 0.50 | TOP |
| B | -2.55 | -2.20 | ROOM_B |
| C | -2.30 | -5.00 | BOTTOM |
| D | -0.60 | 0.40 | TOP |
| E | -0.55 | -2.40 | ROOM_E |
| F | -0.80 | -5.10 | BOTTOM |

**자원 3개**: `CORRIDOR`, `ROOM_B`, `ROOM_E` (홀 TOP·BOTTOM은 여러 대가 있어도 되므로 자원이 아니다)

구간에 필요한 자원 = 출발 영역과 도착 영역 사이에 지나는 것 전부:

| 출발 → 도착 | 필요한 자원 |
|---|---|
| 같은 홀 안 (A↔D, C↔F) | 없음 |
| TOP ↔ BOTTOM | `CORRIDOR` |
| 홀 ↔ B방 | `CORRIDOR`, `ROOM_B` |
| 홀 ↔ E방 | `CORRIDOR`, `ROOM_E` |
| B방 ↔ E방 | `CORRIDOR`, `ROOM_B`, `ROOM_E` |

- 출발 영역은 지금 위치로 판정한다. 로봇이 `CORRIDOR` 안에서 새 구간을 시작하면(예: 재시도) 이미 쥔 `CORRIDOR`를 그대로 쓴다.

### 5.3 `resource_manager` — 예약과 허가

자원마다 쥔 로봇 목록(`holders`, 최대 `capacity`대)과 로봇별 상태(`GRANTED` 허가만 받음 / `OCCUPIED` 들어감)를 둔다. 자원 상태 표시는 쥔 로봇이 없으면 `FREE`, 하나라도 들어가 있으면 `OCCUPIED`, 허가만 있으면 `GRANTED`, 점유 불명이면 `UNKNOWN`이다. 별도 플래그 `blocked`(5.5절).

1. **요청**: `HOLD`에 들어갈 때 필요한 자원 집합으로 요청한다.
2. **한꺼번에 허가 (all-or-nothing)**: 필요한 자원이 **전부** 자리가 남아 있을 때만(쥔 로봇 수 < `capacity`) 전부 허가한다. 일부만 주지 않는다. 대기 중에는 아무것도 쥐지 않으므로 복도를 사이에 둔 교착이 생기지 않는다.
   - 단, 로봇이 **지금 서 있는 방**(`ROOM_B`·`ROOM_E`)은 몸이 거기 있으니 계속 쥐고 있다. 이것 때문에 교착이 생길 수 있다 (5.4절).
3. **순서**: 대기 요청 중 level이 높은 로봇이 먼저, 같으면 먼저 요청한 로봇이 먼저, 요청 시각까지 같으면 접수한 순서. `aging_s`(기본 0 = 끔)를 켜면 그 시간마다 대기 로봇의 실효 level을 1씩 올린다.
   - 복도가 빈 직후나 첫 요청 뒤에는 `decision_window_s`(1초) 동안 경쟁 요청을 모은 뒤 정한다.
4. **선점 금지**: 이미 준 허가는 level이 더 높은 요청이 와도 회수하지 않는다.
5. **진입**: 허가받은 로봇 위치가 자원 다각형 안에 들어오면 `OCCUPIED`.
6. **해제**: 아래 둘 다 맞으면 `FREE`로 바꾸고 대기 요청을 다시 평가한다.
   - 점유 로봇이 자원 다각형을 `clear_margin_m`(0.20 m)만큼 넓힌 범위 밖에 `clear_hold_s`(1초) 동안 연속으로 있다
   - 그 로봇의 연결이 살아 있다 (`link_ok`)
   - 허가만 받고 들어가지 않은 채 구간이 끝나거나 취소되면 바로 반납한다.
7. **점유 불명**: 점유·허가 로봇이 `OFFLINE`이 되면 `UNKNOWN`. 자원 안에 있는 로봇 수가 `capacity`를 넘거나, 자리가 다 찬 자원에 허가 없는 로봇이 들어와 있어도 `UNKNOWN`이다 (`SHARED_YIELD`에서 복도 안 두 대는 정상). 새 허가를 내지 않고 경고(`E-423`)를 띄운다. 운영자의 `RESOURCE_CLEAR_CONFIRM`으로만 `FREE`가 된다. 누가 눌렀는지 기록한다.
   - 공유 자원의 `UNKNOWN`을 운영자가 풀 때 다른 로봇의 확인된 허가·점유는 그대로 둔다. 지금 보고가 여전히 `capacity`를 넘으면 `E-423`을 유지한다.
8. **시작 시**: 이미 자원 안에 있는 로봇은 그 자원의 점유자로 본다.
9. **멈춘 로봇**: `SAFE_PAUSE` 로봇은 허가 후보에서 뺀다 (요청은 대기열에 남긴다). 이미 쥔 자원은 그대로 유지한다.
10. **기록**: 모든 결정에 사유 문장과 후보 목록을 붙여 이벤트로 남긴다 (4.8절).
    - 예: "복도 비어 있음 + Level 3 > Level 1 → Robot B 허가"
    - 예: "Robot A 복도 점유 중 → Robot B 대기 (선점 금지)"
11. **복도 양보 (`SHARED_YIELD` 모드)**: 복도에서 두 로봇이 마주 오면 우선순위가 낮은 쪽을 잠깐 세운다. 서 있는 로봇은 Nav2가 피해 가기도 쉽다.
    - **마주 옴 판정**: 아래가 모두 맞을 때
      - 두 로봇 모두 `CORRIDOR`를 쥐고 있다 (허가 또는 점유)
      - 이번 구간의 복도 방향이 서로 반대다. 목적지 영역이 TOP이면 위쪽, BOTTOM이면 아래쪽, B·E 방이면 지금 위치에서 그 방 문(`mouths`) 쪽이다. B방↔E방 가로지르기는 방향이 없으므로 판정에서 뺀다
      - 서로를 향해 오고 있다. 로봇 위치를 복도 중심선(TOP 입구 → BOTTOM 입구)에 투영했을 때 위로 가는 로봇이 아래로 가는 로봇보다 아래에 있다
      - 두 로봇 거리가 `yield_trigger_m`(1.2 m) 이하다
    - **설 로봇**: level 낮은 쪽 → 같으면 복도에 나중에 들어온 쪽 → 같으면 `robot_b`
      - "나중에 들어온 쪽"의 진입 시각은 위치가 처음 `OCCUPIED`로 관측된 tick이다. 같은 level에서 한 대만 들어와 있으면 아직 안 들어온 로봇이 선다.
    - **동작**: 설 로봇에 `PAUSE`를 보내고 관제 상태를 `GIVE_WAY`로 바꾼다. 사유는 "Robot B 복도 양보 중 (Robot A Level 3)" 형식이다. 이벤트 `CORRIDOR_YIELD_START`.
    - **해제**: 우선 로봇이 지나갔으면(중심선 위 순서가 바뀌고 거리가 `yield_release_m` 0.5 m 이상) 또는 복도를 나갔으면 `RESUME`을 보내고 원래 상태로 돌아간다. 이벤트 `CORRIDOR_YIELD_END`.
    - **안전장치**: 양보를 시작한 뒤 `yield_timeout_s`(10초) 동안 우선 로봇이 진행하지 못하면 양보를 풀고(`RESUME`) 둘 다 Nav2에 맡긴다. 이벤트 `CORRIDOR_YIELD_TIMEOUT`. 그 뒤로는 5.5절 멈춤 규칙을 적용한다.
      - 우선 로봇이 중심선 위에서 이전 최장 위치를 넘어 나아가면 10초 타이머를 다시 센다. 시간 초과 뒤에는 같은 두 구간 사이에서 `PAUSE`를 다시 내지 않는다.
    - 같은 방향으로 가는 두 로봇은 건드리지 않는다 (뒤따라가기).
    - `SAFE_PAUSE`가 양보보다 우선이다. 안전 정지 중에는 양보 쪽에서 `RESUME`을 내지 않는다. 재개하면 양보가 필요한지 다시 판단한다.
    - PART 04가 나중에 로봇 쪽에서 우선순위 양보를 직접 하게 되면 `corridor.yield_enabled: false`로 끈다.
    - 거리값은 시험 영상을 보고 정한 시작값이다. 리허설에서 조정한다.

### 5.4 `deadlock` — 교착 감지와 해소

- **대기 그래프**: 로봇 X가 기다리는 자원을 로봇 Y가 쥐고 있으면 X → Y. 매 tick networkx `DiGraph`로 만들고 `find_cycle`로 순환을 찾는다.
  - 간선은 **자리가 다 찬 자원**의 보유 로봇들로만 잇는다. `SHARED_YIELD`에서 복도에 한 대만 있으면 복도를 기다리는 간선은 생기지 않는다.
- **생기는 경우**: 자원을 한꺼번에 허가해도, 로봇이 서 있는 방은 계속 쥐고 있어서 생긴다.
  - 예: Robot A가 B방에 있고 E방으로 가야 함, Robot B가 E방에 있고 B방으로 가야 함 → A는 `ROOM_E`(B가 쥠)를, B는 `ROOM_B`(A가 쥠)를 기다린다 → 순환.
- **비킬 로봇 정하기**: ① level이 낮은 쪽 ② 같으면 나중에 요청한 쪽 ③ 같으면 환자를 태우지 않은 쪽(`PATIENT_TRANSPORT`의 픽업 전) ④ 그래도 같으면 `robot_b`.
- **해소**: 순환을 처음 본 뒤 `deadlock.confirm_s`(2초) 동안 계속되면 `DEADLOCK_DETECTED`. 카드 `DEADLOCK`을 띄운다 (추천 = 위 규칙, `timeout_s: 20`, `default` = 추천). 시간 안에 사람이 고르면 그대로, 아니면 추천대로 한다.
  - 비키는 로봇은 `YIELDING`이 되고, 가장 가까운 홀 구역(`yield_zones`: `ROOM_B → D`, `ROOM_E → F`)으로 보낸다. 이 이동도 자원 규칙을 똑같이 따른다 (보통 `CORRIDOR`만 필요하고 비어 있다).
  - 비키는 로봇이 방을 나가면 `ROOM_x`가 풀리고, 기다리던 로봇이 허가를 받는다.
  - 비킨 로봇은 도착하면(`YIELD_DONE`) 원래 구간을 다시 요청한다.
- 2대뿐이라 순환 길이는 2지만, 코드는 N대에서도 돌게 일반 그래프로 짠다.
- 지표: 교착 횟수, 해소 시간. 해소 시간은 교착 확정부터 비킨 로봇이 원래 방 점유를 실제로 해제한 tick까지다.

### 5.5 `stall` — 멈춤 판단 (시간 + PART 06 장애물 분류)

- **진행 없음**: 로봇이 `MOVING`인데 `stall.window_s`(5초) 동안 위치 변화가 `stall.move_m`(0.05 m)보다 작고 PART 04 `percent`도 늘지 않으면 멈춘 것으로 본다. 멈춘 시간 `stall_s`를 센다.
- `stall_s ≥ short_s`(10초): `STALL_SHORT` 기록, 화면에 대기 시간 표시. 동작은 안 바꾼다 (지나가는 사람은 보통 비켜 준다. Nav2가 스스로 돌아가는 길도 찾는다).
- `stall_s ≥ long_s`(30초): `STALL_LONG` + 로봇이 있는 영역(복도면 `CORRIDOR`)에 `blocked=true` 표시 → **다른 로봇의 새 허가에서 그 자원을 뺀다** (막힘 정보 공유). 카드 `BLOCKED`, `default: null` (무엇이 막고 있는지 모르므로 저절로 움직이지 않는다).
  - [다시 출발] (`retry`): 로봇 쪽 상태에 맞게 `RESUME` 또는 `MOVE` 재전송, `blocked` 해제. 버튼 글은 장애물 분류에 맞춘다: `PERSON` → [비켜 줌, 다시 출발], `OBJECT` → [치웠음, 다시 출발], `NONE`·`UNKNOWN`·분류 없음 → [확인함, 다시 출발].
  - [되돌아가기] (`return`): `CANCEL` → 이전 구역으로 `MOVE`
  - [임무 보류] (`hold`): `PAUSE`, 임무는 `FAULT`로
- 다시 움직이면 `stall_s = 0`, 카드가 열려 있으면 "상황 해소"로 닫고 `blocked`를 해제한다 (`UNBLOCKED`).
- 진행 없음 중에 Nav2가 포기하면 `MISSION_FAILED`가 오고, 5.1절 실패 규칙이 우선한다.
- **장애물 분류 (3.2a절)**
  - `stall_s ≥ obstacle.inspect_at_s`(10초)가 되면 그 로봇에 `inspect_image`를 한 번 보낸다. `OBSTACLE_INSPECT` 이벤트.
  - 결과를 종류로 나눈다. 신뢰도 `min_confidence`(0.4) 미만 탐지는 종류 판단에서 뺀다 (기록에는 남긴다).
    - `PERSON`: `person`이 하나라도 있음.
    - `OBJECT`: `person`은 없고 다른 물체가 있음.
    - `NONE`: `status: ok`, `obstacle_present: false`.
    - `UNKNOWN`: `ok`가 아님, 시간 초과, 깨진 JSON, 또는 장애물이 있다는데 쓸 만한 탐지가 하나도 없음.
  - `OBSTACLE_RESULT` 이벤트에 종류·라벨·신뢰도·위치를 남기고, `robots.<id>.obstacle`에 넣는다.
  - 종류별 동작
    - `PERSON`: 30초까지 기다린다. 사람은 보통 비켜 준다. 30초 카드의 이유에 "사람 감지 (로봇 앞 약 1.2 m)"를 붙인다.
    - `OBJECT`: 물건은 저절로 치워지지 않는다. **결과를 받는 즉시** 30초 막힘과 같은 처리를 한다 (`object_blocks_now`): `STALL_LONG`, `blocked=true`, 카드 `BLOCKED`. 카드 이유는 "물건이 길을 막고 있음: 의자 (로봇 앞 약 0.8 m)".
    - `NONE`: 30초 규칙 그대로. 카드 이유에 "카메라에는 장애물이 보이지 않음 — 주행 문제일 수 있음".
    - `UNKNOWN`: 30초 규칙 그대로. 카드 이유에 "장애물 분류 불가".
  - 분류 결과로 로봇을 저절로 움직이지 않는다. 멈추고 알리는 것만 앞당긴다.
  - 거리는 `pose`가 있는 탐지와 로봇 위치 사이의 거리다. `pose`가 없으면 거리를 쓰지 않는다.
  - 한 번 멈춤에 한 번만 찍는다. 다시 움직였다가 또 멈추면 새로 찍는다. [다시 출발] 뒤에 또 멈춰도 새로 찍는다.
  - 라벨은 화면과 문장에서 한국어로 쓴다 (`fleet.yaml obstacle.label_ko`. 표에 없으면 영어 그대로).

### 5.6 `safety` — 침상 이탈과 수동 정지

| 상황 | 감지 | 동작 | 복구 |
|---|---|---|---|
| 침상 이탈 | `/patient/bed_exit` false → true (상승 에지) | `bed_exit_pause_targets` 로봇 중 임무 중인 로봇을 `SAFE_PAUSE`. 주행 중이면 `PAUSE` 전송. 경고 `BED_EXIT` | 재개 (bed_exit가 false일 때만) |
| 수동 정지 | `PAUSE_ALL`, `/robot/pause`, `PAUSE_ROBOT` | 대상 로봇 `SAFE_PAUSE`, 주행 중이면 `PAUSE` | `RESUME_ALL`, `/robot/resume`, `RESUME_ROBOT` |
| 범퍼 충돌 | `hazard_detection`에 `BUMP`가 새로 나타남 | 그 로봇 `SAFE_PAUSE`, 주행 중이면 `PAUSE`. 경고 `BUMP` | 사람이 확인 후 `RESUME_ROBOT` |
| 침상 감시 끊김 | `/patient/bed_exit`가 `bed_monitor.warn_s`(1초) 넘게 안 옴 → 경고만. `lost_s`(3초) 넘게 안 옴 → 끊김 | 경고: `BED_MONITOR_WARN`. 끊김: `bed_exit_pause_targets` 로봇 중 임무 중인 로봇을 `SAFE_PAUSE`, 주행 중이면 `PAUSE`. `BED_MONITOR_LOST` + 카드 `BED_MONITOR_LOST` | 신호가 `recover_s`(2초) 이어서 들어오면 `BED_MONITOR_BACK`. 그 뒤 사람이 재개. 또는 카드에서 [감시 없이 운행] |

- 재개는 `SAFE_PAUSE` 직전 상태로 되돌린다. 로봇 쪽이 `PAUSED`면 `RESUME`을 보낸다 (PART 04가 같은 목적지로 다시 출발). `HOLD`·`WAIT_CONFIRM`이었으면 관제 상태만 되돌린다.
- 멈춰 있는 동안에도 쥔 자원은 그대로 유지한다.
- 침상 이탈 중에 들어온 임무는 `SAFE_PAUSE`로 시작한다.
- 환자 안전 판단은 시간이 지나도 저절로 재개하지 않는다.
- 침상 감시
  - 감시 상태 `bed_exit.monitor.state`: `NEVER`(켜진 뒤 한 번도 안 옴) → `OK` → `WARN`(1초 끊김) → `LOST`(3초 끊김) → 신호가 2초 이어지면 `OK`.
  - `LOST`인 동안 대상 로봇은 침상 이탈과 똑같이 다룬다: 새로 시작하는 임무도 `SAFE_PAUSE`로 시작하고, 재개는 거절한다 ("침상 감시가 끊겨 재개할 수 없습니다. 웹캠을 확인하세요").
  - 신호가 돌아와도 저절로 재개하지 않는다. 돌아온 값이 `true`면 침상 이탈 규칙으로 넘어간다.
  - `NEVER`: `bed_monitor.require_at_start: true`면 `LOST`와 같게 다룬다 (실제 시연. 감시 없이 출발하지 않는다). `false`면 경고만 한다 (mock 시험, `fleet_mock.launch.py`가 false로 띄운다).
  - 받은 횟수로 수신 속도(`rate_hz`, 최근 3초)를 계산한다. `min_rate_hz`(10 Hz)보다 낮으면 `BED_MONITOR_WARN`("침상 감시 불안정")만 낸다.
  - [감시 없이 운행]: 카메라를 바로 고칠 수 없을 때 사람이 고른다. `bypass: true`가 되고 재개가 허용된다. 누가 골랐는지 `BED_MONITOR_BYPASS`에 남긴다. 신호가 돌아오면 `bypass`는 저절로 풀린다. 화면은 `bypass` 동안 "침상 감시 없이 운행 중" 띠를 계속 보여 준다.

### 5.7 `decisions` — 결정 카드

| kind | 언제 | 선택지 (ID) | 기본값 |
|---|---|---|---|
| `DEADLOCK` | 교착 확정 | 어느 로봇이 비킬지 (`yield_a`·`yield_b`) | 추천안, 20초 |
| `BLOCKED` | 30초 이상 막힘, 또는 물건 감지 | 다시 출발·되돌아가기·보류 (`retry`·`return`·`hold`) | 없음 |
| `NAV_FAILED` | 주행 실패 | 다시 시도·되돌아가기·취소 (`retry`·`return`·`cancel`) | 없음 |
| `MOVE_REJECTED` | 로봇이 명령을 안 받음 | 다시 보내기·취소 (`retry`·`cancel`) | 없음 |
| `STOP_MISMATCH` | 정류장 [불일치] | 다시 확인·건너뛰기·취소 (`recheck`·`skip`·`cancel`) | 없음 |
| `BED_MONITOR_LOST` | 침상 감시 3초 끊김 | 계속 정지·감시 없이 운행 (`hold`·`bypass`) | 없음. 신호가 돌아오면 `resolved`로 닫힌다 |

- 각 선택지에 한국어 표시명(`label`)을 같이 담는다.
- 카드는 근거(`reason`)를 반드시 한 문장으로 붙인다. 카드 하나는 한 번만 실행한다 (같은 `card_id` 두 번 오면 두 번째는 `E-409`).
- 상황이 먼저 풀리면 카드를 닫는다 (`CARD_CLOSED`, 닫힌 이유 `resolved`).

### 5.8 `store` — DB

- 테이블 정의, 칸, 상태 값, 어느 로직에서 무엇을 쓰는지는 `WEB_DB_DESIGN.md` 4장이 정본이다.
- `bad.db` (`db.path`): SQLite, WAL. **관제만 쓴다.** 화면 서버는 지난 기록을 보려고 읽기 전용으로 연다.
  - 테이블 5개: `patients`, `missions`, `mission_stops`, `events`, `cards`
  - 실제 연결은 `~/.bad/bad_real.db`, 시연은 `~/.bad/bad.db`를 쓴다 (실행 파일 기본값).
- 쓰는 쪽을 관제 하나로 둔다. 두 프로그램이 같은 표를 동시에 고쳐 상태가 엇갈리는 일을 막기 위해서다.
- 로그인·계정 테이블은 없다. 누가 눌렀는지는 화면의 조작자 이름 칸 값으로 남긴다.
- 지표 조회 함수: 임무별 출발·도착 시각, 자원 대기(HOLD) 횟수와 대기 시간, Level 3 요청→허가 대기 시간, 교착 횟수·해소 시간, 일시정지 횟수.

---

## 6. 파일 구조

```
src/part3_fleet_manager/
├─ package.xml, setup.py, setup.cfg       # 실행: fleet_manager_node, mock_hospital_nav, mock_obstacle_classifier, preflight
├─ config/fleet.yaml                      # 8장
├─ launch/fleet_mock.launch.py            # 관제 + mock 로봇 2대 + mock 분류기 2개 (시험용)
├─ part3_fleet_manager/
│  ├─ fleet_manager_node.py               # ROS 노드: 구독·발행·서비스·10 Hz tick. 판단 없음
│  ├─ robot_link.py                       # 3.3·3.4절 어댑터 (HOSPITAL_NAV)
│  ├─ mock_hospital_nav.py                # PART 04 hospital_nav 흉내 (7장)
│  ├─ mock_obstacle_classifier.py         # PART 06 흉내 (7장)
│  ├─ preflight.py, preflight_checks.py   # 실제 연결 점검 도구 (10장). 명령을 보내지 않는다
│  └─ core/                               # ROS import 금지
│     ├─ config.py, geometry.py, schemas.py
│     ├─ mission.py, route_planner.py, resource_manager.py, deadlock.py
│     ├─ stall.py, obstacle.py, decisions.py, safety.py, store.py
│     ├─ robot_protocol.py                # PART 04 명령 규칙 (3.4절)
│     └─ fleet_core.py                    # tick(now) 순서(5.0절)대로 모듈을 부르는 곳
└─ test/                                  # pytest, ROS 없이 실행 (conftest.py, fleet_simulation.py, test_*.py)

src/part1_ui_dashboard/                   # 관제 화면 (WEB_DB_DESIGN.md)
├─ package.xml, setup.py                  # 실행: ui_server, measure_floor_plan
├─ config/ui.yaml, config/floor_plan.yaml
├─ launch/
│  ├─ ui.launch.py                        # 화면만
│  ├─ demo_mock.launch.py                 # fleet_mock.launch.py + 화면 (시험 도구 켬)
│  └─ real.launch.py                      # 관제 + 화면 (실제 로봇, 10장)
├─ part1_ui_dashboard/
│  ├─ ui_server_node.py                   # Flask + rclpy 한 프로세스
│  ├─ web.py                              # Flask 주소
│  ├─ ui_api.py                           # 폼 → 요청 JSON, 화면 문장 표, 지도 변환, 요약 계산 (ROS·Flask 없음)
│  ├─ history.py                          # bad.db 읽기 전용 조회
│  ├─ occupancy.py                        # 맵 읽기·정리
│  └─ static/ index.html, style.css, app.js, start.js, map.js, history.js, pages/*.js
└─ test/
```

- `core/` 안에서는 `rclpy`를 import하지 않는다. 테스트가 이것을 확인한다.
- PART 01이 import하는 PART 03 코드는 `part3_fleet_manager.core.schemas`(요청 검증)와 `part3_fleet_manager.core.config`(`fleet.yaml` 읽기) 두 개뿐이다. 나머지는 토픽과 읽기 전용 DB로만 주고받는다.
- 의존 방향은 PART 01 → PART 03 한쪽뿐이다. PART 03은 PART 01을 import하지 않는다.

## 7. mock 로봇

`mock_hospital_nav.py`는 PART 04 `hospital_nav`와 **같은 토픽 이름과 같은 JSON**으로 동작한다.

- `/<ns>/hospital_nav/command`를 받아 3.3절 규칙대로 받거나 거절한다 (거절은 로그만, PART 04와 같게). `IDLE`·`COMPLETE`·`FAILED`·`CANCELED`에서 받은 `CANCEL`도 로그만 남기고 버린다.
- 구역 사이를 정해진 속도(`mock.speed_mps`, 0.2 m/s)로 직선 보간해 움직이고, 복도를 지날 때는 입구 중심점(`mouths`)을 거친다.
- 1초마다 `STATE`, 이벤트마다 `STATUS`를 보낸다. `amcl_pose`도 움직일 때 발행한다. 도크·위험 센서 토픽은 1 Hz로 "도크 밖·감지 0건"을 내고, Nav2 `navigate_to_pose` 액션 서버도 이름만 띄운다 (preflight 확인용).
- `/mock/control`(`std_msgs/String` JSON)으로 실행 중에 시험 상황을 만든다. 예 `{"robot": "robot_a", "action": "stall", "seconds": 40}`.
  - `action`: `stall`(지금 위치에서 N초 멈춤), `fail_next`, `reject_next`, `drop_link`(N초), `battery`(`value` %), `obstacle`(`kind`: `person`|`object`|`none`|`error`|`silent`)
- `mock_obstacle_classifier.py`: 로봇마다 `/<ns>/obstacle/inspect_image`를 받으면 1초 뒤 3.2a절 형식의 결과를 낸다. 결과는 `/mock/control`의 `obstacle`로 정한다 (기본 `none`). `person`·`object`의 `pose`는 로봇 앞 1 m.
- 침상 감시 흉내는 화면의 시험 도구(가짜 침상 감시) 또는 `ros2 topic pub /patient/bed_exit std_msgs/msg/Bool "{data: true}"`로 한다.

## 8. 설정 `config/fleet.yaml`

```yaml
robots:
  robot_a: {label: "Robot A", ns: robot2, role: PATIENT_TRANSPORT, start_zone: D}
  robot_b: {label: "Robot B", ns: robot3, role: MED_ROUND,         start_zone: F}
type_to_robot: {PATIENT_TRANSPORT: robot_a, MED_ROUND: robot_b, CALL: robot_b}   # assignment.mode: ROLE일 때만 씀

assignment:
  mode: IDLE_BEST_BATTERY       # IDLE_BEST_BATTERY | ROLE
  min_battery_pct: 20

robot_link:
  mode: HOSPITAL_NAV            # HOSPITAL_NAV | ICD_GOAL_POSE(자리만, 고르면 시작하지 않음)
  command_topic: "/{ns}/hospital_nav/command"
  status_topic: "/{ns}/hospital_nav/status"
  amcl_pose_topic: "/{ns}/amcl_pose"
  battery_topic: "/{ns}/battery_state"
  hazard_topic: "/{ns}/hazard_detection"
  dock_status_topic: "/{ns}/dock_status"
  request_id: auto              # auto | always | never (3.3절 R1 표)
  accept_timeout_s: 3.0
  link_timeout_s: 3.0           # STATE가 1 Hz라서 3초

zones:                          # PART 04 goal_manager.py와 같은 값. name: 화면 이름 (비면 "A 구역")
  A: {x: -2.20, y:  0.50, name: ""}
  B: {x: -2.55, y: -2.20, name: ""}
  C: {x: -2.30, y: -5.00, name: ""}
  D: {x: -0.60, y:  0.40, name: ""}
  E: {x: -0.55, y: -2.40, name: ""}
  F: {x: -0.80, y: -5.10, name: ""}

corridor:
  mode: SHARED_YIELD            # SHARED_YIELD(두 대 같이, 마주 오면 낮은 쪽이 잠깐 정지) | EXCLUSIVE(한 대씩)
  yield_enabled: true
  yield_trigger_m: 1.2          # 리허설에서 조정
  yield_release_m: 0.5
  yield_timeout_s: 10.0
  include_boundary: true        # 복도 경계 위의 위치도 CORRIDOR로 본다
  polygon: [[-1.89, -0.51], [-1.00, -0.58], [-1.26, -4.23], [-2.16, -4.17]]   # [TL, TR, BR, BL]. 맵의 복도 벽 안쪽 면 (폭 0.90 m)
  mouths:                       # 입구·문 중심점 (경로 표시·mock 경로)
    TOP:    {x: -1.37, y: -0.55}
    BOTTOM: {x: -1.72, y: -4.20}
    ROOM_B: {x: -1.96, y: -2.10}
    ROOM_E: {x: -1.08, y: -2.25}

resources:
  clear_margin_m: 0.20
  clear_hold_s: 1.0
  decision_window_s: 1.0
  aging_s: 0                    # 0 = 끔

deadlock:
  confirm_s: 2.0
  card_timeout_s: 20
  yield_zones: {ROOM_B: D, ROOM_E: F}

stall:
  window_s: 5.0
  move_m: 0.05
  short_s: 10.0
  long_s: 30.0

obstacle:                       # PART 06 장애물 분류 (3.2a·5.5절)
  enabled: true
  inspect_topic: "/{ns}/obstacle/inspect_image"
  result_topic: "/{ns}/obstacle_classifier/result"
  inspect_at_s: 10.0
  timeout_s: 6.0                # PART 06 촬영 제한 3초 + 추론 여유
  object_blocks_now: true       # OBJECT면 30초를 기다리지 않고 바로 막힘 처리
  min_confidence: 0.4           # 이보다 낮은 탐지는 종류 판단에서 뺀다 (기록에는 남김)
  label_ko: {person: 사람, chair: 의자, suitcase: 가방, backpack: 가방, bottle: 병, bed: 침대, couch: 소파, dining table: 탁자, potted plant: 화분, dog: 개, cat: 고양이}

safety:
  bed_monitor:                  # 침상 감시 끊김 (5.6절)
    warn_s: 1.0
    lost_s: 3.0                 # 이때 정지. 리허설에서 조정
    recover_s: 2.0
    min_rate_hz: 10.0
    require_at_start: true      # fleet_mock.launch.py에서는 false
  bed_exit_pause_targets: [robot_a]   # 팀 확정 필요: [robot_a] 또는 [robot_a, robot_b]

mission:
  return_home: true             # 임무 후 홈 구역(start_zone)으로 복귀
  return_interruptible: true    # 복귀 중에도 새 임무 배정
  return_level: 1               # 홈 복귀의 응급도 (끝낸 임무에서 물려받지 않음)
  manual_level: 1               # GO_ZONE 응급도
  retry_after_return: false     # 되돌아간 뒤 원래 구간은 운영자가 다시 시도
  confirm_at_stops: true
  return_to_dock: false

publish:
  fleet_state_hz: 5.0
  tick_hz: 10.0

db:
  path: ~/.bad/bad.db           # real.launch.py는 ~/.bad/bad_real.db를 넘긴다

mock:
  speed_mps: 0.2
  stall_radius_m: 0.05          # 시험 장애물 좌표와 mock 이동 선분 사이의 허용 거리
  supports_request_id: true     # 로봇별 launch 인자로 R1 반영 전후를 섞어 시험
```

## 9. 테스트 (pytest, ROS 없이, 가짜 시계)

`fleet_core`에 가짜 로봇 보고를 넣고 tick을 돌려 결과(보낸 명령, 이벤트, 카드, 상태)를 확인한다. 화면(PART 01) 테스트도 이 목록에 같이 둔다.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PYTHONPATH=src/part1_ui_dashboard:src/part3_fleet_manager \
  python3 -m pytest -p no:cacheprovider -q src/part1_ui_dashboard/test src/part3_fleet_manager/test
```

1. `EXCLUSIVE` 모드: 두 로봇이 동시에 복도가 필요하면 하나만 허가, 다른 하나는 `HOLD` + 사유 문장. `SHARED_YIELD` 모드: 둘 다 허가 (B·E 방이 겹치지 않을 때)
2. 둘 다 대기면 level 높은 쪽 먼저, 같으면 먼저 요청한 쪽
3. 허가 후 더 높은 level이 와도 회수하지 않음
4. Robot A가 B방에 있을 때 Robot B의 B방행 구간은 대기
5. B방↔E방 맞바꾸기: 순환 감지 → 카드 → 시간 초과 시 추천 로봇이 홀로 비킴 → 둘 다 완료
6. 한꺼번에 허가: 필요한 자원 중 하나라도 잡혀 있으면 아무것도 허가하지 않음
7. 해제: 복도+여유 밖 1초 연속 + 연결 살아 있음일 때만 `FREE`
8. 연결 끊김 → `UNKNOWN`, 새 허가 없음, `RESOURCE_CLEAR_CONFIRM` 후 `FREE`
9. 10초 멈춤은 기록만, 30초 멈춤은 `blocked` + 카드 + 다른 로봇 허가 막힘, 다시 움직이면 해제
10. 침상 이탈 → 대상 로봇 `PAUSE`, 이탈 중 재개 거절, 해제 후 재개 → `RESUME` 전송
11. `MOVE` 후 3초 안에 `MISSION_ACCEPTED` 없음 → 한 번 재전송 → 또 없음 → `FAULT` + 카드
12. 로봇 쪽 `PAUSED`에서 새 구역으로 보낼 때 `CANCEL` → `CANCELED` 확인 → `MOVE` 순서
13. `MISSION_FAILED` → `FAULT` + 카드, [다시 시도]로 같은 구간 재전송
14. 복도 안 점유 중 임무 취소 → `E-423` 거절, `FAULT`면 허용 + 잠금 유지
15. 픽업 이송: 픽업 도착 → 확인 → 목적지 → 확인 → 완료
16. 물품 전달 [불일치] → 그 자리 보류 + 카드
17. JSON 형식: 4장 예시가 그대로 파싱·검증되고, 잘못된 요청은 `E-400`
18. `core/`에 `rclpy` import 없음
19. `request_id: auto`: `id=`를 버리는 옛 로봇 → 3초 뒤 `id=` 없이 재전송 → 이후 그 로봇에는 `id=` 안 붙임. `id=`를 받는 로봇 → 계속 붙임
20. 범퍼(`BUMP`) 새로 감지 → 그 로봇 `SAFE_PAUSE` + `PAUSE` 전송 + 경고, `RESUME_ROBOT`으로 재개
21. 복도 양보: 마주 오며 1.2 m 안 → level 낮은 쪽만 `PAUSE` + `GIVE_WAY` → 우선 로봇이 지나가 0.5 m 벌어지면 `RESUME`
22. 복도 양보: level이 같으면 복도에 나중에 들어온 쪽이 섬
23. 복도 양보: 같은 방향 두 로봇, B방↔E방 가로지르기, 거리 1.2 m 초과는 아무것도 안 함
24. 복도 양보 안전장치: 우선 로봇이 10초 진행 못 하면 `RESUME` + `CORRIDOR_YIELD_TIMEOUT`
25. 복도 양보 중 침상 이탈 → `SAFE_PAUSE` 우선, 재개 후 양보 다시 판단
26. **두 로봇 동시 임무** (`assignment.mode: ROLE`로 고정해서 시험): Robot A가 환자 이송(C → A)을 하는 동안 Robot B가 물품 전달(F → E → B)을 하고, 그 사이 두 로봇 모두에 새 임무가 대기열로 들어온다. 두 임무가 끝까지 같이 진행되고, 복도 양보와 B·E 방 규칙이 지켜지고, 대기열 임무가 이어서 시작된다
27. **기본 배정 (`IDLE_BEST_BATTERY`)**: `CALL` 두 건이 연달아 오면 배터리 많은 로봇부터 배정, 둘 다 바쁘면 세 번째는 "쉬는 로봇 없음"으로 대기, 배터리 20% 이하 로봇은 건너뜀, 배터리를 모르는 로봇은 뒤로
28. **복귀**: 마지막 [수령 완료] → `MISSION_DONE` → 홈 구역 `MOVE` → `RETURNING` → 도착 시 `IDLE`. 복귀 중 새 임무가 오면 `CANCEL` → `CANCELED` 확인 → 새 임무 `MOVE`
29. **복귀-취소 경합**: 복귀 중 새 임무 → `CANCEL` 직후 복귀 구간 `ARRIVED`만 옴 → 카드 없이 바로 새 임무 `MOVE`. STATE `COMPLETE`만 와도 같다. 3초 동안 셋 다 없으면 `NAV_FAILED` 카드
30. **화면용 상태 필드**: 주행 중 `route`의 끝점이 목적지 구역이고 복도를 지나면 입구 중심점을 거친다. 대기 중 `queue_detail`에 level·since가 있다. 복도 판단 직후 `resources.CORRIDOR.reason`과 `last_decision.detail.reason`이 그 판단 문장과 같다. 끝난 임무가 `missions`에 최근 20건 남는다
31. **판단 근거**: 두 로봇이 동시에 복도를 요청하면 `RESOURCE_GRANT`·`RESOURCE_HOLD`의 `detail.rule`이 `LEVEL`(level 다를 때)·`REQUEST_TIME`(같을 때)이고 `contenders`에 두 로봇이 있다. 허가 뒤 더 높은 level이 오면 그 로봇의 `RESOURCE_HOLD`에 `NO_PREEMPT`. 양보는 `CORRIDOR_YIELD_START`에 `LEVEL` 또는 `ENTRY_ORDER`
32. **화면 문장** (`ui_api`): 4.8절 이벤트 종류 전부와 5.1절 로봇 상태 전부에 한국어 문장이 있다. 하나라도 빠지면 실패 (이 문서를 직접 읽는다)
33. **화면 지도** (`ui_api`, 실제 맵 파일이 없으면 건너뜀)
    - 자동 기울기가 3.6°~4.6° 사이다.
    - 변환 뒤 위 줄이 D·E·F, 아래 줄이 A·B·C 순서다.
    - 평면도 벽(`floor_plan.yaml`)이 실제 맵과 맞다: 벽 선마다 5 cm 간격으로 찍은 점의 80% 이상이 실제 맵 벽 칸에서 0.10 m 안에 있다.
    - 변환 뒤 평면도 벽이 모두 가로 또는 세로와 0.5° 안으로 나란하다.
    - 구역 점 6개가 평면도 벽에서 0.25 m 이상 떨어져 있고, 문 두 개의 가운데가 복도 다각형의 긴 변 위(0.10 m 안)에 있다.
34. **화면 폼** (`ui_api`): 환자 이송·물품 전달·로봇 호출 폼이 각각 올바른 요청 JSON이 되고 `schemas.py` 검증을 통과한다. 물품 전달은 `MED_ROUND` 정류장 2개(수령지, 전달지)가 된다. 빈 칸·같은 구역 두 번·Level 4는 화면에서 거른다
35. **침상 감시 끊김**: 30 Hz로 `false`를 넣다가 끊는다 → 1초에 `BED_MONITOR_WARN`만(정지 없음) → 3초에 `BED_MONITOR_LOST` + Robot A `SAFE_PAUSE` + `PAUSE` 전송 + 카드. 끊긴 동안 재개 거절, 새 임무는 `SAFE_PAUSE`로 시작. 신호가 2초 돌아오면 `BED_MONITOR_BACK` + 카드 `resolved`, 그래도 저절로 재개하지 않고 재개 명령으로 재개. 돌아온 값이 `true`면 침상 이탈로 넘어감. Robot B(대상 아님)는 계속 움직인다
36. **감시 없이 운행과 시작 조건**: 카드에서 `bypass` → 재개 허용, `BED_MONITOR_BYPASS`에 고른 사람, 신호가 돌아오면 `bypass` 풀림. `require_at_start: true`이고 한 번도 신호가 없으면 Robot A 임무가 `SAFE_PAUSE`로 시작, `false`면 경고만. 수신 속도가 10 Hz 아래로 3초 떨어지면 "불안정" 경고
37. **정류장 테이블**: 환자 이송 등록 → `mission_stops` 2줄 `PENDING` → 배정 시 1번 `ACTIVE` → 도착 `ARRIVED`·`arrived_at` → 확인 `CONFIRMED`·`confirmed_by` → 마지막 확인 뒤 `missions.status = DONE`. 취소하면 남은 정류장 `SKIPPED`. 같은 `req_id`가 두 번 와도 임무는 하나. 옛 DB 파일(칸이 모자란 것)을 열면 칸이 더해지고 기존 줄은 남는다
38. **기록 조회** (PART 01): 시험용 `bad.db`를 만들어 `/api/missions`·`/api/missions/<id>`·`/api/events`·`/api/cards`·`/api/kpi`·`/api/events.csv`가 거르기·쪽 나눔대로 답한다. 화면 서버가 `bad.db`에 쓰려고 하면 실패한다 (읽기 전용으로 열었는지). DB 파일이 없으면 기록 주소는 503, 나머지는 정상
39. **장애물 분류 연결**: 10초 멈춤 → `inspect_image` 한 번. `person` 결과 → 카드 없음, 30초에 `BLOCKED` 카드(이유에 "사람"). `chair` 결과 → 받는 즉시 `STALL_LONG` + `blocked` + 카드(이유에 "의자"), 다른 로봇의 새 허가에서 그 자원이 빠짐. `false` 결과·시간 초과 → 30초 규칙, 이유 문구. 6초 뒤 늦게 온 결과는 판단에 안 씀. 다시 움직이면 `obstacle`이 `null`이 되고, 또 멈추면 새로 찍음. 신뢰도 0.4 미만 탐지는 종류 판단에서 빠짐. 카드 [다시 출발] 버튼 글이 분류에 맞음
40. **장애물 결과 JSON**: 3.2a절 예시(사람+의자, pose 있음·없음), `obstacle_present: false`, `image_timeout`, `processing_error`, 깨진 JSON이 각각 PERSON·OBJECT·NONE·UNKNOWN으로 나뉘고, 라벨이 한국어로 바뀐다
41. **기록 문장** (PART 01·03): 4.8절 이벤트 종류마다 관제가 실제로 만드는 이벤트를 화면 문장 함수에 넣으면, 제목과 보조 문장에 `robot_a`·`robot_b`, `OK`·`MISMATCH`·`DONE` 같은 대문자 코드 값, `selected`·`timeout`·`resolved`가 나오지 않는다. 같은 이벤트를 시험용 `bad.db`에 쓰고 기록 조회로 읽어 그려도 실시간과 같은 문장이다. 옛 형식(`reason`이 코드 값)의 행도 `WEB_DB_DESIGN.md` 화면 문장대로 바뀐다
42. **전체 정지·재개 기록**: 화면의 [안전 정지 요청]·[전체 재개]가 `PAUSE_ALL`·`RESUME_ALL`과 조작자 이름을 보내고 `events.by`에 그 이름이 남는다. 침상 이탈·감시 끊김 중 `RESUME_ALL`은 `/robot/resume`과 같이 거절되고 `RESUME_REJECTED`에 이름이 남는다. `/robot/pause`·`/robot/resume` 서비스는 그대로 동작하고 `by`는 "ROS 서비스"
43. **연결 점검** (`preflight`): PART 04 `goal_manager.py`의 좌표를 실행하지 않고 읽는다. 구역 좌표 차이 0.05 m 경계에서 경고가 맞게 나뉜다. 발견 서버 주소·포트·소유 프로세스가 틀리면 "실제 실행 안 됨"이다. 점검 중 로봇 명령은 0건이다

- 화면 라우트 테스트 (Flask test client): 버튼마다 가짜 publisher에 올바른 토픽과 JSON이 넘어간다. `dev_tools: false`면 시험 도구 라우트가 403이다. `/map/meta`·`/map/image`가 맵이 있을 때와 없을 때 맞게 답한다.
- 화면 확인은 mock 2대로 시나리오 1~5(`stage3-scenarios.md`)를 **화면 클릭만으로** 끝까지 진행하고, 캡처를 `docs/part3/ui/screens/`에 남긴다. 녹화는 사람이 한다.

## 10. 실제 로봇 연결

단계별 작업 기록은 `STAGES.md`로 옮겼다. 이 장은 실제 연결 도구의 사양이다. 현장 순서는 `REAL_TEST.md`, 새 PC 설치는 `CONTROL_PC_SETUP.md`에 있다.

- **네트워크는 실행 파일에서 정하지 않는다.** 이 팀은 Fast DDS 발견 서버를 **관제 PC에서** 띄운다: `/usr/bin/fastdds discovery --server-id 0 --ip-address <관제 PC IP> --port 11811` (11/18 강의실 관제 PC는 `192.168.108.31`). 로봇과 PART 04 노트북은 모두 이 서버에 붙는다.
  - 관제 노드를 띄우는 터미널도 `ROS_DISCOVERY_SERVER=<관제 PC IP>:11811`로 같은 서버에 붙고, 명령줄 도구가 전체 토픽을 보도록 `ROS_SUPER_CLIENT=True`를 둔다.
  - `ROS_DOMAIN_ID`·`RMW_IMPLEMENTATION`은 PART 04가 쓰는 값과 같게 한다.
  - mock 시험용 `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`·격리 도메인은 실제 실행에서 쓰지 않는다. 반대로 mock은 반드시 격리해서 띄운다 (mock 로봇도 `/robot2`·`/robot3` 이름을 쓴다).
- **PART 04 `keyboard_control`과 같이 띄우지 않는다.** 그것도 `/<ns>/hospital_nav/command`로 명령을 보내는 시험용 관제다. 둘이 같이 돌면 한 로봇에 명령이 두 군데서 간다.
- 관제 PC는 팀 Python 가상환경(`rokey_venv`)을 켠 터미널에서 실행한다. 코드가 venv 밖 경로를 가정하지 않는다.
- **실행 파일** `ros2 launch part1_ui_dashboard real.launch.py`: 관제 + 화면만 띄운다 (mock 로봇·mock 분류기 없음, 시험 도구 끔, "실기 모드" 표시). 인자: `config_file`, `map_yaml`, `db_path`(기본 `~/.bad/bad_real.db`), `bed_monitor_require_at_start`(비우면 `fleet.yaml` 값), `ui_config`.
- **연결 점검 도구** `ros2 run part3_fleet_manager preflight`: 읽기만 하고 명령·주행 목표·서비스 호출·촬영 요청을 하나도 보내지 않는다.
  - 셸 환경 출력: `ROS_DOMAIN_ID`, `ROS_DISCOVERY_SERVER`, `ROS_SUPER_CLIENT`, `ROS_AUTOMATIC_DISCOVERY_RANGE`, `RMW_IMPLEMENTATION`. LOCALHOST 범위거나 `ROS_DISCOVERY_SERVER`가 비어 있으면 경고.
  - 발견 서버: 지정한 주소·포트가 이 PC의 Fast DDS 서버 프로세스 것인지 확인한다. 서버를 다른 PC에서 띄우면 이 줄은 "확인 불가"로 나온다. 그때 로봇 줄이 모두 OK면 실행해도 된다.
  - 로봇마다 10초 동안: `hospital_nav/status`의 STATE 수신과 그 `state`, `amcl_pose`, `battery_state`(%), `dock_status`(도킹 여부), `hazard_detection`, Nav2 `navigate_to_pose` 액션 서버. 항목별 OK·없음 표.
  - 명령 토픽 발행자: 관제 시작 전에는 없어야 하고, 관제 말고 다른 발행자(예: `keyboard_control`)가 있으면 이름을 보이고 "실제 실행 안 됨".
  - 침상 감시 `/patient/bed_exit` 수신 속도와 마지막 값, 장애물 분류 결과 발행자 유무 (없어도 실패 아님, "없음 — 30초 규칙으로 동작").
  - `fleet.yaml zones`와 PART 04 `goal_manager.py`의 `GOALS` 비교 (PART 04 파일은 글자로만 읽는다). 0.05 m 넘게 다르면 두 값을 보인다. `--goals-file`, `--config-file`로 다른 파일을 지정할 수 있다.
  - 마지막 줄: "실제 실행 가능" 또는 "실제 실행 안 됨"과 이유 한 줄씩. 안 되면 종료 코드 1.

## 11. 다른 파트에 요구한 변경

### 11.1 PART 04 (`PART04_REQUESTS.md`, 11/19 기준 모두 미반영)

관제 코드는 반영 전후 모두 동작한다.

| # | 변경 | 관제 영향 | 반영 전 관제 동작 |
|---|---|---|---|
| R1 | 명령 거절 시 `COMMAND_REJECTED` 이벤트, 명령 끝 `id=<관제 ID>` | 거절을 바로 알고, 이벤트를 관제 ID로 짝짓는다 | 3초 타임아웃, 첫 `MISSION_ACCEPTED`로 짝짓기 (3.3절 표) |
| R2 | `dock_manager.py` 소스 커밋, `__pycache__` 삭제 | — | — |
| R3 | `MOVE` 때 도킹 중이면 언도킹 후 출발, STATE에 `docked` | 언도킹 상태를 화면에 보여 준다 | `dock_status` 직접 구독으로 도킹 여부만 안다. 시험 전 로봇을 도크에서 뗀다 |
| R4 | `setup.py` `entry_points`, `package.xml` 의존성 | 누구나 `ros2 run`으로 PART 04를 띄울 수 있다 | PART 04가 자기 방식으로 띄운다 |

관제가 코드를 보고 직접 정한 것 (PART 04 작업 없음): namespace `robot2`·`robot3`, 범퍼·도킹 상태·위치는 표준 토픽 직접 구독, `PAUSED`에서 새 목적지는 `CANCEL` → `MOVE`.

### 11.2 PART 05 (`PART05_REQUESTS.md`, 미반영)

| # | 변경 | 이유 | 반영 전 관제 |
|---|---|---|---|
| P5-R1 | 이력이 5프레임 모이기 전(시작 직후, 실패로 이력을 비운 직후)에는 아무것도 발행하지 않는다 | 지금은 그 사이 `False`(정상)를 발행한다. 카메라가 몇 프레임마다 실패하면 환자가 침대를 떠나도 `False`만 나간다 | 끊김은 잡지만 "간헐 실패 중 거짓 정상"은 못 잡는다 |

- 관제의 침상 감시는 10 Hz 아래면 "불안정" 경고를 낸다. PART 05 추론이 30 Hz를 못 따라가도 10 Hz 이상이면 정상이다.

### 11.3 PART 06과 맞출 것

- 어디서 띄우나: 로봇 OAK-D 토픽을 서버 너머로 받으므로 PC에서 띄우는지, namespace(`robot2`·`robot3`)마다 하나씩 띄우는지.
- 결과에 요청 짝짓기 값(트리거 받은 시각이나 순번)을 넣어 줄 수 있는지. 있으면 늦게 온 결과를 더 정확히 거른다. 없어도 관제는 동작한다.
- 병원 시연에서 의미 있는 라벨만 남길지 (COCO 80종 중 사람·의자·가방 등). 관제는 표에 없는 라벨도 영어 그대로 보여 준다.

## 12. 아직 정해지지 않은 것 (코드는 설정값으로)

- 구역 이름(디자인의 접수 구역·촬영실·병실·조제실·수술 베드 구역·수술실)을 어느 글자 구역에 붙일지. 정해지면 `zones.<X>.name`에 넣는다. 화면 지도(복도 가로)에서 위 줄이 D·E·F, 아래 줄이 A·B·C다.
- 디자인의 환자 3구간 이동(접수 → 촬영실 → 병실)은 지금 `PATIENT_TRANSPORT`(픽업 → 목적지)로는 등록 두 번이다. 필요하면 정류장 목록을 받도록 넓힌다.
- 손목 태그 확인은 PART 02 QR 연동 뒤에 붙인다. 그 전까지 화면은 태그 ID를 받아 두기만 한다.
- 침상 이탈 시 멈출 로봇(`bed_exit_pause_targets`), 대기·막힘·양보 시간값, 침상 감시 끊김 시간.
- 팀 ICD v0.3의 로봇 줄(`goal_pose`, `pose` 5 Hz 등)이 PART 04 실제 코드와 다르다. 팀이 ICD를 고칠 때 3.3절을 반영한다.
- 팀 피그마(김주환)에 그리는 화면·로직 그림과 `WEB_DB_DESIGN.md`를 맞춘다. 다른 점: 그림은 주행 명령에서 로봇을 사람이 고르고, 관제는 자동 배정한다. 그림의 버튼 1~8·빨간 경광등이 화면인지 실제 장치인지 확인한다.
- `AGENTS.md`에는 PART 01이 `rosbridge` 연동(미확정)으로 적혀 있다. 실제로는 Flask + SSE 서버가 ROS 토픽을 중계한다. 팀 문서라 담당자가 고친다.

## 13. 오픈소스

### 13.1 쓰는 것 (apt로 설치, 0.2절)

| 패키지 | 어디에 | 대신하는 것 |
|---|---|---|
| networkx (`python3-networkx`) | `route_planner` 영역 그래프, `deadlock` 대기 그래프 순환 탐지(`find_cycle`) | 직접 짠 그래프 탐색·DFS |
| shapely (`python3-shapely`) | `geometry` 영역 판정(`Polygon.contains`), 해제 여유 거리(`buffer`) | 직접 짠 점-다각형 판정 |
| Flask (`python3-flask`) | 관제 화면 서버 | — |
| psutil (`python3-psutil`) | preflight의 발견 서버 포트 소유 프로세스 확인 | — |
| irobot_create_msgs (`ros-jazzy-irobot-create-msgs`) | 범퍼(`hazard_detection`), 도킹 상태(`dock_status`) 구독 | PART 04에 중계 요청 |

### 13.2 검토했지만 쓰지 않는 것

- **Open-RMF**: 여러 로봇의 통로·문·엘리베이터 사용을 조정하는 시스템이다. Nav2 로봇을 붙이려면 로봇마다 zenoh 브리지와 fleet adapter를 따로 구성해야 해서 일정에 맞지 않는다. "공유 자원을 예약하고 허가한다"는 개념이 같으니 발표에서 비교 대상으로 언급한다.
- **BehaviorTree.CPP**: C++ 행동 트리 라이브러리다. PART 04의 Nav2(bt_navigator)가 이미 쓰고 있다. 관제는 파이썬 상태머신으로 충분하다.
- **py_trees / py_trees_ros**: 파이썬 행동 트리와 실시간 트리 뷰어다. 지금 구조(10 Hz tick + 상태머신)를 바꿔야 해서 쓰지 않는다.
- **transitions** (`python3-transitions`): 파이썬 상태머신 라이브러리다. 상태 전이를 콜백으로 처리하는 방식이라 "판단은 tick 한 곳에서" 규칙과 맞지 않아 쓰지 않는다.
- **Nav2 Route Server** (`nav2_route`): 로봇 한 대의 경로 그래프 계획용이다. 여러 로봇 교통 조정은 범위 밖이다. 쓴다면 PART 04 쪽 일이다.

## 14. 관제 화면 (PART 01)

`WEB_DB_DESIGN.md`로 옮겼다. 화면 구성, 지도, 화면 문장, 시험 도구, `ui.yaml`이 모두 거기 있다.
