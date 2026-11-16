# COCO 2017 In-Bed / Blanket Pose Curated Samples

- **출처**: MS COCO `val2017` (`bed` + `person_keypoints` 교차 선별)
- **라이선스**: COCO Terms of Use / 이미지별 CC 라이선스
- **라벨 형식**: `annotations_coco.json` (COCO 17관절 + `bed` 인스턴스), `labels/*.txt` (YOLO Pose 17관절), `rois/*.roi.json` (`bed` bbox 기반 ROI)
- **시각화 범례**: 파란색 박스=`bed`, 녹색 점/선=`v=2` 보이는 관절, 노란색 점=`v=1` 가려진 정답 관절

| 파일 | 해상도 | 인물 수 | 특징 |
| --- | :---: | :---: | --- |
| `000000355257.jpg` | 640x427 | 1 | Hospital bed with medical monitors, patient lying under sheet (14 kpts, 4 occluded) |
| `000000469067.jpg` | 640x480 | 1 | Side-lying in bed partially covered by white blanket (13 kpts, 1 occluded) |
| `000000578545.jpg` | 640x474 | 1 | Reclining in bed with lower body covered by blanket (12 kpts, 4 occluded) |
| `000000254368.jpg` | 392x640 | 1 | Lying on side in bed under patterned blanket (13 kpts, 3 occluded) |
| `000000551439.jpg` | 427x640 | 1 | Sitting/reclining in bed with lower body covered by white blanket (15 kpts, 2 occluded) |
| `000000521509.jpg` | 640x427 | 1 | Sitting on edge of bed facing camera, bed-exit posture (16 kpts, 3 occluded) |
| `000000451155.jpg` | 640x480 | 1 | Low-light person sitting on edge of bunk bed (14 kpts, 7 occluded) |
