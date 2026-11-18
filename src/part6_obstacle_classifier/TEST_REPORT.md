# `part6_obstacle_classifier` 파이프라인 검증 보고서

## 1. 실행 정보
- **테스트 파일**: `test/test_pipeline.py`
- **실행 명령어**:
  ```bash
  PYTHONPATH=/home/mu-02/rokey_ws/bad_autonoumous_delivery/src/part6_obstacle_classifier pytest test/test_pipeline.py
  ```
- **결과**: **13 passed in 0.12s** (5개 테스트 함수, 총 13개 파라미터 케이스 모두 통과)

---

## 2. 테스트 항목별 검증 내용

### 1) `test_valid_results_preserve_header` (2개 케이스)
- 탐지 결과가 비어 있는 경우(`obstacle_present: False`)와 객체 1개가 탐지된 경우(`obstacle_present: True`)를 검증합니다.
- 출력 딕셔너리에 입력 `header`와 `detections` 개수가 손실 없이 유지되는지 확인합니다.

### 2) `test_invalid_image_is_rejected` (4개 케이스)
- `None`, 빈 배열(`0x2x3`), 2차원 이미지(`2x2`), 4채널 이미지(`2x2x4`) 등 잘못된 입력 프레임이 들어왔을 때 `ValueError`가 발생하는지 확인합니다.

### 3) `test_invalid_model_result_is_rejected` (5개 케이스)
- 모델이 반환한 `Detection` 중 아래의 비정상 결과를 `ValueError`로 걸러내는지 확인합니다.
  - `confidence`가 `NaN`이거나 `1.1`(0~1 범위 초과)인 경우
  - `label`이 빈 문자열(`""`)인 경우
  - `bbox`가 이미지 크기를 벗어나거나(`x2=11`), `x1 > x2`인 경우

### 4) `test_unconfigured_model_does_not_report_clear` (1개 케이스)
- 모델 경로가 비어 있을 때(`load_classifier('')`) `RuntimeError`가 발생하는지 확인합니다.
- `build_error`가 장애물 없음(`False`)이 아닌 불명확 상태(`obstacle_present: None`)를 반환하는지 확인합니다.

### 5) `test_valid_depth_includes_pose_and_invalid_depth_omits_pose` (1개 케이스)
- 한 프레임에 객체 2개가 동시에 탐지되었을 때:
  - Depth가 유효한 객체(`2000mm` → `2.0m`)에는 3D/변환 좌표 `pose`가 포함되는지 확인합니다.
  - Depth가 무효한 객체(`0`)에는 `pose` 키가 제외되고 감지 정보(`label`, `confidence`, `bbox`)만 포함되는지 확인합니다.
