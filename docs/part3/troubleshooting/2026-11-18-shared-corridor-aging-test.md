# 공유 통로 전환 후 대기 우선순위 테스트의 허가 대수 불일치

- 프로젝트: bad_ws PART 03
- 발생일·기록일: 2026-11-18
- 상태: 해결
- 관련 파일: [자원 관리자 테스트](../../../src/part3_fleet_manager/test/test_resource_manager.py)

## 1. 문제정의

공유 통로를 기본값으로 적용한 첫 전체 pytest에서 155건이 통과하고 `test_aging_and_blocked_resource` 한 건이 실패했다. 막힘을 해제한 tick에서 오래 기다린 로봇이 먼저 허가됐고, 두 번째 로봇도 남은 한 자리를 허가받았다. 기존 테스트는 허가 목록에 첫 로봇만 있어야 한다고 가정했다.

## 2. 해결방법 안들 설정

| 해결안 | 선택 근거 | 결과 |
|---|---|---|
| 두 모드에서 각각 정원과 허가 순서를 검증 | 단독 통로의 기존 기대값과 공유 통로의 두 번째 허가를 모두 확인할 수 있음 | 적용 |

## 3. 해결방법 실행

같은 입력을 `EXCLUSIVE`와 `SHARED_YIELD`에서 실행했다. 두 모드 모두 막힘 중에는 대기하고 해제 후 오래 기다린 로봇이 먼저 허가된다. 두 번째 로봇은 공유 모드에서 같은 tick에 허가된다.

### 검증

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src/part3_fleet_manager /usr/bin/python3 -m pytest \
  -p no:cacheprovider src/part3_fleet_manager/test/test_resource_manager.py -k aging -q
```

두 모드 모두 통과했다. PART 03 전체 pytest 결과는 174개 통과, 0개 실패였다.

## 4. 경험의 재해석

사용자 경험 미기록. 이 사례에서는 허가 순서와 허가 대수를 따로 확인해야 대기 우선순위와 정원 정책을 각각 검증할 수 있었다.

## 5. 정리

두 모드의 기대 허가 목록을 각각 명시해 대기 순서와 정원을 함께 검증한다.
