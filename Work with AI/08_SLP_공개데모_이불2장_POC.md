# 08. SLP 공개 데모 이불 가림·비가림 2장 Pose POC

작업일: 2026-11-16

## 1. 데이터셋 선정과 접근 상태

이불 가림과 비가림 RGB를 포함하는 후보로 SLP2022를 선정했다. SLP는 RGB, LWIR,
depth, pressure를 같은 자세에서 수집하며 `uncover`, `cover1`, `cover2` 조건을 제공한다.

원본 공식 ZIP은 17,534,988,296 bytes이고 RGB member가 암호화되어 있다. 현재 환경에는
승인된 `SLP_ZIP_PASSWORD`가 없으므로 원본 RGB를 추출하지 않았다. 접근 제한을 우회하지
않고 기존 다운로드 코드로 공식 GitHub reference archive만 내려받았다.

```bash
PYTHONPATH=src/part5_bed_exit .venv/bin/python \
  src/part5_bed_exit/research/datasets/download_datasets.py \
  --dataset slp2022 --mode preview
```

공식 저장소의 `imgs/demo_2d.PNG`에는 공개된 저해상도 RGB panel이 있다. 이 자료에서
비가림과 이불 가림 panel을 각각 한 장 분리했다.

| 조건 | 크기 | SHA-256 |
|---|---:|---|
| 비가림 `uncovered.png` | 120×216 | `d9ea1601485433a6bf3d1810e773616046ac23f08007603e666fd721350764ec` |
| 이불 가림 `covered.png` | 120×216 | `beb8a50c3dea5ad6565d91f8ac5f357cf3e71e93dc16c59231c03b583416fc9d` |

원본 `demo_2d.PNG` SHA-256:
`a52b1dff9d0ba4e9a55865fd869c8ae0422e35bcf91ae0fc960ec52801fe2192`.

두 crop은 원본 SLP 데이터 파일이 아니라 공식 코드 저장소의 공개 데모 판넬이다. 이미지에
SLP 정답 스켈레톤이 이미 그려져 있고 두 조건의 자세도 다르다. 따라서 모델 성능·정확도·
담요 강건성 비교에는 사용할 수 없고 실행 가능성과 실패 양상 확인만 가능하다.

## 2. 실행기

신규 `research/poc/run_pose_pair_poc.py`는 임의의 비가림·가림 이미지 pair를 받아 다음을
생성한다.

- YOLO Pose 모델별 이미지 2장
- MediaPipe Pose 모델별 이미지 2장
- 조건별 좌우 비교 이미지 2장
- 전체 비교 시트 1장
- 전체 후보와 관절 좌표 `keypoints.jsonl` 4행
- 판정·시간·실패 원인 `results.csv` 4행
- 입력 hash·전처리·한계 `summary.json`

저해상도 panel의 시각적 검토를 위해 120×216을 cubic interpolation으로 480×864로
확대한다. 단일 ROI는 이미지 전체를 사용한다. 이는 침대 외곽 ROI가 아니므로 Bool은 침대
이탈 결과가 아니라 pose 관절의 유효성 smoke test다.

실행 명령:

```bash
PYTHONPATH=src/part5_bed_exit .venv/bin/python \
  -m research.poc.run_pose_pair_poc \
  --uncovered src/part5_bed_exit/research/data/datasets/slp2022/public_demo_pair/uncovered.png \
  --covered src/part5_bed_exit/research/data/datasets/slp2022/public_demo_pair/covered.png \
  --output-dir src/part5_bed_exit/results/pose_pair_slp_public_demo_v1
```

## 3. 실제 결과

| 조건 | 방법 | 후보 | 원판정 | 이유 | 추론 ms |
|---|---|---:|---|---|---:|
| 비가림 | YOLO Pose | 1 | `False` | `keypoints_inside` | 38.010 |
| 비가림 | MediaPipe Pose | 0 | 판단 불가 | `no_person` | 13.696 |
| 이불 가림 | YOLO Pose | 1 | 판단 불가 | `incomplete_body_coverage` | 34.917 |
| 이불 가림 | MediaPipe Pose | 0 | 판단 불가 | `no_person` | 13.413 |

해석:

1. YOLO Pose는 비가림에서 17관절 후보 하나를 찾아 공통 13관절을 유효하게 냈다.
2. 이불 가림에서는 YOLO Pose가 사람 후보와 일부 관절을 찾았지만 공통 13관절 전체가 없어 안전 `False`를 확정하지 않았다.
3. MediaPipe는 두 panel 모두 pose 후보를 만들지 못했다.
4. 가림에서 판단 불가를 정상으로 치환하지 않는 기존 안전 정책이 작동했다.
5. 원본 panel의 정답 스켈레톤 선이 모델 입력에 포함되어 있으므로 검출 결과가 영향을 받았을 가능성이 있다.

산출물:

```text
src/part5_bed_exit/results/pose_pair_slp_public_demo_v1/
├── yolo_pose/{uncovered,covered}.jpg
├── mediapipe_pose/{uncovered,covered}.jpg
├── comparison/{uncovered,covered}.jpg
├── pose_pair_comparison.jpg
├── keypoints.jsonl
├── results.csv
└── summary.json
```

- 비교 시트 SHA-256: `c1be5256125c1e5646e20f925ea61215d364ecc1fb19632274432ef0071a7a5a`
- 관절 JSONL SHA-256: `8acb43b828696c592186de1abe834e475f05694412cc29206f7f5ddccaa500b5`

## 4. 검증과 재현

신규 단위 테스트는 120×216 입력이 480×864로 aspect ratio를 유지해 확대되는지와 잘못된
폭을 거부하는지 확인한다. 실행기는 기존 output directory가 있으면 덮어쓰지 않는다.

검증 결과:

- 관련 테스트: `27 passed`
- Part 5 전체 pytest: `62 passed`
- Part 5 colcon build: 성공
- Part 5 colcon test: 62 tests, 0 errors, 0 failures, 0 skipped

원본 SLP 접근 승인을 받은 후에는 다음 순서로 이 결과를 교체한다.

1. 같은 subject·pose index의 `uncover`와 `cover1` RGB를 선택한다.
2. 정답 스켈레톤이 그려지지 않은 원본을 사용한다.
3. 동일 crop·해상도·ROI로 두 모델을 실행한다.
4. 유효 판정률과 관절별 검출률을 조건별로 비교한다.

현재 결과는 이 네 조건을 충족하지 않으므로 정량 검증 전 단계다.
