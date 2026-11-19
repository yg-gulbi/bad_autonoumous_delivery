# Part 5 공개 데이터셋별 선별 샘플 모음 (`sample_datasets/`)

각 공개 데이터셋에서 비밀번호·접근 차단 없이 바로 내려받을 수 있는 **정상/고품질 샘플만 직접 눈으로 검수해 데이터셋별 폴더로 분리 구성**했습니다.
(기존 SLP 공식 데모 3종은 [../slp_public_samples/README.md](../slp_public_samples/README.md)에 유지되어 있습니다.)

| 폴더명 | 샘플 수 | 라벨 형식 | 주요 용도 |
| --- | ---: | --- | --- |
| [coco2017_in_bed_pose/](coco2017_in_bed_pose/README.md) | 7 | 17-kpt COCO + YOLO Pose + Bed ROI | 실사 병상·이불 가림·침대 모서리 (`bed` ROI + 17관절 GT) |
| [gmdcsa24_bed_exit_keyframes/](gmdcsa24_bed_exit_keyframes/README.md) | 9 | Clip action description CSV (no pose GT) | 실사 침대 수면→걸터앉기→기상 이탈→낙상(`EXIT`) 키프레임 |
| [simuletic_cctv_lying_pose/](simuletic_cctv_lying_pose/README.md) | 6 | 17-kpt YOLO Pose (laying/standing) | 상단 CCTV(병원 복도 포함) 누운/서있는 자세 17관절 Pose GT |
| [ochuman_occluded_pose/](ochuman_occluded_pose/README.md) | 3 | 17-kpt COCO + YOLO Pose | 심한 다중 인물 가림(Occlusion) 17관절 Pose GT 검증 |
| [polar_lying_bbox/](polar_lying_bbox/README.md) | 5 | YOLO BBox (lying) | 실내·이불 누운 자세(`lying`) BBox 검출 샘플 |
