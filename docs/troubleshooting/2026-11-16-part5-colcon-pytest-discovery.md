# Part 5 colcon test가 pytest를 수집하지 못함

- 프로젝트: bad_ws Part 5 병상 이탈 감지
- 발생일: 2026-11-16
- 기록일: 2026-11-16
- 상태: 해결
- 관련 파일·Issue·PR·커밋: `src/part5_bed_exit/setup.py`, 6단계 팀 공유 커밋

## 1. 문제정의

### 기술적으로 관찰한 사실

`colcon build --packages-select part5_bed_exit --symlink-install`은 성공했으나 `colcon test --packages-select part5_bed_exit`가 `python -m unittest -v`를 실행했다. 저장소의 테스트는 pytest 형식이라 `Ran 0 tests`, `NO TESTS RAN`, 종료 코드 5가 발생했다. 같은 환경에서 직접 실행한 pytest는 60건을 수집했다.

### 원인

`package.xml`에 `python3-pytest` test dependency는 있었지만 Python package metadata에는 pytest test extra가 없었다. 설치된 `colcon-core 0.21.3`은 setup metadata에 pytest가 있을 때 pytest testing step을 선택하고, 없으면 unittest step으로 대체한다.

## 2. 해결방법 안들 설정

| 해결안 | 선택 또는 보류 이유 | 결과 |
| --- | --- | --- |
| `tests_require=['pytest']` | 과거 setuptools와 colcon에서 쓰던 방식 | 현재 setuptools가 unknown option으로 무시하여 실패 |
| `--python-testing pytest`를 명령마다 지정 | 코드 변경 없이 우회 가능하지만 기본 명령이 계속 실패 | 보류 |
| `extras_require={'test': ['pytest']}` | 현재 setuptools에서 유효하고 colcon이 test dependency로 인식 | 선택·성공 |

## 3. 해결방법 실행

1. 실패 로그에서 실제 실행 명령이 `python -m unittest -v`임을 확인했다.
2. `tests_require=['pytest']`를 시도했으나 setuptools가 옵션을 무시하고 같은 실패를 재현했다.
3. 실패한 옵션을 제거하고 `extras_require={'test': ['pytest']}`로 교체했다.
4. setup metadata의 `extras_require`에 pytest가 추출되는 것을 확인했다.

### 검증

- 실행한 확인: Part 5 `colcon build`, `colcon test`, `colcon test-result --verbose`
- 실제 결과: 빌드 1 package 성공, pytest 60건 수집, `60 tests, 0 errors, 0 failures, 0 skipped`
- 남은 한계: 실제 카메라 ROS 노드 장시간 구동과 hardware-in-the-loop 검증은 수행하지 않음

## 4. 경험의 재해석

### 내가 실제로 경험하고 느낀 것

사용자 경험 미기록.

### AI 해석·제안

`package.xml` dependency와 Python build metadata는 역할이 다르다. ROS dependency 선언만으로 colcon의 Python test runner 선택이 보장되지 않으므로 새 ament Python package에서도 test extra와 기본 `colcon test`를 함께 검증해야 한다.

## 5. 정리

- 현재 결론: `extras_require` 선언으로 기본 `colcon test`가 pytest를 선택하고 전체 60건이 통과한다.
- 다시 발생하면: `log/latest_test/<package>/command.log`에서 unittest/pytest 중 어느 runner가 실행됐는지 먼저 확인한다.
- 다음에 반영할 것: CI에서도 별도 pytest 명령뿐 아니라 `colcon test`와 `colcon test-result`를 실행한다.
