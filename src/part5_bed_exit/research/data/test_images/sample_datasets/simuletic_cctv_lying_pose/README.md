# Simuletic CCTV Fall / Lying-Down Pose Samples

- **출처**: HuggingFace `Simuletic/CCTV_Incident_Dataset_Fall_Lying_Down_Detection` (111장 무료 공개 샘플 중 6장 선별)
- **라이선스**: CC BY 4.0
- **라벨 형식**: YOLO Pose 17관절 (`labels/*.txt`, class `0=laying`, `1=standing`), `dataset.yaml` 포함
- **참고**: 이불·침대 데이터는 아니지만 상단 CCTV 화각(병원 복도 포함) 누운 자세 Pose 파이프라인 바로 검증 가능

| 파일 | 해상도 | 인물 수 | 특징 |
| --- | :---: | :---: | --- |
| `laying04.png` | 1024x1024 | 1 | Hospital corridor wall handrail view, side-lying pose |
| `laying05.png` | 1024x1024 | 1 | Hospital corridor opposite angle, side-lying pose |
| `laying08.png` | 1024x1024 | 1 | Hospital corridor slightly rotated view, lying pose |
| `laying03.png` | 1024x1024 | 1 | Low-light grayscale garage view, curled side-lying pose |
| `laying16.png` | 1024x1024 | 2 | Multi-person outdoor scene: laying (0) + background standing (1) |
| `laying13.png` | 1024x1024 | 2 | Multi-person indoor aisle: laying (0) + standing (1) |
