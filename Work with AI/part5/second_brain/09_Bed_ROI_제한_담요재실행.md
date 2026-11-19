# Bed ROI 제한 반영 및 담요 pair 재실행

## 1. 요청과 변경 범위

- 요청: full-image ROI 대신 처음 구현과 같은 병상 경계를 반영하고 담요 조건을 다시 실행한다.
- 기존 첫 구현의 ROI는 자동 침대 인식 모델이 아니라 사람이 지정한 `Bed ROI`/`Safe ROI`
  다각형이었다.
- 현재 MVP는 단일 ROI가 합의 사항이므로 `Safe ROI`를 다시 추가하지 않고 단일 `Bed ROI`만
  사용한다.
- 자동 침대 검출 모델의 추가 학습이나 가중치 생성은 이번 변경 범위가 아니다.

## 2. 구현 내용

### Pair 전용 ROI 설정

설정 파일:

`src/part5_bed_exit/research/configs/slp_public_demo_pair_roi_v1.json`

```json
{
  "roi_reviewed": true,
  "roi_version": "slp_public_demo_visible_mattress_v1",
  "roi_source": "manually traced visible mattress boundary shared by the two SLP public demo crops",
  "deployment_approved": false,
  "roi": [
    [0.133333, 0.092593],
    [0.875, 0.111111],
    [0.891667, 0.925926],
    [0.141667, 0.916667]
  ]
}
```

- 두 공개 crop에서 공통으로 보이는 매트리스 모서리를 정규화 좌표로 지정했다.
- `deployment_approved=false`이므로 실제 카메라 배포 ROI로 사용할 수 없다.
- pair 실행기는 이제 `--roi-config`를 필수로 요구한다.
- `roi_reviewed=true`, `roi_version`, `roi_source`가 없으면 실행을 거부한다.

### 시각화

- ROI 색상을 관절 내부 표시와 겹치던 초록색에서 자홍색으로 변경했다.
- 최소 3px 굵은 경계, 8% 투명 채움, `BED ROI` 라벨을 추가했다.
- 결과 CSV에는 `roi_version`, `roi_source`, `roi_normalized`를 추가했다.
- `summary.json`에는 설정 경로, 좌표, 배포 승인 여부를 기록한다.

## 3. 재실행 명령

```bash
PYTHONPATH=src/part5_bed_exit .venv/bin/python \
  -m research.poc.run_pose_pair_poc \
  --uncovered src/part5_bed_exit/research/data/datasets/slp2022/public_demo_pair/uncovered.png \
  --covered src/part5_bed_exit/research/data/datasets/slp2022/public_demo_pair/covered.png \
  --roi-config src/part5_bed_exit/research/configs/slp_public_demo_pair_roi_v1.json \
  --output-dir src/part5_bed_exit/results/pose_pair_slp_public_demo_bed_roi_v1
```

결과:

`src/part5_bed_exit/results/pose_pair_slp_public_demo_bed_roi_v1/`

## 4. 전후 결과

| 조건 | 방법 | full-image ROI v2 | Bed ROI 적용 | 적용 후 ms |
| --- | --- | --- | --- | ---: |
| 비가림 | YOLO Pose | `False / keypoints_inside` | `False / keypoints_inside` | 40.164 |
| 비가림 | MediaPipe | 판단 불가 / `no_person` | 판단 불가 / `no_person` | 12.321 |
| 이불 가림 | YOLO Pose | 판단 불가 / `incomplete_body_coverage` | `True / visible_keypoint_outside` | 34.699 |
| 이불 가림 | MediaPipe | 판단 불가 / `no_person` | 판단 불가 / `no_person` | 12.962 |

이불 가림 YOLO의 `True` 근거는 매트리스 ROI 왼쪽 밖에서 검출된 `left_elbow`다.

- 좌표: `(55.214, 439.342)` / 480×864 출력
- confidence: `0.789`
- ROI 밖 공통 관절 비율: `0.2`

## 5. 해석 제한

- Bed ROI 제한이 실제 판정에 반영되는 것은 확인했다.
- 이불 가림 샘플에는 병상 이탈 정답이 없으므로 변경된 `True`를 정답으로 볼 수 없다.
- 원본 crop은 120×216 저해상도이며 이미 SLP 정답 skeleton overlay가 그려져 있다.
- ROI 밖 `left_elbow`가 실제 가려진 팔 위치인지 모델 오검출인지 이 이미지 하나로 확정할 수 없다.
- 따라서 이번 결과는 ROI 통합 시험이며 담요 조건 정확도 근거가 아니다.

## 6. 검증

```text
targeted pytest: 5 passed
Part 5 pytest: 63 passed
colcon build: 1 package finished
colcon test-result: 63 tests, 0 errors, 0 failures, 0 skipped
```

## 7. 복구 방법

- 결과 폴더는 Git 제외 대상이므로 코드 복구와 별도로 보존하거나 삭제할 수 있다.
- 구현을 되돌릴 때는 이 작업의 커밋을 `git revert <commit>`으로 되돌린다.
- 이전 full-image 결과는 다음 경로에 그대로 남아 있다.

```text
src/part5_bed_exit/results/pose_pair_slp_public_demo_v2/
```

