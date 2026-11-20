# 4H 현장 반영 보고서

11/19 · PART 01·03 · 작업 브랜치 `feat/part3-4g`

복도에서 침상형 이송 로봇의 STATE가 MOVING인 동안, 뒤따르던 약품 운송 로봇은 축 간격 0.9996 m에서 PAUSE를 수락했다. 홀에서 실시한 별도 시험에서는 정차한 침상형 로봇에 약품 로봇이 0.590 m까지 접근했을 때 주행 중인 약품 로봇만 멈췄다. 두 값은 격리 mock 관제 DB의 정지 이벤트에서 읽었다.

## 동작과 설정값

| 판단 | 정지 또는 출발 보류 | 해제와 운영자 선택 |
|---|---|---|
| 같은 방향 복도 | `DIRECTIONAL`에서 복도 축 간격 ≤ 1.0 m. 아직 출발하지 않은 뒤 로봇은 진입할 입구를 기준으로 판단한다. 앞 로봇의 정지 보고도 계속 적용한다. | 축 간격 ≥ 1.4 m 또는 선행 이탈. 정지 보고도 있었다면 전진이나 이탈을 확인한다. `CORRIDOR_FOLLOW_STOP`의 `rule`로 GAP과 FOLLOW를 구분한다. |
| 목적지 점유 | A~F 목적지 중심 반경 ≤ 0.6 m에 다른 로봇이 있거나 먼저 허가한 구간이 그곳으로 향하면 첫 MOVE를 보류한다. 홈 복귀와 허가 뒤 아직 출발하지 않은 구간도 확인한다. | 점유가 풀리면 자동 출발한다. 30초에 [다시 확인]·[되돌아가기]·[보류] 카드가 열린다. 다시 확인해도 여전히 점유 중이면 기다린다. |
| 근접 | 모든 구역에서 중심 간 거리 < 0.6 m이면 주행 중인 로봇만 PAUSE한다. 두 대가 움직이면 둘 다 세운다. | 두 로봇의 연결이 살아 있고 거리 ≥ 0.9 m이면 이 사유를 해제한다. 10초에 [상대 로봇 먼저 보내기]가 있는 카드가 열린다. 범퍼·운영자·후행 정지 등은 각자의 해제 조건을 따른다. |

요청에서 위치가 지정되지 않은 목적지 반경 설정은 `resources.zone_occupied_m`에 두었다. 정지와 재개 거리는 각각 `corridor.follow_gap_m`·`follow_resume_m`, `safety.proximity_stop_m`·`proximity_resume_m`이다. 재개 거리가 정지 거리보다 커야 설정을 읽는다.

[상대 로봇 먼저 보내기]는 양보할 로봇의 정지 응답을 확인한 뒤 선택한 상대의 한 구간에만 적용한다. 상대가 IDLE이면 운영자가 [보내기]에서 비킬 구역을 고른다. 근접 범위에서 다시 가까워지면 재정지한다. 출발 직전에 남은 정지 사유를 함께 검사하므로, 하나의 해제 이벤트가 곧바로 RESUME를 뜻하지 않는다.

## 화면 검증

발견 서버·슈퍼 클라이언트 환경 변수를 해제하고 `LOCALHOST`, 도메인 93, Fast DDS의 127.0.0.1 전용 전송으로 관제·화면·mock 두 대를 실행했다. HTTP도 127.0.0.1에 연결했다. 캡처 해상도는 모두 1440 × 900이다.

| 장면 | 화면에서 조작하고 확인한 결과 |
|---|---|
| [복도 간격 정지](screens/4h-01-gap-stop.png) | A는 E에서 C로, B는 D에서 F로 주행했다. 앞 A의 mock 속도는 0.18 m/s, 뒤 B는 0.50 m/s로 두었다. A는 MOVING, B는 HOLD / PAUSED였다. |
| [목적지 대기](screens/4h-02-zone-occupied.png) | D에 B가 쉬는 동안 A에 D 탑승·C 인계 작업을 주었다. A가 E에 머물고 지도에 ‘목적지 대기’, 상황 줄에 B를 옮기라는 안내가 나왔다. [판단 근거]에서 두 로봇과 목적지를 확인했다. |
| [근접 정지](screens/4h-03-proximity.png) | 홀에서 B가 주행하는 경로 옆에 A를 세웠다. 이 장면만 임시 설정의 D 좌표를 (−1.79, 0.03)으로 두었다. B에 주황 테두리와 ‘가까움 정지’가 표시됐고 A는 IDLE이었다. 카드에서 A 먼저 보내기를 고르고 A를 E로 보내자 B는 근접과 후행 대기가 모두 풀린 후 MOVING으로 돌아왔다. |
| [로봇 블록의 가는 곳](screens/4h-04-robot-dest.png) | D에 있던 B를 응급실(A)으로 보낸 뒤 환자 로봇이 자동 출발했다. 로봇 블록에는 ‘간호 스테이션(D) (환자 탑승)’, 작업 표에는 전체 경로 ‘D → C’가 보인다. |
| [전체 정지 중 상황 줄](screens/4h-05-pause-all-lane.png) | 목적지 대기 중 전체 정지를 누르면 ‘운영자 전체 정지’가 맨 앞, ‘외 1건’이 옆에 남는다. 근접 장면에서도 전체 정지와 침상 이탈을 차례로 겹쳐 침상 경보 → 운영자 정지 → 근접 정지 순으로 표시가 돌아오는 것을 확인했다. |

