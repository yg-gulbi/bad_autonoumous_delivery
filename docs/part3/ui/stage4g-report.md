# 단계 4G 검증 보고 — 2026-11-19

PART 01 테스트 56개와 PART 03 테스트 333개가 통과했다. 같은 방향으로 복도 안을 주행하던 두 mock에서 앞 A를 정지하자 뒤 B도 멈췄다. A의 재개 명령 후 실제 전진을 확인하자 B가 재개됐다. 반대 방향 시험에서는 A가 E방 안에서 기다렸다.

## 관제 변경

- 기본 배정 모드는 `ROLE`이다. 침상형 이송 로봇(A)은 환자 이송, 약품 운송 로봇(B)은 약품 운송과 호출을 맡는다. 두 번째 호출은 A가 쉬고 있어도 B의 대기열에 남았다. 배터리 배정 모드도 별도로 테스트했다.
- `DIRECTIONAL`에서 같은 방향 보유 로봇의 위치를 복도 축에 투영해 앞뒤를 구한다. 앞 로봇의 안전·운영자 정지, 진전 없음, 장애, 연결 끊김을 감지하면 주행 중인 뒤 로봇에는 PAUSE를 보내고 미출발 MOVE는 보류한다. 구간이 교체된 tick에도 새 구간을 다시 검사한다.
- 새 이벤트는 `CORRIDOR_FOLLOW_STOP`과 `CORRIDOR_FOLLOW_CLEAR`다. `robot`은 뒤 로봇, `detail.front`는 앞 로봇이다. 해제는 두 로봇 사이의 대기가 끝났다는 기록이다. 뒤 로봇에 자체 정지 사유가 남아 있으면 RESUME를 보내지 않는다.
- 화면에 보이는 방향은 관제의 허가 시점 방향이다. 기존 `holders` ID 목록을 이용하던 구독자를 위해 보유 로봇의 방향·상태는 `holder_detail`에 추가했다. 화면은 `follow_stop`과 개별 `pause_causes`로 계속되는 상황을 보여 준다.

## 화면 변경

위쪽 탭 다섯 개, 명령 줄, 상시 상황 줄을 적용했다. P1 왼쪽에 실측 평면도·복도 상태·출발 조건·범례, 오른쪽 380px에 역할 로봇과 작업 표, 아래에 최근 기록 세 줄을 둔다. 경보 확인과 카드 보류는 각각 경보 원인과 열린 카드 상태를 유지한다. 서체는 IBM 공식 배포본 `@ibm/plex-sans-kr` 1.1.0의 완전한 WOFF2 네 굵기와 OFL을 패키지에 포함했다. 로컬 Flask에서 네 파일을 받아 사용한 것을 확인했다.

요청 창과 P3·P4는 같은 폼을 쓴다. 선택 순서, 마지막 구역 교체, 구역 제거, 탭 변경 시 최대 개수에 맞춰 자르기, 응급도, 초기화를 적용했다. 환자·약품 임무는 이 폼에서 실제 mock 관제로 접수했다. Flask 테스트에서도 약품 정류장 1곳과 6곳의 순서 및 품목이 기존 ROS 요청 형식으로 전달됐다.

## 검증

| 대상 | 결과 |
|---|---|
| PART 01 pytest | 56 통과, 0 실패 |
| PART 03 pytest | 333 통과, 0 실패 |
| 반대 방향 동시 요청 44번 보강 | 사용자가 제공한 3개 경로 쌍 × 3개 레벨 조합 모두 한 대만 허가. 레벨이 다르면 높은 쪽을 허가 |
| 47번 | 양방향에서 앞 로봇의 정지·재출발, 뒤 로봇의 개별 안전 정지·장애·막힘·연결 끊김, 미진입 MOVE 및 교체 구간, 장애물 카드가 열리기 전 막힘 감지 확인 |
| colcon | 두 패키지 빌드 성공 |
| 브라우저 | Chrome, 1440×900 및 1280×900. 실제 맵 전환·확대·더블클릭 맞춤, 요청 탭·제출 조건, 경보 확인 후 위험 상황 유지, DB 작업 상세·운영 기록 확인 |

워크스페이스 루트에서 ROS 2 Jazzy와 기존 가상환경을 활성화한 후 실행했다.

```bash
export PYTHONPATH="src/part1_ui_dashboard:src/part3_fleet_manager:${PYTHONPATH}"
python3 -m pytest -q -p no:cacheprovider src/part1_ui_dashboard/test
python3 -m pytest -q -p no:cacheprovider src/part3_fleet_manager/test
colcon --log-base /tmp/bad-stage4g/log build --symlink-install \
  --base-paths src/part1_ui_dashboard src/part3_fleet_manager \
  --packages-select part1_ui_dashboard part3_fleet_manager \
  --build-base /tmp/bad-stage4g/build --install-base /tmp/bad-stage4g/install
```

