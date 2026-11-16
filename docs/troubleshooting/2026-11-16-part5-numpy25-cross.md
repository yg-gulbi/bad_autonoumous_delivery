# Part 5 PolygonROI 2D np.cross NumPy 2.5 호환 오류

- 프로젝트: bad_ws Part 5 병상 이탈 감지
- 발생일: 2026-11-16
- 기록일: 2026-11-16
- 상태: 해결
- 관련 파일·Issue·PR·커밋: `src/part5_bed_exit/part5_bed_exit/methods/bed_exit_core.py`, `feat/part5-detect-seg`

## 1. 문제정의

### 기술적으로 관찰한 사실

`PYTHONPATH=src/part5_bed_exit python3 -m pytest src/part5_bed_exit/test -q` 실행 시 `test_pretrained_bed_exit.py`와 `test_part5_integration.py`에서 `4 failed, 4 errors`가 발생했다. 모두 `PolygonROI.__init__` 내부의 `orient(a, b, c)` 호출 지점에서 다음 예외로 실패했다.

`ValueError: Both input arrays must be (arrays of) 3-dimensional vectors, but they are 2 and 2 dimensional instead.`

### 원인

`bed_exit_core.py` 49행의 `np.cross(b-a, c-a)`가 2차원 좌표 벡터를 직접 전달하고 있었다. 현재 시스템 환경의 NumPy 2.5.3에서는 2차원 벡터에 대한 `np.cross` 지원이 제거되어 3차원 벡터가 아니면 `ValueError`가 발생한다.

## 2. 해결방법 안들 설정

| 해결안 | 선택 또는 보류 이유 | 결과 |
| --- | --- | --- |
| 시스템 NumPy 다운그레이드 | 실행 환경 의존성이 커지고 동일 오류 재발 가능 | 보류 |
| 2차원 외적 스칼라식(`ab[0]*ac[1] - ab[1]*ac[0]`) 직접 계산 | NumPy 1.x와 2.5+ 모두에서 동일 수학 결과 보장 | 선택·성공 |

## 3. 해결방법 실행

`src/part5_bed_exit/part5_bed_exit/methods/bed_exit_core.py`의 `orient(a, b, c)`를 `ab = b - a`, `ac = c - a` 후 `float(ab[0] * ac[1] - ab[1] * ac[0])`를 반환하도록 수정했다.

### 검증

- 실행한 확인: `PYTHONPATH=src/part5_bed_exit python3 -m pytest src/part5_bed_exit/test -q`, `colcon build --packages-select part5_bed_exit --symlink-install`, `colcon test --packages-select part5_bed_exit`
- 실제 결과: 68 passed, 1 skipped (`av` 미설치로 인한 선택적 영상 벤치마크 스킵), 0 failed, 0 errors
- 남은 한계: `av`(PyAV) 선택 의존성 포함 전체 69건 통과 여부는 미확인

## 4. 경험의 재해석

### 내가 실제로 경험하고 느낀 것

사용자 경험 미기록.

### AI 해석·제안

NumPy 2.x 환경과 호환하려면 기하 계산에서 2D `np.cross` 대신 명시적 2D 외적 연산을 사용하는 것이 안전하다.

## 5. 정리

- 현재 결론: 2D 외적 직접 계산으로 수정해 `PolygonROI` 및 Part 5 테스트 68건이 모두 통과했다.
- 다시 발생하면: `np.cross`에 2차원 벡터를 전달하는 다른 기하 유틸리티가 있는지 검색한다.
- 다음에 반영할 것: 없음
