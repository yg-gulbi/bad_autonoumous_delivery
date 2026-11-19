# Part 5 시험 입력

이 폴더의 JPG·PNG·GIF는 모델과 연구 도구에 넣는 **시험 입력 또는 입력 미리보기**다.
모델 실행 결과 이미지는 `results/`에만 둔다.

- `01_uncovered_slp.jpg`, `02_covered_arm_out_slp.jpg`: 정적 모델 비교 입력
- 같은 이름의 `.roi.json`: 각 입력 이미지의 고정 Bed ROI
- `slp_public_samples/`: SLP 입력 프레임과 원본 시퀀스 확인용 GIF·preview
- `sample_datasets/*/images`: 데이터셋에서 선별한 시험 입력
- `sample_datasets/*/labels`, `annotations_coco.json`: 기대 정답·주석
- `sample_datasets/*/rois`: 입력별 Bed ROI
- `sample_datasets/*/vis`: 데이터셋 준비 단계에서 정답·선별 상태를 확인하는 미리보기

`vis/`, GIF, `preview.jpg`는 모델 예측 결과가 아니라 **입력 데이터 준비 검수물**이다.
