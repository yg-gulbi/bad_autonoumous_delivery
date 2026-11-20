# 단계 3D-3 · 3S 시연 기록

ROS 2 Jazzy에서 관제, Robot A·B mock, PART 01 화면을 함께 실행했다. 아래 캡처는 최종 빌드의 브라우저 화면(1440 × 900)이다.

## 3D-3 지도와 복도 표기

| 화면 | 확인한 내용 |
|---|---|
| [평상시](screens/01-dashboard.png) | Robot A·B의 ‘대기’는 원 안쪽 방향에 있다. 102호 병실(B) 이름표는 오른쪽으로 이동했고 여섯 이름표 모두 벽 안에 들어온다. 가운데에 ‘복도’가 보인다. |
| [시나리오 2 · 복도 양보](screens/02-corridor-wait.png) | Robot A의 ‘복도 양보 중’과 Robot B의 ‘통과 중’이 두 원의 서로 바깥쪽에 놓인다. 우선 로봇이 지나간 뒤 양보 종료와 두 임무 완료까지 확인했다. |
| [시나리오 4 · 복도 막힘](screens/09-corridor-blocked.png) | Robot A가 복도 안에 멈추고 Robot B는 진입 전 대기가 된다. 오른쪽 복도 칸과 상단에 막힘이 표시되고, 장애물이 해제되면 두 로봇의 주행이 끝난다. |

세 장면에서 상태 글·구역 이름표가 벽, 로봇 원 또는 다른 글자와 겹치는 곳은 보이지 않았다. 두 주행 장면은 경로 선이 복도 글자의 세 후보를 지나므로 지도 가운데의 ‘복도’ 글자가 숨겨진다.

가로 후보 거리는 BRIEF가 수치를 지정하지 않아 글자의 실측 너비에 맞춰 정했다. 로봇 상태 글은 상자 반폭에 18px, 구역 이름표는 반폭에 8px를 더해 중심에서 옆으로 옮긴다. 상자 가장자리에는 1px 판정 여유를 두었다.

**변경 파일** (패키지 소스 기준)

- PART 01 — `ui_api.py`, `static/map.js`, `static/app.js`, `static/pages/dashboard.js`, `static/pages/tools.js`
- PART 03 — `core/config.py`, `core/resource_manager.py`, `core/deadlock.py`, `core/mission.py`, `core/schemas.py`, `test/test_schemas.py`
- 캡처 — 위 세 PNG

## 3S 침상 감시

시험 도구에서 [정상 송신]을 누르고, 화면에서 수술실(C) CALL을 등록해 Robot A에 배정했다. 가짜 침상 감시의 송신 주기는 10 Hz이고, 화면을 닫아도 화면 서버의 ROS 타이머가 계속 발행한다.

| 화면 조작 · 캡처 | 확인한 관제·로봇 상태 |
|---|---|
| [송신 끄기 후 1초](screens/10-bed-monitor-warn.png) | 배지가 주황색으로 바뀌고 경고 알림이 뜬다. Robot A는 `MOVING`을 유지한다. |
| [3초 끊김 경보](screens/11-bed-monitor-lost.png) | `BED_MONITOR_LOST`, Robot A `SAFE_PAUSE`, mock `PAUSED`. 빨간 띠와 경보 창이 뜨고 전체 재개가 비활성화된다. |
| [경보 확인 후 결정 카드](screens/12-bed-monitor-card.png) | [계속 정지]와 [감시 없이 운행]이 표시된다. 첫 번째 끊김에서는 [나중에]로 카드를 보류했다. |
| [정상 송신 복구](screens/13a-bed-monitor-back.png) | 2초간 연속 수신한 뒤 `BED_MONITOR_BACK`이 기록되고 카드가 `resolved`로 닫힌다. Robot A는 `SAFE_PAUSE`, mock은 `PAUSED`를 유지한다. |
| [운영자 재개](screens/13-bed-monitor-resumed.png) | [전체 재개]를 누른 뒤 Robot A와 mock 모두 `MOVING`을 보고한다. |
| [두 번째 끊김 · 감시 없이 운행](screens/14-bed-monitor-bypass.png) | 카드에서 [감시 없이 운행]을 선택하고 [전체 재개]를 눌렀다. 송신이 꺼진 상태에서 Robot A가 이동하고 우회 띠가 유지된다. `BED_MONITOR_BYPASS.detail.by`는 `리허설 담당자`였다. 이후 정상 송신을 2초 이어 가자 우회가 해제됐다. |

수신율은 최근 3초 수신 횟수로 계산한다. 시작 후 3초가 차기 전에는 첫 수신부터의 간격으로 추정하고, 복구 직후에는 누락된 구간이 3초 창에 남아 표시값이 잠시 10 Hz보다 낮을 수 있다. [감시 없이 운행]은 운영자의 명시적 재개를 허용한다. 재개 버튼을 누른 뒤 주행한다.

화면을 관제보다 늦게 켜도 시작 시 열린 카드를 선택할 수 있도록 `bed_exit.monitor`에 열린 카드도 담았다. 화면에 필요한 경고 구분, 대상 로봇, 시작 필수 여부도 함께 전달한다. 실기의 `require_at_start`는 `true`, mock은 `false`다.

**변경 파일** (패키지 소스 기준)

- PART 01 — `ui_api.py`, `ui_server_node.py`, `web.py`, `static/index.html`, `static/app.js`, `static/start.js`, `static/style.css`, `static/pages/dashboard.js`, `static/pages/tools.js`, `test/test_ui_routes.py`
- PART 03 — `config/fleet.yaml`, `launch/fleet_mock.launch.py`, `fleet_manager_node.py`, `core/config.py`, `core/safety.py`, `core/decisions.py`, `core/fleet_core.py`, `core/mission.py`, `core/schemas.py`, `test/test_fleet_core.py`, `test/test_schemas.py`
- 기록 — 위 여섯 PNG와 이 보고서

## 검증

워크스페이스 루트에서 실행한 명령이다.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
PYTHONPATH=src/part1_ui_dashboard:src/part3_fleet_manager \
/usr/bin/python3 -m pytest -p no:cacheprovider src/part1_ui_dashboard/test src/part3_fleet_manager/test -q
```

PART 01 44건, PART 03 249건, 합계 293건 통과 · 0건 실패. 두 패키지의 colcon 빌드도 통과했다. 테스트 35·36은 30 Hz 송신, 5 Hz 저속 송신, 중간에 다시 끊긴 복구, 새 임무의 정지, Robot B의 계속 주행, 실제 침상 이탈, 시작 필수 설정 양쪽, 우회 선택자 기록을 확인한다.

mock 두 대와 화면을 함께 띄우는 명령은 `ros2 launch part1_ui_dashboard demo_mock.launch.py`다. 화면 주소는 [127.0.0.1의 8090 포트](http://127.0.0.1:8090/)다.

두 단계의 미완료 항목은 없다. 실물 웹캠 대신 mock으로 검증했다. BRIEF 11.1절의 간헐적 카메라 실패 중 발행되는 거짓 정상값은 신호가 계속 수신되므로 이 끊김 감시로 구분할 수 없다.
