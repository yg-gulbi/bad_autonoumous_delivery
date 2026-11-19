# OCHuman-Pose Heavy Occlusion Samples

- **출처**: HuggingFace `vrg-prague/OCHuman-Pose` 공개 `assets/` 3장 + `val`/`test` COCO JSON 어노테이션 매칭
- **라이선스**: MIT License
- **라벨 형식**: `annotations_coco.json` (COCO 17관절) + `labels/*.txt` (YOLO Pose 17관절)
- **참고**: 침대·이불 데이터는 아니며, 다중 인물 겹침으로 인한 **고도 가림(Occlusion) Pose GT 검증용**

| 파일 | Split | 해상도 | 인스턴스 수 | 특징 |
| --- | :---: | :---: | :---: | --- |
| `000007.jpg` | test | 913x1000 | 3 | 3 closely overlapping people (heavy shoulder/arm occlusion) |
| `000028.jpg` | test | 550x860 | 6 | 6 crowded overlapping people (dense leg/torso occlusion) |
| `000116.jpg` | val | 637x639 | 5 | 5 subway riders with heavy overlapping limb occlusion |
