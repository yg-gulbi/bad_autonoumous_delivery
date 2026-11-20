# 단계 3O · 운영 기록과 장애물 분류

PART 01·03 테스트 325건이 통과했고 두 패키지를 ROS 2 Jazzy에서 빌드했다. 시나리오 4에서 의자는 분류 응답을 받은 tick에 복도 진입을 막았다. 사람과 미검출은 멈춤이 30초에 이를 때까지 기다렸다.

## 3L 검토 반영

화면에서 조작자 이름을 ‘당직 김서진’으로 입력하고 M-0001 도착 후 전체 정지 → 전체 재개 → 불일치 → 다시 확인 → 수령 완료를 눌렀다. 운영 기록을 M-0001로 걸러 본 13행과 작업 상세의 작업 기록 13행에서 같은 문장을 확인했다. 두 표의 정지·재개 행 모두 ‘누가’에 ‘당직 김서진’이 표시됐다. [운영 기록 캡처](screens/3o-records.png) · [작업 상세 캡처](screens/3o-mission-detail.png)

| 기록 | 화면에 표시된 문장 |
|---|---|
| 통행 요청 | Robot A — 응급실(A)으로 가는 구간 요청 |
| 도착 | Robot A · 응급실(A) 도착 |
| 정지 / 재개 | 운영자 안전 정지 요청 / 운영자 재개 |
| 확인 불일치 | 내용 불일치 — 운영자 결정 대기 |
| 카드 선택 | '다시 확인' 선택 |
| 수령 확인 / 임무 완료 | 확인 완료 / 모든 정류장 확인 완료 |

관제는 표시할 문장과 판정 값을 별도 필드에 저장한다. 화면은 실시간 이벤트, 운영 기록, 작업 상세와 CSV의 문장을 같은 함수로 만든다. 3L 시연 DB의 M-0001을 읽기 전용으로 열어 원본의 `robot_a이 A행 구간을 요청했습니다.`, `MISMATCH`, `selected`, `OK`, `DONE` 다섯 값이 위 문장으로 조회되는 것도 확인했다. 기존 행에 조작자 기록이 없으면 미기록으로 표시한다.

화면의 두 버튼은 `/ui/operator_cmd`로 `PAUSE_ALL` 또는 `RESUME_ALL`과 조작자 이름을 보낸다. ROS의 기존 두 Trigger 서비스는 ‘ROS 서비스’로 기록한다. 침상 이탈 중이거나 감시가 끊긴 경우 두 경로 모두 재개를 거절하며, 거절 이벤트에도 요청 출처가 남는다.

## 시나리오 4 · 복도 막힘

각 실행은 빈 시연 DB와 독립된 로컬 ROS 도메인에서 시작했다. `demo_mock.launch.py`로 관제, 주행 mock 두 대, 분류 mock 두 대와 화면 서버를 띄웠다. 브라우저의 **시험 도구 → Robot A → 분류 결과 적용 → 4. 복도 막힘**을 눌렀다. 아래 시간은 장면별 DB에서 읽은 마지막 진행 이후의 경과 시간이다. 10 Hz 관제 주기에 맞춰 소수 첫째 자리로 표시했다.

| Robot A의 분류 결과 | 촬영 요청 | 결과 처리 | 막힘 카드 | 로봇 카드와 지도 | 캡처 |
|---|---:|---:|---:|---|---|
| 사람 | 10.1초 | 11.2초 | 30.1초 | ‘앞에 사람 감지’, 전방 약 1.0 m에 빨간 느낌표와 ‘사람’. 결과 수신 직후에는 통행 가능 | [종합 관제](screens/3o-person-dashboard.png) · [막힘 카드](screens/3o-person-card.png) |
| 물건(의자) | 10.0초 | 11.1초 | 11.1초 | ‘앞에 의자 감지’, 전방 약 1.0 m에 빨간 느낌표와 ‘의자’. 곧바로 복도가 막히고 Robot B가 진입 전 대기 | [종합 관제](screens/3o-object-dashboard.png) · [막힘 카드](screens/3o-object-card.png) |
| 미검출 | 10.1초 | 11.2초 | 30.1초 | ‘카메라에 보이는 장애물 없음’, 지도 표식 없음. 카드에 주행 문제 가능성 표시 | [종합 관제](screens/3o-none-dashboard.png) · [막힘 카드](screens/3o-none-card.png) |

세 실행 모두 멈춤 한 번에 촬영 요청이 한 번이었다. 응답 전에는 ‘장애물 분류 중…’이 표시됐다. mock의 40초 정지가 끝난 뒤 진행이 관측되자 막힘과 장애물 상태가 해제됐고, Robot B가 통과한 뒤 두 로봇 모두 대기 상태가 됐다. [촬영 응답 대기 캡처](screens/3o-inspecting.png)

## 테스트와 빌드

