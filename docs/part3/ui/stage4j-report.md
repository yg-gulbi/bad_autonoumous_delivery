# 단계 4J · PART 04 새 코드(main 5b7418f) 맞춤과 의료용 상자 시연

2026-11-19. PART 04 main `5b7418f`의 `goal_manager.py`·`mission_manager.py`·`obstacle_reporter.py`·`command_receiver.py`를 읽고(수정 없음) PART 01·03을 맞췄다. PART 01·03 테스트는 **484개 통과, 실패 0개**(PART 01 58개, PART 03 426개)였다. 두 패키지 colcon 빌드가 성공했다. 격리 mock으로 2번 자리, 교차로 경유, 상자 카드를 화면에서 진행하고 4장을 캡처했다. 커밋·푸시는 하지 않았다.

## 관제 동작

| 항목 | 구현한 규칙 |
|---|---|
| 로봇 이름 (D-29) | `robot_a` 침상형 이송 로봇 = `robot3`(도크 없음, 홈 D). `robot_b` 약품 운송 로봇 = `robot2`(도크 (-0.330, -5.110, -3°), 홈 F). 판단 코드는 그대로이고 `fleet.yaml` 두 줄과 시험·문서만 바꿨다. |
| 자리 2개 (D-30) | `fleet.yaml zones`에 1번 자리(`x`, `y`)와 `slot2`를 넣었다. 1번 자리에 다른 로봇이 서 있거나 그리로 가는 구간이 있으면 2번 자리가 비었을 때 바로 `MOVE F2 n`을 보낸다. 쉬는 중이든 확인 대기 중이든 기다리지 않는다. 둘 다 차면 지금처럼 대기, 30초 카드. 도착·확인·기록 문구는 "F 구역 (2번 자리)". STATE에 `slot` 키가 한 번도 안 온 로봇에는 번호를 붙이지 않는다. |
| B·E 문과 교차로 (D-30) | `ROOM_B`·`ROOM_E`를 문 통과 허가로 바꿨다(`corridor.room_mode: DOOR`). 들어갈 때는 허가 때 잡고 자리 도착에서 놓는다. 나올 때는 허가 때 잡고 `VIA_PASSED` 뒤 교차로 0.6 m를 벗어나면 놓는다. 교차로에 다른 로봇이 있거나 교차로로 가는 중이면 B·E 구간을 허가하지 않는다(`rule: CROSSROAD`, "교차로에 … 있음 → … 대기"). `VIA_PASSED`는 진행으로 보고, 경로선은 교차로를 거친다. 로봇 카드에 "교차로 경유"가 보인다. 4I 방식은 `room_mode: ROOM`으로 남겼다. |
| 장애물 (D-31) | `obstacle.mode: report`가 기본이다. `MISSION_FAILED`(`Nav2: ABORTED`)이면 `NAV_FAILED` 카드를 4초 보류하고 `OBSTACLE`을 기다린다. 같은 임무 ID면 묶고, 6초 안의 첫 PART 06 결과로 판정한다. `medical_box`이면 상자 카드와 복도 막힘, `NONE`이면 3초 뒤 1회 자동 재출발, 결과 없음·`UNKNOWN`이면 "장애물 판별 불가 — 로봇이 찍은 사진 확인" 카드를 연다. 새 이벤트는 `OBSTACLE_REPORT`·`OBSTACLE_RETRY`다. 10초 촬영 요청은 `inspect` 모드로 남겼고 report 모드는 `inspect_image`를 보내지 않는다. |
| 화면 사진 | 화면 서버가 로봇마다 원본·표시 사진 토픽을 구독해 최신 JPEG와 받은 시각만 메모리에 둔다. `GET /api/obstacle_image/<robot>?kind=raw\|masked`. 장애물 카드는 표시 사진 → 원본 순으로 보이고 "판별: 의료용 상자 0.93" 또는 "로봇이 찍은 사진 hh:mm:ss"를 쓴다. |
| 가짜 로봇 | `MOVE Z2 n`을 받고 `slot`을 보낸다. B·E는 교차로를 거쳐 `VIA_PASSED`를 보낸다. report 모드의 `/mock/control` `obstacle`이면 복도에서 멈춰 5초 뒤 `MISSION_FAILED`, 예시 JPEG(`resource/mock/obstacle_sample.jpg`, 직접 그림), `OBSTACLE`을 보낸다. 가짜 분류기는 사진을 받으면 1초 뒤 `medical_box` 0.93 또는 없음을 내고 받은 사진을 표시 사진으로 다시 보낸다. `supports_slots:=false`면 4J 이전 PART 04처럼 동작한다. |
| preflight | `SLOTS`·`CROSSROAD`(새 형식)와 `GOALS`(예전 형식)를 실행하지 않고 읽는다. 1번·2번 자리와 교차로를 비교한다. 로봇마다 STATE `slot` 여부와 사진 토픽 구독자 수를 보인다. 둘 다 경고만 하고 실행을 막지 않는다. |

