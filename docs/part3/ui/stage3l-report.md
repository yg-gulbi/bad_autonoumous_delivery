# 단계 3L · 관제 DB와 기록 조회

## 화면과 시나리오 5

[시연 화면](http://127.0.0.1:8090/)에서 CALL 세 건을 연속 등록했다. Robot A와 Robot B가 한 건씩 받고, 대기 중이던 세 번째 호출은 먼저 끝난 Robot A가 맡았다. 첫 수령에서는 [불일치] → [다시 확인]을 거쳐 카드 선택 기록도 남겼다. 세 임무가 모두 끝나고 두 로봇이 홈에서 대기하는 것까지 확인했다.

| 임무 | 로봇 · 목적지 | 도착 시각 | 확인 시각 | 확인한 사람 |
|---|---|---|---|---|
| M-0001 | Robot A · 응급실(A) | 16:28:09.270 | 16:28:09.670 | 기록 검증 |
| M-0002 | Robot B · 102호 병실(B) | 16:28:14.470 | 16:28:14.770 | 기록 검증 |
| M-0003 | Robot A · 101호 병실(E) | 16:28:16.971 | 16:28:17.071 | 기록 검증 |

시각은 2026-11-18 한국 시각이다. 세 임무는 모두 `DONE`, 각 정류장은 `CONFIRMED`였다. 관제와 두 mock을 계속 실행한 채 화면 서버를 종료하고 새 프로세스로 띄웠다. 재시작 전후 세 임무 상세 API 응답(정류장과 전체 이벤트 포함)이 일치했다. 재시작 후 화면 서버 메모리의 이벤트가 0건인 상태에서 DB 조회는 완료 3건, 불일치를 포함한 확인 이벤트 4건, 운영자가 선택한 카드 1건을 반환했다.

| 캡처 | 확인 항목 |
|---|---|
| [완료 탭](screens/15-db-completed.png) | CALL 세 건과 배정 로봇 |
| [임무 상세](screens/16-db-stops.png) | 품목, 도착·확인 시각, 확인자와 임무 기록 |
| [운영 기록](screens/17-db-events.png) | M-0001로 거른 이벤트, 수령 확인·카드 선택의 조작자 |
| [화면 서버 재시작 후 상세](screens/18-db-after-ui-restart.png) | 같은 임무, 정류장, 시각, 확인자 |
| [재시작 후 결정 카드](screens/19-db-cards.png) | 확인 불일치 카드, 열린·닫힌 시각, ‘다시 확인’, 선택자와 닫힌 이유 |

## DB와 조회

- 관제가 기존 SQLite 파일에 열, 인덱스, `mission_stops`를 추가한다. 한 tick의 임무·정류장·이벤트·카드 변경은 같은 트랜잭션으로 저장한다. `stops_json`과 기존 임무 ID를 보존한다. 재전송된 `req_id`는 관제 재시작 후에도 처음 임무 ID로 응답한다.
- 차트번호가 같은 환자는 한 행에 갱신한다. 옛 DB에 같은 번호로 여러 행이 있으면 기존 임무를 대표 환자 ID에 연결한다. 이전 중복 행과 ID는 보존하고 원래 번호를 `legacy_chart_no`에 옮긴다. 과거 도착·확인 기록은 원래 이벤트로 복원하며, 원본에 없는 시각·확인자는 미기록으로 둔다. 실제 시연 DB의 기존 이벤트 9건이 모두 보존됐고 SQLite 무결성·외래키 검사도 통과했다.
- PART 01은 요청마다 URI `mode=ro`로 연결한다. L14의 기록 주소 일곱 개를 제공한다. 완료·취소 임무, 정류장·임무 이벤트, 최근 환자 이송 10건, 운영 이벤트·카드·기간 지표와 CSV가 DB를 읽는다. CSV는 같은 검색 조건에서 최대 10,000행, 목록은 쪽당 50행이다. 진행 중·대기 임무와 운행 제어는 실시간 상태를 사용한다.
- 기간 필터는 날짜 또는 epoch 초를 받는다. 날짜 입력의 종료일은 그날 전체를 포함한다. 교착 평균은 고른 기간 안에 감지되고 해소된 건을 대상으로 계산한다. DB가 없으면 기록 API가 503을 반환하고 대상 표에 ‘기록 DB에 연결할 수 없습니다’를 표시한다.

## 설계서와 기존 코드의 차이 및 구현 결정

| 항목 | 적용한 동작 |
|---|---|
| 임무 상태 | 실제 임무 행은 `QUEUED`, `ACTIVE`, `DONE`, `CANCELED` 네 값이다. 검증 실패는 L2처럼 `MISSION_REJECTED` 이벤트와 오류 응답을 남긴다. 3.3절 도식의 `REJECTED` 임무 행은 생기지 않는다. 새 정류장의 상태 이름은 3.3절과 같다. |
| 배정과 출발 | ROLE에서 미리 담당 로봇이 정해진 대기 건, 복귀 취소 응답을 기다리는 새 임무는 로봇 ID가 있어도 일시적으로 `QUEUED`일 수 있다. `assigned_at`은 로봇 선정, 새 임무의 `started_at`은 첫 구간 수락(`LEG_START`) 시각이다. 구버전에서 이미 쓴 `started_at`은 보존했다. |
| 취소와 복귀 | 기존 명령에는 자유 입력하는 취소 이유가 없어 관제가 처리한 사유를 저장한다. 기존 운행 로직은 정상 완료 뒤 홈 복귀를 시작한다. 취소 뒤에는 정지 확인 후 다음 배정을 처리한다. |
| 운영자 직접 이동·카드 | 기존 `GO_ZONE` 기록은 임무 테이블에 남고, 임상 작업 목록에서는 환자 이송·물품 전달·호출만 조회한다. 기존 카드 ID는 `K-`와 UUID, `cards.robots`는 JSON 배열 문자열 형식을 유지한다. |
| 조작자 | 폼과 결정은 화면의 조작자 이름을 기록한다. 전체 정지·재개는 인자 없는 ROS `Trigger`여서 관제에 이름이 전달되지 않아 `events.by`는 NULL이다. 자동 판단도 NULL이다. |
| 웹 요청 | 기존 명령 POST는 요청 참조를 돌려주고 브라우저가 SSE로 같은 참조의 ACK를 최대 3초 기다린다. 설계 4.0의 단일 HTTP 응답 방식과 다르다. |
| 문서 참조·표시명 | BRIEF 14.5절에 적힌 L17은 정본 4.15절의 L14를 가리킨다. 운영 기록의 이벤트 이름 목록에는 갱신된 BRIEF의 장애물 확인 요청·결과 표시명도 포함했다. |

## 테스트와 파일

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
PYTHONPATH=src/part1_ui_dashboard:src/part3_fleet_manager \
/usr/bin/python3 -m pytest -p no:cacheprovider src/part1_ui_dashboard/test src/part3_fleet_manager/test -q
```

PART 01 48건, PART 03 252건, **총 300건 통과 · 0건 실패**. 두 패키지의 colcon 빌드도 통과했다. 테스트 37은 정류장 생명주기·취소·요청 재전송·기존 DB 이전을 확인한다. 테스트 38은 실제 임시 DB에서 검색·쪽 나눔·기간 지표·CSV 제한·읽기 전용·파일 부재 동작을 확인한다. 기존 테스트 두 곳의 기대값을 테이블 5개와 동일 차트번호 환자 1행으로 갱신했다.

| 위치 | 만든·바꾼 파일 |
|---|---|
| PART 03 `part3_fleet_manager/core/` | `store.py`, `mission.py`, `fleet_core.py`, `decisions.py`, `deadlock.py`, `safety.py` |
| PART 03 `test/` | `test_store.py`, `test_fleet_core.py` |
| PART 01 `part1_ui_dashboard/` | `history.py`(신규), `web.py`, `ui_api.py`, `ui_server_node.py` |
| PART 01 `part1_ui_dashboard/static/` | `history.js`(신규), `index.html`, `app.js`, `start.js`, `style.css`, `pages/missions.js`, `pages/forms.js`, `pages/log.js` |
| PART 01 launch·테스트 | `launch/ui.launch.py`, `launch/demo_mock.launch.py`, `test/test_history.py`(신규) |
| 문서 | 이 보고서와 위 캡처 5장 |

두 패키지의 설치 환경에서 시연 명령은 `ros2 launch part1_ui_dashboard demo_mock.launch.py`다. 이번 검증은 화면 서버만 재시작하기 위해 관제·mock과 화면을 별도 launch로 띄웠다. 단계 3L에 남은 차단 사항은 없다.
