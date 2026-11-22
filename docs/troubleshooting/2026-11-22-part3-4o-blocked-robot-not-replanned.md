# 막힘(BLOCKED) 카드에서 다시 출발·되돌아가기를 골라도 로봇이 움직이지 않음 (PLAN)

- 프로젝트: bad_ws PART 03 (`part3_fleet_manager`)
- 발생일: b98f6ca(태그 `4o-dual-mode`)의 PLAN부터. 2026-11-22 4O 정리 중 LEGACY 시험을 PLAN으로 옮기며 발견
- 기록일: 2026-11-22 (4O 후속 6에서 해결)
- 상태: 해결
- 관련 파일·Issue·PR·커밋: `src/part3_fleet_manager/part3_fleet_manager/core/fleet_core.py` (`_choose`의 BLOCKED, `_stalled`, `block_legs`), `core/plan_board.py` (`unblock_robot`), `core/obstacle_report.py` (`_block`), `test/test_fleet_core.py::test_blocked_choices_respect_protocol_state`, `test/test_4j_obstacle_report.py::test_57_box_card_return_unblocks_when_the_robot_moves_again`, `test/test_4o_followup6.py::test_box_card_answer_gets_the_robot_going`, `docs/part3/DECISIONS.md` D-67

## 1. 문제정의

### 기술적으로 관찰한 사실

시험 하네스(`fleet_simulation`)에서 로봇이 복도에서 30초 멈춰 `BLOCKED` 카드가 열린 뒤 운영자가 [확인함, 다시 출발](`retry`, 로봇 STATE `PAUSED`)이나 [되돌아가기](`return`)를 고르면, LEGACY에서는 `RESUME` 또는 이전 구역으로 가는 `MOVE`가 나갔다. PLAN에서는 40초가 지나도 아무 명령도 나가지 않았다. 의료용 상자 카드의 [되돌아가기]도 같았다. 태그 `4o-dual-mode`에서 PLAN으로 돌려도 똑같다(정리 전부터 있던 동작).

### 원인

막힌 로봇은 `RobotReport.stalled`가 참이라 계획에서 움직일 수 없는 로봇(`static`)이 되고 계획이 없다. 카드 선택으로 막힘이 풀려도 계획을 다시 세울 계기(`_mark`)가 없다. "계획보다 늦음"은 계획이 있는 로봇만 보므로 그것도 일어나지 않는다. 그래서 `go`(출발 허가)를 받지 못한다. [되돌아가기]는 새 구간을 요청했지만, `stalled`가 "막힘 기록이 있는 동안"으로 정해져 있어 새 구간도 움직일 수 없는 로봇으로 계획됐다.

## 2. 해결방법 안들 설정

| 해결안 | 선택 또는 보류 이유 | 결과 |
| --- | --- | --- |
| 카드 종류별로 계획 쪽에 분기(retry면 RESUME, return이면 MOVE) | 요구 사항이 금지("막힘 해제와 새 목표 두 가지로만") | 보류 |
| 운영자 선택을 계획의 입력 두 가지로: [다시 출발] = 막힘 해제(`unblock_robot`, `_mark("막힘 해제")`), [되돌아가기] = 새 목표(`mission.retry(back=True)`의 새 구간 요청) | 계획은 "막힘이 풀렸다"와 "새 구간이 생겼다"만 알면 된다 | 적용 |
| 막힘은 그 막힘이 생긴 구간만 묶음(`block_legs`, `_stalled`) | [되돌아가기]의 새 구간은 막힌 구간이 아니므로 계획된다. 복도 `blocked` 표시는 로봇이 실제로 움직일 때까지 남는다(test_57이 기대하는 동작) | 적용. 처음에는 return에서도 막힘을 풀었는데, 복도 표시가 바로 지워져 test_57이 실패해 바꿨다 |

## 3. 해결방법 실행

AI가 4O 후속 6에서 위 두 입력과 구간별 막힘을 넣었다. 장애물 보고(`obstacle_report._block`)로 생긴 막힘도 같은 규칙을 쓰도록 `block_legs`를 적는다. 시험 하네스는 계획 작업이 진행 중이거나 다시 계획할 일이 남아 있으면 같은 시각에 한 tick을 더 돈다(카드 선택 → 다시 계획 → 반영이 두 tick 걸리므로).

무작위 시험의 운영자 동작도 고쳤다. 예전에는 Nav2 실패(FAULT) 로봇을 PART 06 판정 전에 바로 RETRY해서 확인한 시드(0·1·5·7)에서 장애물 카드가 한 번도 열리지 않았다. 이제 판정과 카드를 기다리고, 막힘 카드에 [다시 출발]·[되돌아가기]를 무작위로 고른다.

### 검증

- 실행한 확인: xfail 3개를 지우고 통과. kinematic mock 장면(상자 → Nav2 포기 → medical_box → 카드 → retry / return → RETRY) 2개. 무작위 시드 300개에서 막힘 카드 선택 뒤 MOVE·RESUME까지의 시간 검사(60 s 넘으면 실패)
- 실제 결과: 4O 보고서 17절 참고. 길이 비어 있으면 선택 뒤 0.6 s에 첫 명령이 나갔고(시드 0·1·2·5·7), 300개 전체의 최대는 약 12 s(다른 로봇 대기, 침상 이탈 등)
- 남은 한계: 실기에서는 확인하지 않음

## 4. 경험의 재해석

### 내가 실제로 경험하고 느낀 것

사용자 경험 미기록.

### AI 해석·제안

무작위 시험이 (확인한 시드에서) 장애물 카드를 한 번도 답하지 않고 있었다는 것이 더 큰 발견이었다. 시험이 그 경로를 지나는지(카드 종류별 선택 횟수)를 통계로 남겨 두는 편이 낫다고 본다.

## 5. 정리

- 현재 결론: 해결 (D-67)
- 다시 발생하면: `FleetCore.blocks`·`block_legs`와 지금 구간(`_leg`)이 같은 객체인지, `PlanManager.dirty`에 "막힘 해제"나 "새 구간 요청"이 들어왔는지 확인
- 다음에 반영할 것: 없음
