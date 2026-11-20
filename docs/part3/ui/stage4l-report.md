# 4L · 주행 중 YOLO 감지와 로봇 출구 대기

2026-11-20 · PART 01·03 전체 pytest **533개 통과, 실패 0개**. 두 패키지의 colcon 빌드가 성공했다. 검증에는 가짜 시계와 로컬 ROS mock 두 대를 사용했다.

## 시뮬에서 발견한 문제와 수정 결과

| 상황 | 관제 동작 | 검증 |
|---|---|---|
| 주행 중 YOLO 감지 | `source=yolo`에서 종류·신뢰도·거리·방위·지도 위치·로봇 위치·PART 04 임무 ID를 기록한다. 최근 감지 문장과 사진은 로봇 블록에서 30초간 볼 수 있다. | 격리 mock이 복도를 주행하며 “주행 중 장애물 감지 · medical_box 0.93 · 약 0.85 m · 계속 주행”을 보냈다. 실제 JPEG가 열렸고 로봇은 F2까지 도착했다. |
| 같은 임무가 이어서 Nav2 포기 | `MISSION_FAILED(Nav2: ABORTED)` 뒤의 포기 보고와 그 사진의 PART 06 판정을 기존 흐름에서 처리한다. | 가짜 시계에서 YOLO 보고 직후 카드와 PAUSE 모두 0건. ABORTED와 포기 사진 뒤 늦게 도착한 YOLO 사진 판정은 건너뛰고, 포기 사진의 상자 판정으로 BLOCKED 카드 1장이 열렸다. |
| D1 옆 통과와 근접 정지 | 연결된 상대가 구역 자리에 정차했고 진행 구간이 없으면 출발지·목적지에 관계없이 0.45 m 기준을 쓴다. 상대에게 진행 구간이 있으면 일반 기준 0.6 m를 쓴다. | robot3를 D1 옆 (-1.01, 0.68)에 두고 측정했다. 가짜 시계에서 robot2는 0.58 m로 통과했고 0.40 m에서는 PAUSE를 받았다. |
| 두 로봇이 함께 대기 | 근접 카드의 [이 로봇 먼저 보내기]는 양보 로봇의 정지 확인과 기존 통행·안전 조건을 확인한다. 승인 뒤 거리가 줄어들면 다시 세운다. 유휴 또는 후행 대기인 상대의 버튼은 비활성이며 같은 요청을 직접 보내도 관제가 거절한다. | 가짜 시계에서 D→C를 받은 robot3가 대기한 상태로 robot2만 재개했다. 거리가 회복되고 복도 후행 조건도 풀리면 robot3에 MOVE가 나갔다. 별도 분기에서 0.71→0.66 m로 좁혀지자 다시 PAUSE가 나갔다. |
| F1 출발과 F2 점유 | F·A의 1번 자리 출발 전에 2번 자리의 실제 위치를 확인한다. 일반 요청은 대기 30초 뒤, Level 3은 첫 tick에 [다시 확인] [그대로 출발(근접 규칙 유지)] [보류] 카드를 연다. | F1은 MOVE 0건으로 기다렸다. F2는 먼저 MOVE를 받았고, 수락만 한 동안에는 F1이 기다렸다. F2가 비자 F1이 출발했다. 그대로 출발을 골라도 처음부터 근접 기준 안이면 첫 MOVE를 보류했다. A에서는 재확인과 보류도 시험했다. |
| preflight 좌표 파일 | 워크스페이스에서 `src/part4_amr_nav/hospital_nav/hospital_nav/goal_manager.py`를 먼저 찾고, 이어서 한 단계 위의 기존 경로를 찾는다. 파일을 읽어 좌표를 비교한다. | 두 파일이 공존할 때의 우선순위와 예전 경로만 있을 때를 시험했다. `406e497`의 실제 새 경로 파일로 비교한 자리·교차로 좌표 차이는 0건이었다. |

[시뮬 원자료](../sim/sim-report.md) · [결정 D-35~D-37](../DECISIONS.md) · [관제 사양](../BRIEF.md)

## 격리 mock 화면

발견 서버와 슈퍼 클라이언트 설정을 해제하고 ROS 도메인 93에서 `LOCALHOST`와 Fast DDS 루프백 인터페이스를 지정했다. 관제·화면·mock을 `demo_mock.launch.py`로 실행했다. 화면 주소는 실행 당시 `http://127.0.0.1:8693`였다. 각 실행에 임시 DB를 썼고 캡처 후 종료했다.

| 화면 (1440×900) | 확인한 내용 |
|---|---|
| [주행 중 YOLO 감지](screens/4l-01-yolo.png) | robot3가 F2로 이동하는 중에 시험 도구의 YOLO 입력을 보냈다. 상태는 ‘이동 중’, 상황 줄은 ‘이상 없음’이었다. |
| [감지 사진 열기](screens/4l-01b-yolo-photo.png) | [감지 사진]으로 해당 보고의 의료용 상자 샘플 JPEG를 열었다. |
| [F1 출구 막힘 카드](screens/4l-02-exit.png) | robot3가 F2에 도착한 뒤 F1의 robot2에 Level 3 호출 M-0002를 배정했다. F1에서 기다리는 이유와 선택지 세 개가 표시됐다. |
| [근접 카드와 먼저 보내기](screens/4l-03-proximity.png) | robot2는 A→F, robot3는 D→C 후행 대기였다. robot2의 [이 로봇 먼저 보내기]만 활성인 것을 확인하고 눌렀다. 실제 기록은 첫 정지 0.445 m → 선택으로 근접 정지 해제 → 0.429 m에서 재정지였다. |

