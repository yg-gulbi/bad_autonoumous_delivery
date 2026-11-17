# 07. YOLO Pose·MediaPipe Pose 시각화 검토 산출물

작업일: 2026-11-16

## 1. 문제와 목표

기존 정적 POC는 모델 내부에서 관절 좌표를 계산했지만 `results.csv`에는 Bool·시간·실패
이유만 기록했다. `review_sheet.jpg`도 Roboflow의 병상·환자 annotation box만 표시했으며,
YOLO Pose 17관절과 MediaPipe Pose 33관절의 실제 추론 결과는 사람이 확인할 수 없었다.

이번 변경의 목표는 다음 네 가지다.

1. 유효 판정뿐 아니라 판단 불가 사례의 전체 pose 후보를 보존한다.
2. 모델별 관절·스켈레톤·ROI·판정 근거 이미지를 저장한다.
3. 동일 입력에 대한 YOLO Pose와 MediaPipe Pose 결과를 좌우로 비교한다.
4. 화면뿐 아니라 원시 관절 좌표와 confidence를 JSONL로 재검증 가능하게 남긴다.

## 2. 구현 변경

### 런타임 진단 상태

`part5_bed_exit/methods/bed_exit_core.py`의 `BedExitMethod`가 최근 추론의 전체
`Candidate` 목록을 `last_candidates`에 보존한다. 공개 `judge(frame) -> bool` 계약과
ROS Bool 인터페이스는 변경하지 않았다.

이 보존이 필요한 이유는 다음과 같다.

- `incomplete_body_coverage`: 환자 후보와 일부 관절은 있지만 안전 `False`를 확정할 수 없음
- `patient_unassociated`: pose는 검출됐지만 초기 환자 box와 연결되지 않음
- `ambiguous_people`: 둘 이상의 후보가 비슷해 환자를 확정할 수 없음
- `no_person`: 후보 자체가 없음

기존 구현은 위 실패에서 최종 Diagnostics만 남기고 후보 좌표를 버렸기 때문에 시각적
원인 검토가 불가능했다.

### 시각화 모듈

신규 `research/poc/pose_visualization.py`가 다음을 담당한다.

- YOLO COCO 17관절과 MediaPipe 33관절의 스켈레톤 연결
- 모든 pose 후보 box·관절 표시
- 선택된 환자와 선택되지 않은 후보 구분
- 단일 ROI polygon 표시
- 공통 13관절의 ROI 내부·외부 표시
- `EXIT TRUE`, `SAFE FALSE`, `UNAVAILABLE`과 판정 이유·시간 표시
- 이미지·방법별 원시 keypoint JSON 직렬화
- 20장 좌우 비교 contact sheet 생성

색상 규칙:

| 표시 | 의미 |
|---|---|
| 초록 관절 | 선택 환자의 공통 관절이며 ROI 내부 |
| 빨강 관절 | 선택 환자의 공통 관절이며 ROI 외부 |
| 청록 관절 | 모델에는 있으나 공통 13관절 비교에서 제외 |
| 회색 관절·box | 검출됐지만 선택 환자가 아닌 후보 |
| 초록 polygon | Roboflow `hospital_bed` box 기반 POC ROI |
| 빨강/초록/노랑 상단 문구 | Exit / Safe / 판단 불가 |

### POC 실행기

`research/poc/run_static_pose_poc.py`가 한 번의 모델 추론에서 Bool과 시각화·좌표를 함께
생성한다. 시각화를 위해 모델을 두 번 추론하지 않는다. 기존 output directory 덮어쓰기
거부 정책도 유지한다.

## 3. 산출물 구조

실제 재실행 결과:

`src/part5_bed_exit/results/pose_mediapipe_roboflow20_v4/`

```text
pose_mediapipe_roboflow20_v4/
├── yolo_pose/                 # 모델별 관절 시각화 20장
├── mediapipe_pose/            # 모델별 관절 시각화 20장
├── comparison/                # 동일 이미지 좌우 비교 20장
├── pose_comparison_sheet.jpg  # 20개 비교 결과 전체 시트
├── keypoints.jsonl            # 이미지×모델 40행 원시 좌표
├── results.csv                # Bool·시간·실패 이유 40행
└── summary.json               # 환경·모델 hash·집계
```

