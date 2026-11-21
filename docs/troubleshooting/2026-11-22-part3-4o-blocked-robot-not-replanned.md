# 막힘(BLOCKED) 카드에서 다시 출발·되돌아가기를 골라도 로봇이 움직이지 않음 (PLAN)

- 프로젝트: bad_ws PART 03 (`part3_fleet_manager`)
- 발생일: b98f6ca(태그 `4o-dual-mode`)의 PLAN부터. 2026-11-22 4O 정리 중 LEGACY 시험을 PLAN으로 옮기며 발견
- 기록일: 2026-11-22
- 상태: 보류 (정리 작업은 동작을 바꾸지 않는 조건이라 고치지 않음)
- 관련 파일·Issue·PR·커밋: `src/part3_fleet_manager/part3_fleet_manager/core/plan_replan.py` (`_agent`의 `static`), `core/fleet_core.py` (`_unblock`), `test/test_fleet_core.py::test_blocked_choices_respect_protocol_state[retry|return]`, `test/test_4j_obstacle_report.py::test_57_box_card_return_unblocks_when_the_robot_moves_again` (`xfail`)

## 1. 문제정의

### 기술적으로 관찰한 사실

시험 하네스(`fleet_simulation`)에서 로봇이 복도에서 30초 멈춰 `BLOCKED` 카드가 열린 뒤 운영자가 [확인함, 다시 출발](`retry`, 로봇 STATE `PAUSED`)이나 [되돌아가기](`return`)를 고르면, LEGACY에서는 `RESUME` 또는 이전 구역으로 가는 `MOVE`가 나갔다. PLAN에서는 40초가 지나도 아무 명령도 나가지 않았다. 의료용 상자 카드의 [되돌아가기]도 같았다. 태그 `4o-dual-mode`에서 PLAN으로 돌려도 똑같다(정리 전부터 있던 동작).

### 원인

확인한 범위: 막힌 로봇은 `RobotReport.stalled`가 참이라 계획에서 움직일 수 없는 로봇(`static`)이 되고 계획이 없다. 카드 선택으로 막힘이 풀려도 계획을 다시 세울 계기(`_mark`)가 없다. "계획보다 늦음"은 계획이 있는 로봇만 보므로 그것도 일어나지 않는다. 그래서 `go`(출발 허가)를 받지 못한다.

## 2. 해결방법 안들 설정

| 해결안 | 선택 또는 보류 이유 | 결과 |
| --- | --- | --- |
| 막힘 해제(`_unblock`)·카드 선택 때 `_mark("막힘 해제")` | 원인에 직접 맞는 작은 변경으로 보임 | 미실행 (4O 정리는 동작을 바꾸지 않음) |
| 시험을 PLAN 동작대로 고쳐 통과시키기 | 틀린 동작을 시험으로 굳히게 됨 | 보류 |
| 시험을 `xfail(strict=True)`로 사유와 함께 남기기 | 고치면 바로 드러나고, 지금 동작이 기록됨 | 적용 |

## 3. 해결방법 실행

AI가 시험을 옮기다 발견하고, 태그 트리에서 같은 장면을 돌려 정리 전부터 있던 동작임을 확인했다. 세 시험을 사유를 적어 `xfail(strict=True)`로 두었다.

### 검증

- 실행한 확인: 같은 장면을 태그 트리(PLAN)와 정리 뒤 트리에서 실행
- 실제 결과: 두 트리 모두 카드 선택 뒤 명령 없음
- 남은 한계: kinematic mock·실기에서 같은 일이 나는지는 확인하지 않음 (kinematic mock 다섯 막 시험에서는 상자를 치우고 [다시 출발]하면 움직였다. 그 장면은 로봇 위치가 실제로 바뀌어 막힘이 스스로 풀림)

## 4. 경험의 재해석

### 내가 실제로 경험하고 느낀 것

사용자 경험 미기록.

### AI 해석·제안

현장에서 운영자가 [다시 출발]을 눌렀는데 로봇이 서 있으면 버튼이 듣지 않는 것처럼 보인다. 다음 작업에서 막힘 해제를 다시 계획할 계기로 넣고, `xfail`을 지우는 것을 제안한다.

## 5. 정리

- 현재 결론: 미해결 (보류)
- 다시 발생하면: `PlanManager.plans`에 그 로봇이 있는지, `dirty`가 None인지 확인
- 다음에 반영할 것: 제안 — 막힘 해제 때 다시 계획