워크스페이스 루트에서 실행했다.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
PYTHONPATH=src/part1_ui_dashboard:src/part3_fleet_manager \
/usr/bin/python3 -m pytest -p no:cacheprovider \
  src/part1_ui_dashboard/test src/part3_fleet_manager/test -q
```

**325건 통과, 0건 실패**. 패키지별로도 PART 01 53건, PART 03 272건이 통과했다.

- **39**는 요청 횟수, 로봇별 응답 기한과 중복·늦은 응답, 종류별 막힘 시점, 다른 로봇의 진입 보류, 진행·재시도 뒤 재촬영, 안전 정지·주행 실패의 우선 처리를 확인한다.
- **40**은 사람과 물건이 함께 있는 JSON, 위치 유무, 신뢰도 경계, 카메라 미검출, 촬영·처리 오류 및 깨진 JSON을 확인한다.
- **41**은 관제에서 실제 발생시킨 이벤트의 제목·문장과 같은 이벤트를 임시 DB에 저장해 Flask로 조회한 결과를 대조한다. 별도로 옛 형식의 이벤트를 운영 기록과 작업 상세 양쪽에서 읽어본다.
- **42**는 화면 명령의 조작자 이름, ROS 서비스 출처, 실제 DB의 정지·재개와 거절 행을 확인한다.

두 패키지의 소스를 임시 빌드 디렉터리에 복사한 뒤 `colcon build`를 실행했다. 결과는 `2 packages finished`였고 오류·경고는 0건이었다. 최종 설치 환경에서 사용한 시연 진입점은 다음과 같다. 시나리오별로 별도 `db_path`와 로컬 포트를 사용했다.

```bash
source /tmp/part3-stage3o.AOhDqENr/release/install/setup.bash
ros2 launch part1_ui_dashboard demo_mock.launch.py mock_speed:=0.6
```

## 구현 결정과 남은 확인

- 장애물 존재가 참인데 신뢰도 기준 0.4를 만족하는 탐지가 하나도 없으면 ‘분류 불가’로 처리한다. 기준 미만 탐지와 신뢰도도 기록에 보존한다. 여러 위치가 있으면 해당 종류 중 가장 가까운 탐지까지의 map 평면 거리를 표시한다.
- 처리 지연은 관제 tick에서 측정하고 6초 이내 수신 여부는 콜백 수신 시각으로 판단한다. 기한을 넘거나 기존 멈춤이 끝난 응답은 이벤트에 `applied=false`로 기록한다. 표에 없는 카메라 라벨은 BRIEF대로 원문을 표시한다.
- 3O의 mock 검증은 완료했다. 실제 PART 06의 네임스페이스, 토픽 연결 및 요청 식별자 지원 여부는 BRIEF 11.2절의 현장 확인 항목이다. 현재 계약에는 요청 ID가 없어 이전 요청의 매우 늦은 응답이 다음 요청의 수신 창 안에 도착하면 두 응답을 완전히 구별할 수 없다.
- 개발 중 첫 분류 결과의 이벤트 기록이 관제 노드를 종료시키는 문제를 수정했다. 자연스럽게 막힘이 풀렸을 때는 새로 움직인 첫 표본을 보존해 다음 멈춤도 정확히 잰다. [재현과 수정](../troubleshooting/2026-11-18-obstacle-result-event.md)

## 만든·바꾼 파일

| 위치 | 파일 |
|---|---|
| PART 03 설정·실행 | `config/fleet.yaml`, `launch/fleet_mock.launch.py`, `setup.py` |
| PART 03 `part3_fleet_manager/` | `fleet_manager_node.py`, `robot_link.py`, `mock_hospital_nav.py`, `mock_obstacle_classifier.py`(신규) |
| PART 03 `part3_fleet_manager/core/` | `config.py`, `deadlock.py`, `decisions.py`, `fleet_core.py`, `mission.py`, `obstacle.py`(신규), `resource_manager.py`, `safety.py`, `schemas.py`, `stall.py`, `store.py` |
| PART 03 `test/` | `fleet_simulation.py`(공유 시험 도구 분리), `test_deadlock.py`, `test_fleet_core.py`, `test_obstacle.py`(신규), `test_resource_manager.py` |
| PART 01 `part1_ui_dashboard/` | `history.py`, `ui_api.py`, `ui_server_node.py`, `web.py` |
| PART 01 `part1_ui_dashboard/static/` | `app.js`, `map.js`, `pages/dashboard.js`, `pages/log.js`, `pages/missions.js`, `pages/tools.js` |
| PART 01 `test/` | `test_event_sentences.py`(신규), `test_history.py`, `test_ui_routes.py` |
| 문서 | 이 보고서, 위 표와 본문에 연결한 신규 화면 캡처 9장, 장애물 이벤트 기록 오류 해결 문서 1건 |