mock은 발견 서버·슈퍼 클라이언트 환경을 해제하고 `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`, `ROS_DOMAIN_ID=91`, Fast DDS의 127.0.0.1 전용 UDP 프로필로 실행했다. 각 시험은 별도 임시 DB를 사용했다. 재현용 설정 사본에서 A의 시작 위치를 E, B의 시작 위치를 D로 두고 `demo_mock.launch.py`를 띄웠다. 화면 주소는 시험 중 `http://127.0.0.1:8491`이었다.

## 캡처

| 장면 | 캡처 시점에 확인한 값 |
|---|---|
| [종합 관제 평상시](screens/4g-01-normal.png) · [1280 폭](screens/4g-11-normal-1280.png) | A·B 둘 다 복도에서 MOVING. 상황 줄은 이상 없음, 복도 방향은 왼쪽에서 오른쪽 |
| [복도 반대 방향 대기](screens/4g-02-opposite-wait.png) | B가 C행 복도 통행 중, A는 ROOM_E에서 HOLD. 판단 근거는 DIRECTION |
| [같은 방향 뒤 로봇 정지](screens/4g-03-follow-stop.png) | A는 SAFE_PAUSE, B는 HOLD. 두 mock 모두 PAUSED 응답. 이후 A가 전진한 뒤 B의 MOVING 응답까지 확인 |
| [침상 이탈 팝업](screens/4g-04-bed-alarm.png) | 환자 임무 중 이탈 신호. [확인] 후에도 위험 상황 줄이 남았고, 정상 신호와 운영자 재개 후 주행 복구 |
| [막힘 결정 카드](screens/4g-08-blocked-decision.png) | B의 복도 주행 중 mock 장애물 ‘의자’ 분류. BLOCKED 카드의 작업·위치·분류와 선택지 설명 |
| [환자 이송](screens/4g-05-request-patient.png) · [약품 운송](screens/4g-06-request-med.png) · [로봇 호출](screens/4g-07-request-call.png) | 같은 창에서 2개 구역 → 품목이 있는 3개 정류장 → 단일 목적지로 전환 |
| [P2 이송 작업](screens/4g-09-missions.png) · [P5 운영 기록](screens/4g-10-log.png) | 동시 임무 목록과 M-0001 작업 상세, 같은 실행의 DB 이벤트·조작자 기록 |

## 원본과의 차이와 정한 값

- 원본의 예시 지도 비율과 임의 로봇 좌표 대신 실측 벽·문과 mock의 실제 map 좌표를 썼다. 화살표와 점선은 그 좌표에 맞춰 회전·확대된다. 임무·위치·배터리·감시 속도·카드 시간도 수신값을 표시한다.
- Request 원본의 `renderVals()`는 선택한 구역 수로 요약 문장을 바꾼다. 등록 가능 여부에는 v2 사양과 요청 검증에 따라 환자 ID 또는 정류장별 약품 이름까지 반영했다. 예시 환자·약품은 초기값으로 채우지 않는다.
- 막힘 카드의 버튼 제목은 현장에서 쓰는 분류별 문구를 유지했다. 이번 캡처의 선택지는 [치웠음, 다시 출발]이며 아래에 동작 설명을 붙였다.
- 1440×900에서 평상시 최근 기록 세 줄이 보이도록 오른쪽 블록의 안쪽 여백을 12px로 조정했다. 기준 HTML의 14·18px 여백은 4px 단위로 맞췄다.
- 막힘 감지는 기존 관찰 창 `stall.window_s`(기본 5초), 선행 로봇의 전진 재확인은 기존 `stall.move_m`(기본 0.05m)를 쓴다. `mark`가 없던 기존 설정은 로봇 등록 순서로 A부터 보완하고, 명시한 표시는 한 글자·중복 여부를 검증한다.
- 실제 로봇의 제동 거리와 PAUSE 응답 지연은 이번 격리 mock 검증의 측정 범위 밖이다. 요청된 구현·캡처에 남은 항목은 없다.

## 변경 파일

- PART 01 — `setup.py`, `history.py`, `ui_api.py`, `static/index.html`, `style.css`, `app.js`, `map.js`, `pages/{dashboard,forms,missions,log}.js`, `static/fonts/`의 WOFF2 4개 및 OFL, `test/{test_event_sentences,test_history,test_ui_routes}.py`.
- PART 03 — `config/fleet.yaml`, `core/{config,fleet_core,mission,resource_manager,schemas}.py`, `test/{test_config,test_corridor_yield,test_field_traffic,test_fleet_core}.py`.
- 문서 — `BRIEF.md`, `WEB_DB_DESIGN.md`, `DECISIONS.md`, `STAGES.md`, 이 보고서, 위 캡처 11장, [막힘 카드의 재출발 회귀와 수정](../troubleshooting/2026-11-19-follow-stop-resume.md).