## 격리 실행 결과

ROS Jazzy, 팀 가상환경을 썼다. `unset ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT`, `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`, `ROS_DOMAIN_ID=97` 셸에서 `demo_mock.launch.py`를 띄웠다(화면 8097번, mock 속도 0.5 m/s, DB는 작업 폴더). 캡처는 헤드리스 크롬 1440×900이고, 매번 페이지 스크립트 오류는 0건이었다.

1. **F 2번 자리 도착**. 약품 운송 로봇이 F(홈 1번 자리)에 있는 동안 침상형 이송 로봇을 F로 보냈다. 관제는 목적지 대기 없이 `MOVE F2 1 id=L-0001-1`을 보냈다. 로봇은 (-1.35, -4.70)에 섰다. 로봇 블록은 "지금 F 구역 (2번 자리)", 기록은 "침상형 이송 로봇 · F 구역 (2번 자리) 도착"이다. [캡처](screens/4j-01-f-slot2.png)
2. **B 교차로 경유와 B2 자리**. 침상형 이송 로봇을 B 1번 자리에 둔 뒤 약품 운송 로봇을 B로 보냈다. 관제는 `MOVE B2 1`을 보냈다. 경로선은 교차로 (-1.5, -2.18)을 거쳐 B 2번 자리로 그려졌고, 로봇 블록은 "B 구역 (2번 자리) · 직접 이동", "교차로 경유"였다. [교차로 경유](screens/4j-02-b-crossroad.png) 도착 후 두 대가 B방에 함께 있고 문 허가는 비었다. [B2 자리](screens/4j-03-b-slot2.png)
3. **4막 상자**. 시험 도구로 약품 운송 로봇에 의료용 상자를 놓고 F로 보냈다. 로봇은 B방을 나와 복도에 들어서자 멈췄다. 6초 뒤(5초 포기 + 1초 판별) 순서는 `NAV_FAILED` → `OBSTACLE_REPORT` → `OBSTACLE_RESULT` → `BLOCKED` → `CARD_OPEN`이었다. 실패 카드는 따로 열리지 않았다. 침상형 이송 로봇의 B → D 요청은 "B 구역 출입 정원 1대 사용 중"으로 기다렸다. [치웠음, 다시 출발] 뒤의 순서는 `UNBLOCKED` → `CARD_CLOSED`('치웠음, 다시 출발' 선택) → `MOVE F`다. 침상형 이송 로봇은 복도 반대 방향 대기 뒤 D로 갔다. [상자 카드](screens/4j-04-box-card.png)

이 실행 중 F1에서 출발한 약품 운송 로봇이 F2에 선 로봇 옆 0.555 m에서 근접 정지에 걸렸다. 원인과 조치는 아래 결정 4와 [문제 해결 기록](../../troubleshooting/2026-11-19-part3-4j-slot-proximity.md)에 적었다. 캡처는 고친 뒤 다시 찍었다.

## 테스트와 빌드

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PYTHONPATH=src/part1_ui_dashboard:src/part3_fleet_manager \
  python3 -m pytest -p no:cacheprovider -q src/part1_ui_dashboard/test src/part3_fleet_manager/test
