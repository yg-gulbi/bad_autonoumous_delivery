# 단계 4I · 도크에서 출발하고 복귀하는 hospital_nav 연결

2026-11-19. PART 04 `3a7401b`의 수신기와 도킹 상태머신을 기준으로 PART 01·03을 갱신했다. 격리 mock에서 홈 구역 복귀와 도크 복귀를 각각 끝까지 확인했다. PART 01·03 테스트는 **434개 통과, 실패 0개**였고 두 패키지의 colcon 빌드가 성공했다.

## 관제 동작

| 항목 | 구현한 규칙 |
|---|---|
| 도크에서 출발 | MOVE가 수락되면 언독과 위치 초기화를 합쳐 한 번만 시간을 잰다. 화면은 ‘도크에서 출발 준비’를 표시한다. 준비 중에는 장애물 촬영과 무진전 판정을 멈추고, 위치로 접근하는 상대 로봇은 계속 보호한다. 120초를 넘으면 기존 NAV_FAILED 카드를 열고 CANCEL을 보낸다. PART 04는 이 단계에서 CANCEL을 받는다. |
| 배터리와 도킹 | STATE의 `battery.percent`와 원본 배터리 중 최근 유효 잔량을 쓴다. `battery.status=CHARGING`이면 ‘충전 중’을 붙인다. 도킹 여부는 STATE가 우선이고, 도킹 필드가 없거나 마지막 유효 보고가 `link_timeout_s`(기본 3초)를 넘겼으면 원본 dock_status를 보조로 쓴다. |
| 범퍼 | 기본 `safety.use_hazard_topic: false`에서 관제와 preflight는 hazard_detection을 구독하지 않는다. 복도 방향·간격, 목적지 점유와 근접 정지는 계속 적용한다. true에서는 기존 BUMP와 이웃 정지가 동작한다. |
| 임무 뒤 복귀 | 두 로봇 모두 `return_to: ZONE`이 기본이다. DOCK을 선택하면 도크 접근 구역의 통행·점유 허가 뒤 인자 없는 `DOCK`을 보낸다. 복귀 구간과 짝지어진 DOCKED 또는 COMPLETE와 docked=true가 함께 있는 STATE에서 완료한다. 도크 앞 도착은 접안 완료로 처리하지 않는다. |
| 연결 점검 | 10초 관측 후 두 로봇의 최신 STATE에서 상태명, 배터리, 위치, 도킹 여부를 검사한다. 원본 센서와 Nav2 액션 발견 여부는 참고 줄에 표시한다. 발견 서버 이상과 명령 발행자 충돌 점검은 유지한다. |

## 격리 실행 결과

ROS Jazzy, 팀 가상환경, `ROS_DOMAIN_ID=95`, `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`, Fast DDS 루프백 전용 프로필을 사용했다. 발견 서버·슈퍼 클라이언트 설정을 해제한 셸에서 관제와 화면, mock 두 대를 실행했다. DB·빌드 산출물은 임시 디렉터리에 두었다.

1. **기본 구역 복귀**. 화면에서 C 구역 호출을 등록했다. 약품 운송 로봇(robot3)이 도크 위 F에서 언독했다. LOCALIZING 동안 화면에 ‘도크에서 출발 준비’가 표시됐고 결정 카드는 0건이었다. C에 도착해 수령을 확인하자 임무가 DONE이 되고 F로 돌아가 IDLE이 됐다. 명령 토픽에서 `MOVE C 1 id=L-0001-1`, `MOVE C 1`, `MOVE F 1`을 순서대로 관측했다. 첫 명령은 실제 수신기와 같은 mock의 무통보 거절이고, 두 번째는 관제의 호환 재전송이다. 별도 재실행에서 robot3의 hazard_detection은 발행자 1개·구독자 0개였다. 임무가 끝난 뒤 robot3에 DOCK을 보내자 좌표 미등록 로그만 남고 COMMAND_REJECTED 이벤트는 0건이었다. [출발 준비 화면 · 1440×900](screens/4i-01-undocking.png)
2. **도크 복귀**. 시험용 사본에서 robot2의 복귀 방식을 DOCK으로 바꾸고 호출을 robot2에 배정했다. robot3는 D에서 대기해 F 쪽 도크 접근 지점을 비웠다. C에 도착하여 수령을 확인한 뒤 마지막 명령은 `DOCK` 한 건이었다. 도크 접근 중 NAVIGATING, 접안 때 DOCKING, 완료 때 DOCKED가 차례로 왔고, 화면 상태는 IDLE, docked는 true가 됐다.
3. **원본 토픽이 없는 점검**. 별도로 두 namespace에서 hospital_nav STATE만 발행했다. 배터리·도크·AMCL 원본 미수신, Nav2 액션 미발견, 범퍼 ‘사용 안 함’인 상태에서 preflight는 두 대 OK와 ‘실제 실행 가능’으로 종료했다(코드 0). 같은 조건에서 robot3의 docked 필드만 빼자 ‘/robot3 STATE.docked 없음’과 종료 코드 1이 나왔다. 캡처는 첫 번째 점검의 실제 표준출력을 브라우저에 그대로 표시해 저장한 것이다. [STATE 전용 preflight · 1440×900](screens/4i-02-preflight.png)

