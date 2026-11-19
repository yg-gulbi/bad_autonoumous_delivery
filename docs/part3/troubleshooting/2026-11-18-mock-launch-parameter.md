# mock 속도 인자를 지정하면 Jazzy launch가 종료됨

bad_ws PART 03 · 2026-11-18 발생 및 기록 · 해결

## 1. 문제정의

`mock_speed:=0.6`을 넘기자 launch가 노드를 시작하기 전에 `value='0.6' is not an instance of <class 'float'>` 오류로 종료됐다. `LaunchConfiguration.perform()`의 결과 문자열을 `ParameterValue`에 직접 전달한 것이 원인이었다.

## 2. 해결안

문자열 값은 인자 지정 여부를 확인하는 데 쓰고, 실제 파라미터 변환에는 원래 launch substitution을 전달한다. 속도와 R1 지원 여부에 같은 규칙을 적용했다.

## 3. 실행과 검증

`mock_speed:=0.6`으로 네 시나리오를 실행했다. 동시 임무에는 `robot_b_r1:=false`도 지정했다. 네 실행 모두 정상 종료 조건에 도달했고, 구형 mock이 태그 없는 MOVE를 수락하는 것을 관측했다. [launch 구현](../../../src/part3_fleet_manager/launch/fleet_mock.launch.py) · [시나리오 기록](../stage3-scenarios.md)

## 4. 경험의 재해석

사용자 경험 미기록. `--show-args`는 인자 선언을 보여 준다. 값의 변환과 노드 시작은 인자를 지정한 실제 launch에서 검증했다.

## 5. 정리

기본값 실행과 숫자·불리언 인자를 지정한 실행을 함께 확인한다.