colcon build --symlink-install --packages-select part3_fleet_manager part1_ui_dashboard
```

| 대상 | 4I | 4J |
|---|---|---|
| PART 01 | 56 통과 | 58 통과, 0 실패 |
| PART 03 | 378 통과 | 426 통과, 0 실패 |
| colcon 빌드 | — | 2개 패키지 성공 |

- 새 시험(BRIEF 9장 55~63)
  - `test_4j_slots_doors.py` 14개: 11/19 15:30·15:32·15:36 재현, 확인 대기 로봇과 2번 자리, 두 자리 다 참과 30초 카드, C 두 자리·F 출발 근접 기준, 예전 PART 04, B2 입장과 문 한 대씩, B·E 동시 출발 교차로 대기, 나오는 로봇과 들어가려는 로봇, `VIA_PASSED`, 처음 보는 이벤트.
  - `test_4j_obstacle_report.py` 12개: 상자 카드·막힘·버튼·해제, NONE 1회 재출발 뒤 카드만, 판별 불가, 늦은 결과·이른 결과, `OBSTACLE` 없음, 다른 임무 ID, 운영자 선행 재시도, 촬영 요청 없음.
  - `test_4j_demo.py` 1개: 시연 5막.
  - 그 밖: `test_robot_protocol.py` 3개, `test_preflight.py` 3개(실제 `goal_manager.py` 차이 0건 포함), `test_config.py` 늘어난 설정 오류 11개·예전 파일 1개, `test_ui_routes.py` 사진 주소 2개.
- 기존 시험은 지우지 않았다. 바뀐 기본값 때문에 아래 시험은 모드를 시험 안에서 직접 정했다.
  - 교착·방 점유(BRIEF 4·5·26, `test_deadlock.py`, `test_resource_manager.py`): `room_mode: ROOM`.
  - 2단계 시나리오와 3O 촬영(`test_fleet_core.py`의 `sim`, BRIEF 13·39): `obstacle.mode: inspect`.
  - 44번 11/18 재현은 ROOM·DOOR 둘 다 돌린다.
  - 로봇 이름·도크 위치가 바뀐 시험(설정, 도크 복귀 52번, 범퍼 45번 좌표, 복귀 29번의 robot_id)은 새 값으로 고쳤다.

## 결정한 것 (임의로 정한 값은 모두 `fleet.yaml`)

1. **실패 카드 보류 4초** (`obstacle.report_wait_s`). PART 04 촬영 제한 3초에 1초를 더했다. `Nav2: ABORTED`일 때만 보류한다. 다른 실패 이유는 바로 `NAV_FAILED` 카드를 연다. 카드가 두 번 뜨지 않게 하려는 것이다.
2. **이른 결과 사용**. PART 04는 사진을 먼저 보내고 `OBSTACLE`을 나중에 보낸다. 그래서 주행 실패 뒤 `OBSTACLE`보다 먼저 온 PART 06 결과도 그 사진의 판별로 쓴다.
3. **사진 404 기준**. 사양은 "카드가 열린 시각 5초 전보다 오래되면 404"다. 판별 시간 초과(6초) 카드는 사진보다 6초 늦게 열려서 원본 사진이 항상 404가 된다. 그래서 기준을 카드의 사진 보고 시각(`obstacle.reported_at`)과 카드 연 시각 중 이른 쪽으로 잡았다. 열린 장애물 카드가 없으면 404다.
4. **같은 구역 자리 사이 근접 정지 0.45 m** (`safety.slot_stop_m`). 한 대가 자리에 서 있고 다른 한 대가 그 구역으로 오거나 그 구역에서 출발할 때만 쓴다. 둘 다 움직이면 0.6 m 그대로다. C 두 자리는 0.55 m이고, A·D·F는 2번 자리가 1번과 입구 사이에 있어 0.6 m로는 2번 자리를 쓸 수 없다.
5. **자리 점유는 가장 가까운 자리 기준**. C 1번 자리 로봇이 0.55 m 떨어진 2번 자리까지 막지 않게 하려는 것이다.
6. **도크 복귀 구간**은 4I처럼 그 구역 전체와 도크 경로를 본다. 자리 번호를 쓰지 않는다.
7. **교차로 범위 0.6 m** (`corridor.crossroad_clear_m`). 예전 PART 04(`VIA_PASSED` 없음)는 교차로 범위에 들어왔다가 벗어나면 나오는 문 허가를 놓는다. 구간이 중간에 끝나면 범위를 벗어날 때 놓는다.
8. **판별 문구**. NONE 문구 `obstacle.none_text`는 report 모드에만 쓴다. inspect 모드 문구("카메라에는 장애물이 보이지 않음 — 주행 문제일 수 있음")는 기존 39번 시험대로 남겼다. 두 번째 NONE 카드의 이유는 "의료용 상자는 보이지 않음 · 자동 재출발 뒤 다시 멈춤 — 로봇이 찍은 사진 확인"이다. PERSON(다른 모델용)은 "사람 감지" 카드만 열고 막힘 표시와 재출발은 하지 않는다. 자동 재출발 1회는 같은 임무·같은 정류장 기준이다.
9. **화면 문구**. 로봇 블록 "가는 곳"은 "B 구역 (2번 자리) · 환자 탑승"처럼 목적을 `·` 뒤에 쓴다. 괄호가 겹치지 않게 하려는 것이다.
10. **mock 상자**. 주행 중이 아니면 다음 주행에서 복도에 들어설 때 멈춘다. 다시 MOVE를 받으면 치워진 것으로 본다. 라이다 거리는 0.42 m로 고정했다.

## 남은 질문

- 현장 거리
  - F1에서 나가는 로봇이 F2에 선 로봇 옆을 실제로 얼마나 붙어 지나는지 확인한다. 0.45 m가 맞는지와 A·D도 같은지 본다.
  - A2에 로봇이 있을 때 A1로 들어가는 로봇이 근접 정지에 걸리는지 본다.
  - mock은 직선으로 달려서 이 경우를 화면에서 재현하지 않았다.
- 현장 시간: Nav2가 약 5초에 포기하고 PART 04 촬영(최대 3초)이 4초 보류 안에 끝나는지 본다. PART 06 추론이 6초 안인지 본다.
- PART 06
  - 결과 `header`가 사진 `header`를 그대로 쓰는지 확인이 필요하다. 그렇다면 수신 시각 창 대신 사진 시각으로 짝지을 수 있다.
  - 1번 프로그램의 `save_dir` 기본값(`/home/mu-02/...`)이 시연 PC에 사진을 쌓는지 확인한다.
- PART 04
  - R1(`id=`)은 5b7418f에도 없다. 거절 통보는 계속 3초 시간 초과로 판단한다.
  - robot3(침상형) 도크 좌표가 미등록이다.
  - 1번 자리 로봇이 먼저 나갈 때 2번 자리 옆을 지나는 배치는 PART 04와 상의할 거리다.
- 그대로 둔 기존 동작: GO_ZONE도 기록에 "임무 완료"가 남는다(4I 이전부터).

## 변경 파일

| 범위 | 파일 |
|---|---|
| PART 01 | `ui_server_node.py`(사진 구독), `web.py`(`Photos`, `/api/obstacle_image`, 시험 도구 종류), `ui_api.py`(새 이벤트 문장, `CROSSROAD`, 2번 자리 문구), `static/app.js`·`map.js`·`pages/dashboard.js`·`pages/tools.js`·`style.css`, `package.xml`(sensor_msgs) |
| PART 01 시험 | `test_ui_routes.py`, `test_event_sentences.py` |
| PART 03 설정·ROS | `config/fleet.yaml`, `launch/fleet_mock.launch.py`, `setup.py`, `resource/mock/obstacle_sample.jpg`(새 파일), `robot_link.py`, `mock_hospital_nav.py`, `mock_obstacle_classifier.py`, `preflight.py`, `preflight_checks.py` |
| PART 03 순수 로직 | `core/config.py`, `core/route_planner.py`, `core/robot_protocol.py`, `core/resource_manager.py`, `core/collision.py`, `core/mission.py`, `core/decisions.py`, `core/obstacle.py`, `core/obstacle_report.py`(새 파일), `core/fleet_core.py` |
| PART 03 시험 | 새 파일 `test_4j_slots_doors.py`, `test_4j_obstacle_report.py`, `test_4j_demo.py`. 고친 파일 `fleet_simulation.py`, `test_config.py`, `test_deadlock.py`, `test_docking.py`, `test_field_traffic.py`, `test_fleet_core.py`, `test_preflight.py`, `test_resource_manager.py`, `test_robot_protocol.py` |
| 문서 | `BRIEF.md`(1.1, 2, 3.2a, 3.3, 4.5·4.6·4.8, 5.0~5.6, 5.5a 새 절, 6~12, 시험 55~63), `DECISIONS.md` D-29~D-31, `WEB_DB_DESIGN.md`, `REAL_TEST.md`, `STAGES.md`(4J, 3.1b), `PART04_REQUESTS.md`, `stage3-scenarios.md`(이름 바뀜 안내), 이 보고서, 캡처 4장 |
| 문제 해결 기록 (`AGENTS.md`) | `docs/troubleshooting/` 3건과 `README.md` 표 |

수정하지 않은 것: `src/part2*`·`part4*`·`part5*`·`part6*`, 루트 `README.md`, `AGENTS.md`. pip 설치 없음.