`keypoints.jsonl`의 각 관절은 다음 값을 가진다.

- `index`, 공통 관절이면 `name`
- 원본 이미지 pixel 기준 `x`, `y`
- 모델이 제공한 `confidence`
- `usable`: confidence 0.5 이상이며 유한 좌표인지
- `common`: 공통 비교 13관절인지
- `outside_roi`: usable 공통 관절이면 ROI 외부인지

각 후보에는 `selected_patient`, `bbox_xyxy`, `candidate_confidence`를 함께 기록한다.

## 4. 실제 실행과 검토

실행 명령:

```bash
PYTHONPATH=src/part5_bed_exit .venv/bin/python \
  -m research.poc.run_static_pose_poc \
  --output-dir src/part5_bed_exit/results/pose_mediapipe_roboflow20_v4
```

실제 산출:

- YOLO Pose 이미지: 20장
- MediaPipe Pose 이미지: 20장
- 좌우 비교 이미지: 20장
- 전체 비교 시트: 1536×4180 JPEG 1장
- keypoint JSONL: 40행
- 전체 검출 pose 후보: 21개
  - YOLO Pose: 11개 이미지에서 11개 후보
  - MediaPipe Pose: 9개 이미지에서 10개 후보

대표 육안 검토:

1. `frame-000217...`: 두 모델 모두 오른쪽 발목 계열 공통 관절을 ROI 외부 빨강으로 표시하고 `EXIT TRUE`를 기록했다.
2. `frame-000025...`: 두 모델 모두 공통 관절을 ROI 내부 초록으로 표시하고 `SAFE FALSE`를 기록했다.
3. `frame-000002...`: 빈 병상에는 관절이 없고 두 모델 모두 `UNAVAILABLE | no_person`을 기록했다.
4. `frame-000162...`: MediaPipe가 복수 후보 스켈레톤을 회색으로 보존하고 `ambiguous_people`를 기록했다.

무결성:

- `pose_comparison_sheet.jpg` SHA-256: `1e519f41e8d90eb5cc2d54a9eb69b553a5994b243fff8be4237a8465993f0e5b`
- `keypoints.jsonl` SHA-256: `97456461689b31fca700e37d0f92794bfe7de25071b43f1ec94322ef01a44c3e`

## 5. 테스트와 복구

단위 테스트는 합성 YOLO 17관절에서 왼쪽 손목을 ROI 밖에 두고 다음을 검증한다.

- JSONL payload의 관절 이름과 `outside_roi=true`
- 선택 환자 표시
- 시각화 JPEG 생성
- comparison sheet 크기

명령:

```bash
PYTHONPATH=src/part5_bed_exit .venv/bin/python -m pytest \
  src/part5_bed_exit/test/test_static_pose_poc.py \
  src/part5_bed_exit/test/test_pretrained_bed_exit.py -q
```

결과: `26 passed`.

전체 회귀 검증:

- Part 5 pytest: `61 passed`
- `colcon build --packages-select part5_bed_exit --symlink-install`: 성공
- `colcon test-result --verbose`: 61 tests, 0 errors, 0 failures, 0 skipped
- v1과 v4의 method·image·Bool·판정 이유·실패 이유 40행 비교: 전부 동일

따라서 시각화 추가가 기존 판정 결과와 공개 인터페이스를 변경하지 않았다.

모델 가중치, 데이터셋, `results/` 산출물은 Git 제외 대상이다. 복구 시 같은 명령에 새
output directory 버전을 지정한다. 기존 결과 경로를 삭제하거나 덮어쓰지 않는다.

## 6. 해석 한계

- 빨강 관절은 단일 프레임의 POC ROI 밖이라는 뜻이며 임상적 이탈 확정이 아니다.
- 시각화 JPEG는 검수 보조 자료다. 수치 재분석은 `keypoints.jsonl` 좌표를 사용한다.
- 검출되지 않은 관절은 화면에 임의 보간하지 않는다.
- 정적 이미지 시각화는 3/5 시간 투표와 Warning Lead Time을 검증하지 않는다.
- POC ROI는 Roboflow annotation 기반이며 배포 카메라의 검수 완료 ROI가 아니다.