## 테스트와 빌드

워크스페이스 루트에서 팀 가상환경과 Jazzy 환경을 불러와 실행했다.

```bash
export PYTHONPATH="src/part1_ui_dashboard:src/part3_fleet_manager:${PYTHONPATH}"
python3 -m pytest -p no:cacheprovider -q src/part1_ui_dashboard/test
python3 -m pytest -p no:cacheprovider -q src/part3_fleet_manager/test
```

| 대상 | 결과 |
|---|---|
| PART 01 | 56 통과, 0 실패 |
| PART 03 | 378 통과, 0 실패 |
| `colcon build --symlink-install --packages-select part3_fleet_manager part1_ui_dashboard` | 2개 패키지 성공 |

BRIEF 50~54번에는 출발 준비가 90초간 계속되는 경우와 120초 초과, 수락 이벤트 유실, 배터리 수신 순서, 반복되는 미지 상태, DOCKED 유실 시 STATE로 완료, 범퍼 비활성 상태에서 주행 로봇 근접 정지를 포함했다. 기존 범퍼 시험은 설정을 명시적으로 켜서 검증한다.

## 정한 값과 현장 확인 항목

- mock의 언독과 최종 접안에는 각각 1초를 주고, 전압은 15.0 V를 쓴다. 위치 준비 기본은 요청값 3초다. 기본 ZONE 시연에서는 두 로봇의 가상 도크를 기존 시작 구역 D·F에 둔다. DOCK 시험에서는 측정된 robot2 도크 좌표를 쓴다. 도크 좌표가 비어 있는 mock은 실물과 같이 DOCK을 거절한다.
- 같은 미지 상태가 반복될 때 경고가 쌓이지 않도록 로봇별로 최근 미지 상태 이름 32개를 기억한다.
- robot2의 도크 위치 `(-0.330, -5.110)`, 방향 `-3°`와 도크 앞 0.6 m는 PART 04 값이다. 홈 D와 도크가 있는 F 쪽은 서로 다른 구역이다. 관제는 도크 설정을 우선 사용하고 설정이 없는 경우에는 도킹 중 STATE에서 확인한 위치를 사용한다. 복귀 경로를 정할 위치 정보가 없으면 NAV_FAILED로 운영자에게 알린다.
- robot3의 PART 04 도크 좌표는 미등록이다. 실제 robot2 도크 위치와 접근 공간, 언독 후 첫 AMCL 갱신까지의 시간, 접안 후 충전 표시, robot3의 도크 좌표 등록은 현장에서 확인할 항목이다. 이번 검증은 격리 mock에서 진행했다. [현장 시험 순서](../REAL_TEST.md)
- 작업 브랜치는 `feat/part3-4g`다. `origin/main`의 `8fb8ac0`을 충돌 없이 반영했고 병합 커밋은 대기 중이다. PART 04 소스는 해당 main 판본과 같다.

## 변경 파일

| 범위 | 파일 |
|---|---|
| PART 01 | `static/app.js`, `static/pages/dashboard.js`, `test/test_event_sentences.py` |
| PART 03 설정·ROS | `config/fleet.yaml`, `launch/fleet_mock.launch.py`, `fleet_manager_node.py`, `robot_link.py`, `mock_hospital_nav.py`, `preflight.py`, `preflight_checks.py` |
| PART 03 순수 로직 | `core/config.py`, `core/schemas.py`, `core/robot_protocol.py`, `core/mission.py`, `core/fleet_core.py`, `core/collision.py`, `core/resource_manager.py`, `core/deadlock.py`, `core/safety.py` |
| PART 03 테스트 | `test/test_config.py`, `test/test_docking.py`, `test/test_field_traffic.py`, `test/test_fleet_core.py` |
| 문서·캡처 | `BRIEF.md`, `DECISIONS.md` D-28, `REAL_TEST.md`, `STAGES.md`, 이 보고서와 위 캡처 2장 |
