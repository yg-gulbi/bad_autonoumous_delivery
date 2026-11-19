# Part 5 단위 테스트 목록

이 디렉터리는 함수·객체·모듈 경계에 Driver/Stub을 주입해 결정적인 입력과 출력을
검증한다. 테스트 이름의 `UT-*` 식별자는 요구사항과 pytest 결과를 대응시키기 위한
실험 단위 이름이다. 실제 MP4·웹캠·ROS 노드 간 연결은 `test/integration/`,
데이터셋·연구 도구 검증은 `test/research_tools/`에서 담당한다.

## 실행 방법

저장소 루트에서 다음 명령을 실행한다.

```bash
source /opt/ros/jazzy/setup.bash
mkdir -p src/part5_bed_exit/results/unit_tests
PYTHONPATH=src/part5_bed_exit:${PYTHONPATH} python3 -m pytest \
  src/part5_bed_exit/test/unit -vv \
  --junitxml=src/part5_bed_exit/results/unit_tests/part5_unit.xml
```

`-vv` 출력과 JUnit XML에는 파일명·테스트 함수명이 함께 남으므로 실패한 요구사항을
즉시 역추적할 수 있다. 결과 디렉터리는 실행 전에 생성해야 한다.

## 요구사항별 실험 단위

- `UT-YOLO-001`: 가짜 YOLO Driver의 입력 기록과 가짜 BBox 출력으로 `conf=0.5`,
  person 클래스, 최종 Python bool을 검증한다.
- `UT-NODE-001`: 가짜 카메라·판단 객체를 주입해 기본 `capture_fps=30.0`이 ROS 타이머
  `1/30초`로 연결되는지 검증한다.
- `UT-RENDER-001`: Detection BBox, Segmentation mask, YOLO Pose 골격,
  MediaPipe Pose 골격이 서로 다른 영상 픽셀을 생성하는지 검증한다.
- `UT-SAFETY-001`: 사람 미검출을 정상 `False`로 오발행하지 않고 이력을 초기화하는
  안전 계약을 검증한다.

## 운영 코드 단위 테스트

### `test_bed_exit_state_voting.py`

대상: `part5_bed_exit/bed_exit_state.py`의 `BedExitState`.

- `test_all_windows_and_startup`: 가능한 5프레임 Bool 조합 32개 전체가 최근 5개 중
  `True` 3개 이상 규칙을 따르는지 확인한다. 최초 4프레임까지는 `False`인지도 확인한다.
- `test_old_frames_expire_and_retrigger`: 여섯 번째 판단부터 가장 오래된 값이 제거되고,
  새로운 최근 5프레임 조합으로 이탈 상태가 다시 계산되는지 확인한다.
- `test_invalid_decisions_do_not_change_history`: `0`, `1`, `None`, `np.bool_`처럼 순수
  Python `bool`이 아닌 값을 거부하고 기존 투표 이력을 보존하는지 확인한다.

### `test_bed_exit_method_contract.py`

대상: `part5_bed_exit/bed_exit_method.py`의 판단 인터페이스.

- `test_abstract_method_cannot_be_instantiated`: `judge()`를 구현하지 않은 추상
  `BedExitMethod` 객체를 생성할 수 없는지 확인한다.
- `test_validate_accepts_instance_with_callable_judge`: 호출 가능한 `judge(frame)`을 가진
  객체를 판단 메서드로 인정하는지 확인한다.
- `test_validate_rejects_invalid_targets`: 클래스 자체, `judge`가 없는 객체, 호출 불가능한
  `judge` 값을 가진 객체를 거부하는지 확인한다.
### `test_runtime_segmentation_factory.py`

대상: `part5_bed_exit/pretrained.py`의 설정 기반 단일 팩토리.

- `test_runtime_factory_uses_method_selected_in_config`: 운영 객체가 설정 JSON의 `method`를
  사용하는지 확인한다.
- `test_method_override_uses_one_explicit_branch_per_model`: 연구 비교에서 지정한 이름으로
  설정값을 덮어쓰고 각 모델의 명시적 `if` 분기를 사용하는지 확인한다.

### `test_runtime_segmentation_flow.py`

대상: FigJam System Flowchart v5의 마스크 누락 예외 흐름.

- `test_missing_mask_resets_vote_but_keeps_patient_position`: 환자 BBox는 찾았지만 마스크가
  없을 때 ROS Bool을 발행하지 않고 5프레임 투표만 초기화하며, 다음 프레임 연결을 위한
  이전 환자 BBox는 유지하는지 확인한다.

### `test_bed_exit_core_rendering.py`

대상: 운영과 연구가 함께 사용하는 `methods/bed_exit_core.py`의 프리뷰와 ROI 교체.

- `test_core_detection_and_segmentation_render`: 코어 Detection·Segmentation이 동일한
  진단 구조로 ROI와 판정 결과를 표시하는지 확인한다.
- `test_set_roi_resets_patient_tracking`: 정적 이미지별 ROI 교체 시 이전 환자 추적이
  함께 초기화되는지 확인한다.
- `test_four_methods_render_different_visual_evidence`: 네 방법론별 프리뷰가 서로 다른
  근거 픽셀을 만드는지 확인한다.

### `test_yolo_inference_driver.py`

대상: `YoloMethod.candidates()`부터 `DetectionROI.judge()`까지의 모델 경계.

- `YoloDriverStub`: 실제 가중치 없이 `predict()` 호출 인자를 기록하고 한 사람 BBox를
  반환한다.
- `test_yolo_driver_uses_confidence_050_and_returns_python_bool`: 신뢰도 0.5와 최종 bool
  출력을 확인한다.

### `test_bed_exit_node_timing.py`

대상: `BedExitNode.__init__()`의 카메라 Driver 주입과 ROS 타이머 설정.

- `CaptureDriverStub`, `MethodStub`: 실제 웹캠과 모델을 대체한다.
- `test_default_capture_fps_creates_30hz_timer`: 기본 30Hz 설정을 확인한다.

## 현재 단위 테스트에서 제외한 항목

- 실제 YOLO·MediaPipe 가중치 추론
- 외부 데이터셋·모델 다운로드
- MP4 전체 재생과 성능 벤치마크
- 실제 USB 웹캠 입력
- ROS 2 DDS 통신과 `/patient/bed_exit` 구독 측 검증
- Jupyter Notebook의 셀 순차 실행

위 항목은 외부 파일·장치·네트워크 또는 여러 구성요소가 필요하므로 단위 테스트가 아니다.
특히 `UT-NODE-001` 통과는 타이머가 초당 30회 호출되도록 설정됐다는 뜻이지, 실제
웹캠+모델 처리량이 30FPS라는 뜻이 아니다. 실제 30FPS 충족 여부는 대상 USB 웹캠과
운영 가중치를 사용해 프레임 수/경과 시간을 기록하는 통합 성능 실험으로 판단한다.
