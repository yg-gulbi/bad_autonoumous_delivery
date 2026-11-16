# Part 5 데이터셋 작업 목록

조사 범위는 Notion 문서에서 확정한 10종이다. `dataset_catalog.json`이 출처·레이블·접근 상태의 기준 파일이다.

## 대상과 현재 작업

1. `roboflow_bed_exit_v4`: API 키로 848장과 YOLO 라벨을 받고 클래스 박스 의미를 검수한다.
2. `fallvision`: Dataverse 파일 목록을 받은 뒤 침대 영상·Pose CSV 대응과 전체 용량을 검수한다.
3. `slp2022`: MICCAI19 공개 `simLab/00004/RGB/cover2` 45장을 비라벨 정성 평가에 사용한다. SLP2022 전체와 관절 정답은 승인 암호가 있을 때만 추출한다.
4. `blanketset`: 공개 README·조건표·동작 이미지만 받는다. 405개 RGB-IR-D 영상은 법적 접근 제한이 해제되거나 제공자 승인을 받은 뒤 사용한다.
5. `in_bed_patient_segmentation`: 저자 승인 후 1,487프레임과 person/bed Mask 형식을 검수한다.
6. `tip_pimesh`: 공개 코드를 받고, TIP 원본은 저자 승인 후 RGB·COCO17 대응을 확인한다.
7. `enighttrack`: 공개 논문을 기준으로 20개 시나리오를 정리하고, Depth 원본은 저자 승인 후 받는다.
8. `hp_bed`: 공개 논문을 받고, HP-BED 원본 공개 경로 또는 저자 제공 여부를 확인한다.
9. `blanketgen2_fit3d`: 공개 논문을 받고, 공식 데이터 아카이브가 공개되면 1,217,312프레임의 실제 용량과 권리를 검수한다.
10. `patientcare`: Hugging Face의 real/synthetic 파일을 받고 원천별 라이선스를 분리한다.

## 실행 순서

```bash
# 10개 ID를 확인한다.
python3 src/part5_bed_exit/research/datasets/download_datasets.py --list

# 한 번에 하나의 대표 자료만 받는다.
python3 src/part5_bed_exit/research/datasets/download_datasets.py --dataset roboflow_bed_exit_v4 --mode preview

# 대표 자료 검수 후 같은 데이터셋의 공개 원본 전체를 받는다.
# 최소 10 GiB의 빈 공간을 남기며, 다른 데이터셋을 자동으로 이어받지 않는다.
python3 src/part5_bed_exit/research/datasets/download_datasets.py --dataset roboflow_bed_exit_v4 --mode full

# 내려받은 이미지와 영상 첫 프레임을 브라우저용 목록으로 만든다.
python3 src/part5_bed_exit/research/datasets/dataset_preview.py
```

필요한 인증값은 파일에 적지 않고 환경변수로만 전달한다.

- `ROBOFLOW_API_KEY`: Roboflow v4 내보내기
- `SLP_ZIP_PASSWORD`: SLP 암호화 RGB 추출
- `DATAVERSE_TOKEN`: FallVision에서 파일별 토큰이 필요한 경우
- `HF_TOKEN`: Patient Care 저장소가 인증을 요구하는 경우

데이터셋별 결과는 `src/part5_bed_exit/research/data/datasets/status/<dataset_id>.json`, 미리보기는
`src/part5_bed_exit/research/data/datasets/preview_index.html`에 생성된다. `research/data/`는 Git에서 제외된다.
