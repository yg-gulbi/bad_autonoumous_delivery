# POC 실험 기록 형식

`research/templates/poc_experiment.template.csv`는 `run_static_pose_poc.py`의 실제 CSV 열과 같다. 정적 frame의 모델 원판정을 기록하며 런타임 3/5 결과와 섞지 않는다.

| 열 | 기록 규칙 |
|---|---|
| `method` | 비교 방법의 안정된 id. 예: `yolo_pose`, `mediapipe_pose` |
| `model` | 모델 파일 또는 정확한 모델 식별자 |
| `dataset`, `split`, `image_id` | 입력 출처와 표본 식별자 |
| `image_sha256` | 파일 내용 재현 확인용 hash |
| `preprocessing` | resize, color order, normalization 등 실제 적용 내용 |
| `roi_version`, `roi_source` | ROI 정책 버전과 좌표 출처 |
| `raw_classes` | 원본 데이터셋 라벨. 모델 출력과 혼동하지 않음 |
| `expected_bool` | 검수한 raw Bool. 정답이 없거나 빈 병상이면 빈 값 |
| `predicted_bool` | 모델 raw Bool. 판단 불가이면 빈 값 |
| `inference_time_ms` | warm-up 이후 frame 단위 wall-clock 시간 |
| `pose_detected` | 유효 Bool 산출 여부. 단순 후보 검출 여부가 아님 |
| `decision_reason` | 판정 내부 이유 |
| `failure_reason` | 판단 불가 또는 예외 이유. 성공이면 빈 값 |
| `notes` | proxy 라벨, corner case, 실험 제한 등 |

필수 최소 항목인 method, model, dataset/image_id, preprocessing, ROI version, expected Bool, predicted Bool, inference time, failure reason, notes를 모두 포함한다.

3/5 런타임을 평가할 때는 별도 frame-sequence 결과에 `sequence_id`, `frame_index`, `timestamp`, `history`, `published_bool`을 추가한다. 판단 불가 frame을 `predicted_bool=false`로 기록하지 않는다.
