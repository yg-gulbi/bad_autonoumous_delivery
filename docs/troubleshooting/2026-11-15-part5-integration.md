# Part 5 실험 코드와 ROS 판정 계약 차이

- 프로젝트: BAD 병상 이탈 감지 Part 5
- 발생일: 2026-11-15
- 기록일: 2026-11-15 (Asia/Seoul)
- 상태: 해결
- 관련 파일·Issue·PR·커밋: [판정 전략](../../src/part5_bed_exit/part5_bed_exit/methods/bed_exit_core.py), [기존 상태 집계](../../src/part5_bed_exit/part5_bed_exit/bed_exit_state.py), [통합 테스트](../../src/part5_bed_exit/test/test_part5_integration.py)

## 1. 문제정의

### 기술적으로 관찰한 사실

재사용할 `/home/hv-02/rokey_ws/vision/bed_exit/bed_exit_core.py`는 EventGate에서 5프레임 미만이어도 세 번째 True부터 EXIT를 만들었다. 원판정 judge()는 미검출·잘못된 프레임에서 마지막 유효 bool을 반환하며 last.valid=false를 별도로 표시했다. 기존 ROS 노드는 bool만 보므로 판단 불가를 정상 프레임으로 투표할 수 있었다.

### 원인

실험 코드의 별도 유효성 필드·래치 정책과 저장소의 5프레임 3/5·오류 시 미발행 계약이 달랐다.

## 2. 해결방법 안들 설정

| 해결안 | 선택 또는 보류 이유 | 결과 |
| --- | --- | --- |
| 실험 모니터를 그대로 ROS에 연결 | 부분 창 조기 판정·이중 투표 가능 | 보류 |
| 네 인식 클래스를 재사용하고 기존 BedExitState 공유 | 인식 구현 유지, ROS 계약 일치 | 선택·검증 |

## 3. 해결방법 실행

AI가 Part 5 내부로 네 전략·다운로드·평가 코드를 복사하고 원본은 수정하지 않았다. 전략은 기존 BedExitMethod를 상속하며 유효 판정에서 Python bool을 반환한다. 판단 불가에서는 JudgmentUnavailable을 발생시킨다. 공통 노드는 이력 초기화 후 발행을 생략한다. 카메라 read() 예외도 같은 경로로 처리한다.

평가용 EventGate는 BedExitState를 호출한다. 연속 경보 래치는 유효 raw SAFE 5회 연속 후 재무장하며 프레임별 Bool에는 영향을 주지 않는다. 노트북 미리보기만 투표를 포함한 BedExitMonitor를 사용한다.

### 검증

- 32가지 5프레임 조합과 이후 창 이동에서 평가 게이트와 ROS 상태의 출력 일치.
- 카메라 예외·numpy.bool_ 반환·판단 불가 시 미발행, 복구 후 초기 4프레임 False 확인.
- 실제 네 사전학습 모델의 BGR 입력·Python bool 출력 확인. 일반 사람 사진을 쓴 API 검사이며 병상 이탈 성능 실험은 아님.
- `colcon build --packages-select part5_bed_exit --symlink-install` 통과.
- 실제 웹캠·병상 영상·검수한 이벤트 정답을 통한 성능과 관제 연결은 미검증.

## 4. 경험의 재해석

### 내가 실제로 경험하고 느낀 것

사용자 경험 미기록.

### AI 해석·제안

같은 judge(frame) 시그니처라도 미검출 처리와 시간 집계 의미를 확인해야 호환성을 유지할 수 있다. 로컬 영상과 ROS에 동일한 원판정 팩터리와 상태 집계기를 사용했다.

## 5. 정리

- 현재 결론: 네 클래스의 공통 인터페이스와 다수결·오류 계약을 연결하고 검증했다.
- 다시 발생하면: bool 타입, 유효성, 초기 창, 오류 후 재수집 동작을 확인한다.
- 다음에 반영할 것: 실제 ROI와 이벤트 GT 검수 후 정량평가 실행.
