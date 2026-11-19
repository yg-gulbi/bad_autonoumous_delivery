# GMDCSA24 Real Bed-Exit & Fall-from-Bed Keyframes

- **출처**: Zenodo `12921216` (`ekramalam/GMDCSA24-A-Dataset-for-Human-Fall-Detection-in-Videos` v2.0)
- **라이선스**: MIT License (GitHub / Zenodo)
- **구성 방식**: `.mp4` 영상 커밋 금지 규칙(`AGENTS.md`)에 맞춰 원본 영상 4편에서 핵심 전환 키프레임 9장만 `.jpg`로 추출
- **라벨 참고**: 원본에는 클립 단위 동작 설명(`ADL.csv`/`Fall.csv`)만 제공되며 **2D 관절 GT 라벨은 없음** — `bed_exit` 전환·이탈·낙상 정성/Pseudo-label 테스트용

| 파일 | 원본 클립 | 단계 구분 | 해상도 |
| --- | --- | --- | :---: |
| `s1_adl_04_sleep_to_sit_f025.jpg` | `Subject 1/ADL/04.mp4` | SAFE (lying) | 1280x720 |
| `s1_adl_04_sleep_to_sit_f068.jpg` | `Subject 1/ADL/04.mp4` | TRANSITION (sitting up) | 1280x720 |
| `s1_adl_04_sleep_to_sit_f110.jpg` | `Subject 1/ADL/04.mp4` | EDGE_SITTING (legs off bed) | 1280x720 |
| `s3_adl_03_get_up_from_bed_f044.jpg` | `Subject 3/ADL/03.mp4` | SAFE (lying) | 1280x720 |
| `s3_adl_03_get_up_from_bed_f118.jpg` | `Subject 3/ADL/03.mp4` | EDGE_SITTING (getting up) | 1280x720 |
| `s3_adl_03_get_up_from_bed_f266.jpg` | `Subject 3/ADL/03.mp4` | EXIT (standing/walking away) | 1280x720 |
| `s2_fall_11_night_fall_from_bed_f063.jpg` | `Subject 2/Fall/11.mp4` | PRE_FALL (on bed, night) | 1280x720 |
| `s2_fall_11_night_fall_from_bed_f143.jpg` | `Subject 2/Fall/11.mp4` | EXIT / FALLEN (on floor, night) | 1280x720 |
| `s3_adl_12_night_bed_stand_f144.jpg` | `Subject 3/ADL/12.mp4` | EXIT (standing at night) | 1280x720 |
