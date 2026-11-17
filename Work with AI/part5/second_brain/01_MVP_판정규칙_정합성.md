# 01. MVP 판정 규칙 정합성

## 1. 현상 및 발견 경위

- 합의된 MVP는 카메라별 다각형 ROI 하나와 최근 5개 유효 프레임 중 `True` 3회 이상 규칙을 사용한다.
- 기존 구현은 환자 후보 획득용 `bed_roi`와 이탈 판정용 `safe_roi`를 모두 공개 설정에 요구했다.
- “3프레임 연속 `True`”는 비교 분석안이므로 ROS 공개 Bool 규칙과 섞이면 안 된다.
- 공개 출력은 `/patient/bed_exit`의 `std_msgs/msg/Bool`만 유지하며 `TRACKING_LOST`, `AUTHORIZED_EXIT` 같은 상태를 추가하지 않는다.

## 2. 근본 원인 분석

- `PatientSelector`, 네 사전학습 방법, 팩토리, JSON 설정, 영상 평가가 모두 두 ROI를 매개변수로 전달했다.
- 분석용 이벤트 게이트와 런타임 투표는 별도 모듈이었지만 3프레임 연속 비교 지표는 명시적 산출물로 남지 않았다.
- 따라서 클래스 생성 경로 전체를 단일 `roi`로 바꾸고, 3연속 지표는 `research/` 내부에만 두어야 했다.

## 3. 해결 조치 및 의사결정 이유

- `BedExitMethod.roi` 하나를 환자 후보 획득과 이탈 경계 판정에 공통 사용한다.
- `make_method(name, roi, model_dir, ...)`로 팩토리 시그니처를 축소했다.
- `methods.template.json`과 `testbed.template.json`은 `roi` 키 하나만 사용한다.
- `pretrained.read_config()`는 구형 `bed_roi` 또는 `safe_roi`가 있으면 오류를 발생시켜 잘못된 설정을 조용히 해석하지 않는다.
- `ConsecutiveExitAnalysis`는 `research.evaluation.monitoring`에만 추가했다. 유효 원판정 3회 연속 여부를 계산하며 판단 불가 프레임에서 초기화한다.
- 분석 CSV에는 `three_consecutive_exit`을 기록한다. `output_bool`은 계속 3/5 결과다.
- 오프라인 오버레이도 ROI 선 하나만 표시한다.
- 노트북, 런타임 문서, 연구 문서의 설명과 예제를 같은 규칙으로 수정했다.

핵심 동작 차이 검증 시퀀스:

```text
raw:                    T, T, F, T, T
3/5 runtime output:     F, F, F, F, T
3-consecutive analysis: F, F, F, F, F
```

이 시퀀스로 두 규칙이 동일 구현으로 합쳐지지 않았음을 테스트한다.

## 4. 검증 결과 및 복구

실행 명령:

```bash
git diff --check
PYTHONPATH=src/part5_bed_exit \
  /home/hyunwoong/venvs/rokey_venv/bin/python -m pytest \
  src/part5_bed_exit/test -q
```

결과:

- `56 passed, 1 skipped, 1 warning`
- skip은 기준 환경에 선택형 영상 평가 의존성이 없는 테스트다.
- warning은 로컬 `dash.testing` pytest 플러그인의 사전 호환성 경고다.
- `src/part5_bed_exit/`의 Python·JSON·Markdown·노트북 코드 셀에서 구형 ROI 사용처가 제거되었고, 구형 설정 거부 검사 문자열과 회귀 테스트만 남았다.

복구:

- 이 단계만 되돌릴 때는 단계 커밋을 `git revert <커밋>`한다.
- 구형 로컬 설정을 수동 복구하지 말고 `bed_roi`/`safe_roi` 중 팀이 검수한 단일 경계를 `roi`로 옮긴 뒤 `roi_version`을 새로 지정한다.
- 원본 `rokey_venv`, 모델 가중치, 데이터셋은 이 단계에서 수정하지 않았다.