마지막 장면은 해당 경로가 정차 로봇 쪽으로 계속 가까워지는 조건이다. 임시 설정에서 robot2를 A에서 시작하고 D1을 (-1.01, 0.68), 위쪽 복도 진입점을 (-1.01, 0.24)로 두었다. 속도는 이 장면에서 0.16 m/s, 다른 장면에서 0.45 m/s로 뒀다. R1 응답을 켜고 위치 준비 시간을 0.3초로 줄였다. 기본 지도 좌표는 변경하지 않았다.

## 정한 값과 실기 확인 항목

- `zones.<구역>.slot2_blocks_exit`는 좌표를 관리하는 사람이 지정한다. 두 자리가 있는 이전 설정 파일에 키가 없으면 F와 A에 기본 적용한다. 한 자리뿐인 구형 설정에서는 출구 대기가 생기지 않는다. 명시적 설정값이 우선한다.
- [그대로 출발]의 효력은 그 구간과 그 출구 상대에 한정했다. ‘먼저 보내기’는 두 로봇의 실제 보고 위치로 매 tick 거리를 비교한다. 거리가 다시 줄어들 때 허용할 오차를 별도로 더하지 않았다.
- 감지 신뢰도는 소수 둘째 자리까지 표시하고 깊이를 받지 못하면 ‘거리 미측정’으로 기록한다. 사진은 로봇과 보고 시각이 맞을 때만 메모리에서 제공한다. 수신 시차 허용값은 기존 사진 처리와 같은 5초다.
- `406e497`에서 YOLO JPEG는 빈 프레임·0 시각 헤더로 발행하고 포기 JPEG는 카메라 헤더를 보존한다. PART 06이 돌려주는 헤더로 늦은 YOLO 판정을 구분한다. 현장에서는 카메라 헤더가 유지되는지, 실제 위치 보고 지연과 정지 거리, F·A 자리 변경 뒤 출구 동선을 확인해야 한다. 이 단계의 검증 범위는 관제 명령과 화면까지다. Gazebo의 물리 접촉 거리와 실기 제동 거리는 새로 측정하지 않았다.
- 실제 체크아웃은 `feat/part3-4g`의 `1cd45ec`였다. `406e497`은 로컬 `origin/main`에 있어 해당 커밋의 PART 04 소스를 읽어 검증했다. 브랜치·PART 04·미커밋 시뮬 파일은 건드리지 않았다.

## 테스트와 빌드

ROS 2 Jazzy와 기존 `rokey_venv`에서 실행했다. pytest는 두 패키지의 소스를 직접 읽었고, 빌드·설치·로그는 임시 디렉터리를 사용했다.

```bash
export PYTHONPATH="$PWD/src/part1_ui_dashboard:$PWD/src/part3_fleet_manager:$PYTHONPATH"
python3 -m pytest -p no:cacheprovider -q src/part1_ui_dashboard/test
python3 -m pytest -p no:cacheprovider -q src/part3_fleet_manager/test
```

| 패키지 | 통과 | 실패 |
|---|---:|---:|
| PART 01 | 78 | 0 |
| PART 03 | 455 | 0 |
| 합계 | 533 | 0 |

두 패키지 모두 `colcon build --symlink-install --packages-select part1_ui_dashboard part3_fleet_manager`로 빌드했다. 기존 F1 통과 시험에는 새 출구 카드의 [그대로 출발]을 먼저 선택하는 단계를 넣어 원래 검증하던 0.45 m 경계를 계속 시험한다. 4J 이전 설정을 만드는 시험에서는 2번 자리 좌표와 함께 새 출구 표시도 제거한다.

## 변경 파일

| 위치 | 파일 |
|---|---|
| PART 03 설정·코어 | `config/fleet.yaml`, `core/config.py`, `core/collision.py`, `core/fleet_core.py`, `core/robot_protocol.py`, `core/obstacle_report.py`, `core/obstacle.py` |
| PART 03 실행·점검 | `mock_hospital_nav.py`, `preflight_checks.py` |
| PART 03 테스트 | `test/test_4l.py` (신규), `test/test_4j_slots_doors.py`, `test/test_config.py`, `test/test_preflight.py` |
| PART 01 화면·서버 | `static/app.js`, `static/pages/dashboard.js`, `static/pages/tools.js`, `ui_api.py`, `ui_server_node.py`, `web.py` |
| PART 01 테스트 | `test/test_event_sentences.py`, `test/test_ui_routes.py` |
| 문서 | `docs/part3/BRIEF.md`, `DECISIONS.md`, `STAGES.md`, `WEB_DB_DESIGN.md`, 이 보고서와 위 화면 캡처 4장 |
