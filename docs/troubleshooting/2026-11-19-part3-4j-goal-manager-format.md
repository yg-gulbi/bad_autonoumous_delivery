# PART 04 새 goal_manager.py를 preflight가 읽지 못함

- 프로젝트: bad_ws · PART 03 중앙 관제 (`src/part3_fleet_manager`)
- 발생일: 2026-11-19 (main `5b7418f` 대조 중 확인)
- 기록일: 2026-11-19
- 상태: 해결
- 관련 파일·Issue·PR·커밋: `src/part3_fleet_manager/part3_fleet_manager/preflight_checks.py`, `src/part4_amr_nav/hospital_nav/goal_manager.py`(읽기만), `docs/part3/ui/stage4j-report.md`

## 1. 문제정의

### 기술적으로 관찰한 사실

- preflight는 PART 04 `goal_manager.py`를 실행하지 않고 글자로 읽어 `GOALS = {"A": GoalPoint(...), ...}` 사전 리터럴에서 구역 좌표를 꺼냈다.
- main `5b7418f`의 `goal_manager.py`는 방마다 자리 2개를 `SLOTS = {"A": (GoalPoint(..), GoalPoint(..)), ...}`로 두고, `GOALS`는 `{room: slots[0] for room, slots in SLOTS.items()}` 사전 내포식이 됐다. 교차로 `CROSSROAD = GoalPoint(-1.500, -2.180)`도 생겼다.
- 그래서 4I preflight는 "GOALS 사전 형식 확인 필요"로 좌표 대조를 건너뛰고, 2번 자리와 교차로는 비교할 방법이 없었다.

### 원인

- 확인됨: 대조 함수가 `GOALS`의 값이 `ast.Dict`일 때만 읽었다. 내포식(`ast.DictComp`)은 실행해야 값을 알 수 있어 읽지 않는다.

## 2. 해결방법 안들 설정

| 해결안 | 선택 또는 보류 이유 | 결과 |
| --- | --- | --- |
| `goal_manager.py`를 import해서 값 읽기 | 다른 파트 코드를 실행·import하지 않는 규칙(BRIEF 0.2)에 어긋난다 | 미실행 |
| `SLOTS` 사전과 `CROSSROAD`를 글자로 읽고, 없으면 예전 `GOALS` 사전을 읽기 | 실행 없이 새 형식과 예전 형식을 모두 다룬다 | 선택 |

## 3. 해결방법 실행

- AI가 `goal_points()`를 추가했다. `SLOTS`(튜플 안 `GoalPoint` 1~2개), `CROSSROAD`, 예전 `GOALS`를 AST로만 읽고 좌표가 숫자가 아니면 오류로 알린다. `compare_points()`가 1번·2번 자리와 교차로를 `fleet.yaml`과 0.05 m 기준으로 비교한다.
- 파일 맨 위에 `raise SystemExit(1)`이 있는 시험 파일로 실행하지 않음을 확인했다.

### 검증

- 실행한 확인: `test_preflight.py`의 62번 시험 3개. 실제 `src/part4_amr_nav/hospital_nav/goal_manager.py`를 읽어 차이 0건, 2번 자리·교차로 차이 경고, 예전 `GOALS` 형식 읽기.
- 실제 결과: PART 03 426개 통과.
- 남은 한계: 관제 PC의 PART 04 소스가 로봇 PC와 다른 판이면 대조가 맞아도 로봇 동작과 다를 수 있다 (STATE `slot` 줄로 보완).

## 4. 경험의 재해석

### 내가 실제로 경험하고 느낀 것

사용자 경험 미기록.

### AI 해석·제안

- 다른 파트 파일을 글자로 읽는 점검은 그 파트가 코드 모양을 바꾸면 조용히 깨진다. 실패 문구가 경고로만 나와 놓치기 쉽다. PART 04에 좌표를 리터럴로 유지해 달라고 요청하거나, preflight가 "좌표 확인 불가"를 별도 줄로 크게 보이게 하는 것을 제안한다 (제안, 미실행).

## 5. 정리

- 현재 결론: 새 형식과 예전 형식을 모두 읽고, 자리 2개와 교차로까지 대조한다.
- 다시 발생하면: preflight 출력의 "PART 04 좌표 확인 불가" 사유를 보고 `goal_manager.py`의 해당 정의가 리터럴인지 확인한다.
- 다음에 반영할 것: 없음.
