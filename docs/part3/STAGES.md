# 단계 기록과 남은 일 (PART 01·03)

담당 김준성 · 2026-11-19 정리

- 지금 사양은 [`BRIEF.md`](BRIEF.md)와 [`WEB_DB_DESIGN.md`](WEB_DB_DESIGN.md)에 있다. 이 문서는 무엇을 어떤 순서로 만들었고 어떻게 확인했는지, 그리고 남은 일만 적는다.
- 각 단계는 코딩 에이전트가 하고, 끝날 때마다 멈추고 보고한 뒤 사람이 확인했다. 보고서는 `docs/part3/ui/`에 있다.

## 1. 끝난 단계

| 단계 | 날짜 | 한 일 | 확인한 것 | 보고서 |
|---|---|---|---|---|
| 0 | 11/18 | 패키지 뼈대, `fleet.yaml`, `config`·`geometry`·`schemas` | 영역 판정·JSON 검증 테스트 | — |
| 1 | 11/18 | `route_planner`, `resource_manager`(복도 양보 포함), `deadlock` | 테스트 1~8, 21~24 | — |
| 2 | 11/18 | `mission`, `stall`, `safety`, `decisions`, `store`, `robot_protocol`, `fleet_core` | 테스트 9~20, 25, 26 | — |
| 2b | 11/18 | 팀 기본 흐름: `CALL` 임무, 배정 모드(`IDLE_BEST_BATTERY`·`ROLE`), 홈 복귀 | 테스트 27, 28 | — |
| 3 | 11/18 | `fleet_manager_node`, `robot_link`, `mock_hospital_nav`, launch | `ros2 launch`로 관제 + mock 2대, 시나리오 4개를 `/fleet/state`로 확인 | [`stage3-scenarios.md`](stage3-scenarios.md) |
| 3U | 11/18 | 간이 관제 화면 (PART 03 안 `monitor/`) | 화면으로 시나리오 진행 | — (3D에서 PART 01로 옮기고 지움) |
| 3D | 11/18 | PART 01 관제 화면: 팀 디자인대로 5개 페이지, 실제 맵, 판단 근거, 결정 카드 창, 경보 창, 시험 도구. 관제 쪽 화면용 필드, 복귀-취소 경합 수정, mock 제어 토픽 | 테스트 29~34. 시나리오 1~5를 화면 클릭만으로 진행 | [`ui/stage3d-report.md`](ui/stage3d-report.md) |
| 3D-2 | 11/18 | 지도 바로잡기: 실제 맵에서 잰 평면도, 기울기 보정, 복도 다각형을 벽에 맞춤, "복도" 용어, 환자 이름 선택 항목 | 테스트 33(새 판). 지도가 `ui/design/04-map-target.png`와 같은 꼴 | [`ui/stage3d2-report.md`](ui/stage3d2-report.md) |
| 3D-3 | 11/18 | 지도 글자 자리 정하기, 화면·관제 문장 "복도"로 통일 | 평상시·양보 중·막힘 캡처에서 글자 겹침 없음 | [`ui/stage3d3-3s-report.md`](ui/stage3d3-3s-report.md) |
| 3S | 11/18 | 침상 감시 끊김: 감지·정지·카드·감시 없이 운행, 화면 배지, 가짜 침상 감시, PART 05 요청 문서 | 테스트 35, 36. 화면에서 끊김 → 정지 → 카드 → 복구 → 재개. 합계 293 통과 | [`ui/stage3d3-3s-report.md`](ui/stage3d3-3s-report.md) |
| 3L | 11/18 | 화면·DB·로직 설계 반영: `mission_stops`, 칸 정리, 화면 서버의 읽기 전용 기록 조회 | 테스트 37, 38. 시나리오 5 뒤 화면 서버를 껐다 켜도 기록이 DB에서 보임. 300 통과 | [`ui/stage3l-report.md`](ui/stage3l-report.md) |
| 3O | 11/18 | 3L 검토 두 건(기록 문장, 전체 정지·재개의 누른 사람) + PART 06 장애물 분류 연결, mock 분류기, 화면 표시 | 테스트 39~42. 막힘 시나리오를 사람·물건·없음으로 한 번씩. 325 통과 | [`ui/stage3o-report.md`](ui/stage3o-report.md) |
| 4P | 11/18 | 실제 연결 준비: `real.launch.py`, `preflight`, `REAL_TEST.md`, 막힘 카드 버튼 글 | 격리 도메인에서 preflight가 mock 두 대 OK, mock 끄면 12개 누락, `keyboard_control` 감지. 328 통과 | [`ui/stage4p-report.md`](ui/stage4p-report.md) |