가까운 로봇이 구역 점을 덮었을 때 구역 이름이 상태 글자와 겹쳐 보였다. 구역 이름의 좌우 후보 자리에 로봇 표시 폭을 확보한 뒤 다시 촬영했다.

## 테스트와 빌드

| 대상 | 통과 | 실패 |
|---|---:|---:|
| PART 01 전체 | 56 | 0 |
| PART 03 전체 | 359 | 0 |
| 합계 | **415** | **0** |

저장소 루트에서 Jazzy와 관제용 가상환경을 적용하고 실행했다.

```bash
export PYTHONPATH="src/part1_ui_dashboard:src/part3_fleet_manager:${PYTHONPATH}"
python3 -m pytest -p no:cacheprovider -q src/part1_ui_dashboard/test
python3 -m pytest -p no:cacheprovider -q src/part3_fleet_manager/test
colcon --log-base /tmp/bad-stage4h/log build --symlink-install \
  --base-paths src/part1_ui_dashboard src/part3_fleet_manager \
  --packages-select part1_ui_dashboard part3_fleet_manager \
  --build-base /tmp/bad-stage4h/build --install-base /tmp/bad-stage4h/install
```

두 패키지가 빌드됐다. 가짜 시계로 47~49번의 거리 경계와 카드 시간, 목적지 예약·도착 후 실점유·홈 복귀, 정지 사유가 서로 다른 순서로 풀리는 경우를 검사했다. 새 이벤트 네 종류의 실시간 문장과 SQLite 기록 문장도 비교했다.

기존 범퍼 시험은 선행 로봇이 1.4 m 재개 간격을 확보한 뒤 후행 로봇의 개별 재개를 검사하도록 바꿨다. 선행 막힘 시험은 같은 방향의 서로 다른 목적지 C와 F를 사용한다. 교착 대기 시간 집계는 목적지 대기와 자원 대기가 겹친 시간을 한 번만 센다.

## 변경 파일

- PART 03 설정·판단 — `config/fleet.yaml`, `core/config.py`, `core/collision.py`(신규), `core/resource_manager.py`, `core/fleet_core.py`, `core/mission.py`, `core/decisions.py`, `core/schemas.py`, `core/store.py`.
- PART 03 시험 — `test/test_field_traffic.py`, `test/test_config.py`.
- PART 01 화면·기록 — `static/app.js`, `static/map.js`, `static/pages/dashboard.js`, `static/pages/log.js`, `static/style.css`, `ui_api.py`, `history.py`, `test/test_event_sentences.py`.
- 문서 — `BRIEF.md`, `WEB_DB_DESIGN.md`, `DECISIONS.md`의 D-27, `STAGES.md`, 이 보고서와 위의 캡처 다섯 장.

## 실기에서 확인할 것

- 기존 [현장 시험 절차](../REAL_TEST.md)에 따라 두 로봇의 map 좌표 정합과 정차 중 마지막 위치를 확인한다. 실제 운행 속도에서 GAP 이벤트, PAUSE 수락, 물리적 정지까지 걸린 시간과 최소 차체 간격을 함께 기록한다.
- 입구와 방 문에서 1.0 / 1.4 m가 충분한지, 홀 정류장과 홈의 0.6 m 반경이 도착 오차를 포함하는지 확인한다. 근접 정지 후 0.9 m에서 떨림 없이 재개하는지도 본다.
- PART 04가 MOVING으로 보고하는 자체 정지, 운영자 정지와 근접 정지의 중첩, 범퍼 이벤트 수신 및 개별 재개를 실제 로봇으로 확인한다. 위 캡처와 수치는 모두 mock 시험 결과다.