- 11/18 PR #2·#3으로 main에 합쳤다 (화면·DB·장애물 분류·실제 연결 준비, README 갱신).
- 11/18 강의실 관제 PC(`mu-03`)에 새로 설치해 가짜 로봇 시연과 `real.launch.py` 실행까지 확인했다. 실제 로봇 토픽은 아직 이 PC에서 보이지 않았다 (주행 노드가 꺼져 있었거나 연결 설정 문제. preflight 결과 로봇 항목 모두 없음).
- 11/19 문서 정리: BRIEF는 지금 사양만 남기고 단계 기록·결정 이유를 이 문서와 `DECISIONS.md`로 나눴다. 화면 사양(BRIEF 14장)은 `WEB_DB_DESIGN.md`로 합쳤다.

## 2. 지금 하는 단계

| 단계 | 내용 | 끝났다는 기준 |
|---|---|---|
| 4 | 실제 로봇 연결 (PART 04와 함께). `REAL_TEST.md` 순서대로 | preflight "실제 실행 가능" → 실제 로봇 2대로 한 대 짧은 이동, 정지·재개, 호출 1건, 시나리오 1(두 대 동시), 시나리오 2(복도 양보)를 화면에서 진행. DB와 캡처를 남김 |

## 3. 남은 일

### 3.1 코드 정리 (11/19 구조 검토 결과)

외부 검토(`part3_structure_review.pdf`, 11/19)가 짚은 구조 문제를 위험도 순으로 나눴다. 이유는 `DECISIONS.md` D-20.

| 시점 | 할 일 | 위험 |
|---|---|---|
| 지금 (실제 시험과 병행 가능) | 로봇 ID·동점 순서(`"robot_a"`·`"robot_b"` 하드코딩 4곳)를 설정으로, `fleet_core._acks_seen` 상한, 쓰이지 않는 `blocked_by`·`ICD_GOAL_POSE` 제거, 끝난 임무 표시 건수 20을 설정으로, 반복문 변수 `kind` 재사용 정리 | 낮음 |
| 실제 시나리오 1·2 통과 뒤 | `fleet_core`의 멈춤·장애물 판단(약 90줄)을 `stall` 쪽으로 옮기기, 이벤트 기록을 `resource_manager.record()`에서 떼어 별도 기록부로 | 중간 (범위 한정) |
| 시연 준비가 끝나고 시간이 남으면 | "판단 모듈은 결과만 돌려주고 서로 부르지 않는다" 전면 개편 (직접 호출 약 95곳) | 높음 (이벤트 순서·같은 tick 연쇄가 바뀜) |

- 같은 검토가 다음에 보겠다고 한 **교착 흐름이 현장(Nav2 정체 → 화면 → 결정 카드)과 맞는지**는 단계 4 실제 시험에서 확인한다.

### 3.2 다른 파트 대기

- PART 04: R1~R4 (`PART04_REQUESTS.md`). 특히 R3(도크에서 바로 출발)이 없으면 시험 전 로봇을 손으로 떼어 둔다.
- PART 05: P5-R1 (`PART05_REQUESTS.md`).
- PART 06: BRIEF 11.3절 질문 (어디서 띄우나, 요청 짝짓기 값, 라벨 범위).

### 3.3 팀 결정 대기

- 구역 이름 (A~F ↔ 접수 구역·촬영실·병실 …), 침상 이탈 때 멈출 로봇, 대기·막힘·양보 시간값 (BRIEF 12장).
- 팀 피그마의 화면·로직 그림(김주환)과 `WEB_DB_DESIGN.md` 맞추기: 로봇 수동 선택 여부, 버튼 1~8·경광등이 화면인지 장치인지.
- `AGENTS.md`의 PART 01 설명(`rosbridge`, 미확정) 갱신 — 팀 문서라 담당자가 고친다.
