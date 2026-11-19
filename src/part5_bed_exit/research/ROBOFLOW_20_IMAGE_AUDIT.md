# Roboflow Bed Exit v4 20장 검수

검수일: 2026-11-16

범위: POC와 동일한 20장(`test` 18장, `valid` 2장)

ROI: 원본 `hospital_bed` 최대 box를 사용한 `roboflow_hospital_bed_box_v1`

## 1. 판정 기준과 한계

- `팀 판정 초안`은 임상 정답이 아니라 합의된 MVP의 단일 ROI 규칙을 사람이 이미지 한 장에 적용한 결과다.
- 보이는 팔·다리 중 하나라도 병상 ROI 밖이면 `비정상(True)`, 모두 안이면 `정상(False)`로 적었다.
- 빈 병상은 정상 `False`로 대체하지 않고 `판정 불가`로 적었다.
- 입력 열의 `D/S/YP/MP`는 Detection/Segmentation/YOLO Pose/MediaPipe Pose다. 20장 모두 RGB JPEG 입력으로 호환되지만, 이번 단계에서 실제 실행한 방법은 YP와 MP뿐이다. D/S 가중치는 준비하지 않았으므로 결과를 추정하지 않는다.
- Pose 열은 `YP/MP` 실제 결과다. `T`, `F`, `U`는 각각 `True`, `False`, `JudgmentUnavailable`이다.
- 원본 상태 라벨은 시퀀스 의미일 수 있어 단일 프레임 ROI 판정과 다를 수 있다. 이 표는 임상 ground truth가 아니다.

## 2. 20장 개별 검수표

| # | split / image_id | 원본 클래스 | 팀 판정 초안 | 빈 병상 | 팔·다리 ROI 이탈 | 네 방법 입력 | Pose 실제 결과 | 조기징후 후보 | 비고 |
|---:|---|---|---|---|---|---|---|---|---|
| 1 | test / `frame-000086_png.rf.6d27d65cfcfa8f6702b8705a4db0e03a` | bed_exit, hospital_bed | 비정상 | 아니오 | 오른팔·손 | D/S/YP/MP 호환 | T/U | 아니오 | MP incomplete body coverage |
| 2 | test / `frame-000114_png.rf.290ef9649c7a5ee41aa867e602d7aa53` | bed_exit, hospital_bed | 정상 | 아니오 | 없음 | D/S/YP/MP 호환 | U/U | **예** | 앉아 있으나 geometry는 ROI 내부. 상태 라벨-정적 규칙 불일치 |
| 3 | test / `frame-000152_png.rf.033463499d30b5bde61eab1e4cf3284e` | bed_exit, hospital_bed | 비정상 | 아니오 | 오른다리·발 | D/S/YP/MP 호환 | T/U | 아니오 | MP patient unassociated |
| 4 | test / `frame-000162_png.rf.73643177102b831cb069cae9a5f6e3ca` | bed_exit, hospital_bed | 비정상 | 아니오 | 양다리 | D/S/YP/MP 호환 | T/U | 아니오 | 한 명 장면에서 MP ambiguous people |
| 5 | test / `frame-000217_png.rf.1e5e3fc3b018662cf183eeee102c7036` | bed_exit, hospital_bed | 비정상 | 아니오 | 오른다리·발 | D/S/YP/MP 호환 | T/T | 아니오 | 두 pose 방법 모두 유효한 명확한 이탈 |
| 6 | valid / `frame-000026_png.rf.b952156e9ad5ac03cb527ddf4dcdfd87` | bed_exit, hospital_bed | 비정상 | 아니오 | 오른팔·손 | D/S/YP/MP 호환 | T/U | 아니오 | MP incomplete body coverage |
| 7 | test / `frame-000033_png.rf.60a95422c751237970517029c8bccdc3` | hospital_bed, in_bed | 정상 | 아니오 | 없음 | D/S/YP/MP 호환 | U/U | 아니오 | 두 방법 모두 누운 자세 판정 불가 |
| 8 | test / `frame-000116_png.rf.a01c6e6e5b0707674ff92024a96774f9` | hospital_bed, in_bed | 정상 | 아니오 | 없음 | D/S/YP/MP 호환 | U/U | 아니오 | 두 방법 모두 누운 자세 판정 불가 |
| 9 | test / `frame-000186_png.rf.beadfb004c42db9c67f0a5efaa7965bb` | hospital_bed, in_bed | 정상 | 아니오 | 없음 | D/S/YP/MP 호환 | U/U | 아니오 | 두 방법 incomplete body coverage |
| 10 | test / `frame-000259_png.rf.ad40fe1bdba6c562e3608a9d8c8c3b7d` | hospital_bed, in_bed | 정상 | 아니오 | 없음 | D/S/YP/MP 호환 | U/U | 아니오 | YP no person, MP incomplete body coverage |
| 11 | test / `frame-000310_png.rf.5647d8d8d05fd2c2ac37d30e8bf2f601` | hospital_bed, in_bed | 정상 | 아니오 | 없음 | D/S/YP/MP 호환 | U/U | 아니오 | 두 방법 incomplete body coverage |
| 12 | valid / `frame-000025_png.rf.5ed2ed9dc6e8cfb86e753d5a995a2554` | hospital_bed, in_bed | 정상 | 아니오 | 없음 | D/S/YP/MP 호환 | F/F | 아니오 | 두 pose 방법 모두 유효한 명확한 정상 |
| 13 | test / `frame-000002_png.rf.27565d266f1baeb69b8ccf485ead745a` | hospital_bed | 판정 불가 | **예** | 해당 없음 | D/S/YP/MP 호환 | U/U | 해당 없음 | 두 방법 no person |
| 14 | test / `frame-000006_png.rf.af81e26e0528ab8874be98f5f1cb6676` | hospital_bed | 판정 불가 | **예** | 해당 없음 | D/S/YP/MP 호환 | U/U | 해당 없음 | 두 방법 no person |
| 15 | test / `frame-000010_png.rf.10dd809347123df58558fb8678cdd762` | hospital_bed | 판정 불가 | **예** | 해당 없음 | D/S/YP/MP 호환 | U/U | 해당 없음 | 두 방법 no person |
| 16 | test / `frame-000016_png.rf.e2ed32218c9b1d799d467f431bf1d0ce` | hospital_bed | 판정 불가 | **예** | 해당 없음 | D/S/YP/MP 호환 | U/U | 해당 없음 | 두 방법 no person |
| 17 | test / `frame-000019_png.rf.a61444a70c2d60bff2abaa0bbc71ff76` | hospital_bed | 판정 불가 | **예** | 해당 없음 | D/S/YP/MP 호환 | U/U | 해당 없음 | 두 방법 no person |
| 18 | test / `frame-000023_png.rf.b8e572c4ec918673863a67c84c5d93c2` | hospital_bed | 판정 불가 | **예** | 해당 없음 | D/S/YP/MP 호환 | U/U | 해당 없음 | 두 방법 no person |
| 19 | test / `frame-000034_png.rf.2f1ec40319c5e7d3c700a3e189c43ba1` | hospital_bed | 판정 불가 | **예** | 해당 없음 | D/S/YP/MP 호환 | U/U | 해당 없음 | 두 방법 no person |
| 20 | test / `frame-000035_png.rf.1decddbdbfdbe5c3ef2b2b0348fc44bb` | hospital_bed | 판정 불가 | **예** | 해당 없음 | D/S/YP/MP 호환 | U/U | 해당 없음 | hospital_bed ROI가 하단 끝까지 확장된 annotation outlier |

집계: 비정상 5장, 정상 7장, 빈 병상/판정 불가 8장. 원본 `bed_exit` 6장 중 1장(#2)은 단일 프레임 geometry 기준 정상이며 조기징후 후보로 분리했다.

## 3. 바로 사용할 대표 3장

| 역할 | 선택 | 선택 이유 |
|---|---|---|
| 명확한 이탈 | #5 `frame-000217...` | 오른다리와 발이 ROI 밖이며 YP·MP가 모두 `True` |
| 명확한 정상 | #12 `frame-000025...` | 사람이 누워 있고 관절이 ROI 안이며 YP·MP가 모두 `False` |
| 빈 병상 안전 동작 | #13 `frame-000002...` | 사람이 없고 두 방법 모두 판단 불가로 처리하여 거짓 정상 발행을 방지 |

세 장은 서로 다른 결과 `True`/`False`/`Unavailable`를 모두 재현한다. #5와 #12는 `valid`/`test`가 섞인 비교가 아니라 각각 test/valid이므로 정식 성능 집계가 아니라 구동 예시로만 쓴다.

## 4. 코너케이스 목록

- #2: `bed_exit` 상태 라벨이지만 단일 프레임 관절·box는 ROI 내부다. 시간 전후 프레임 없이 state label을 raw Bool 정답으로 쓰면 안 된다.
- #4: 한 명으로 보이는 장면에서 MediaPipe 후보 연결이 `ambiguous_people`가 됐다.
- #7~#11: 누운 자세는 사전학습 pose가 관절을 충분히 내지 못해 정상임에도 판단 불가가 많다.
- #20: 병상 ROI annotation이 다른 빈 병상 표본보다 크고 이미지 하단까지 닿는다. 배포 ROI로 재사용할 수 없다.
- 20장 모두 같은 병실·카메라·피험자 시퀀스 계열이라 독립적인 일반화 검증 표본이 아니다.

## 5. 재현

검수 시트는 Git 제외 결과 폴더의 `review_sheet.jpg`이며, 다음 명령으로 원본 이미지와 `hospital_bed`/환자 box를 다시 그린다.

```bash
PYTHONPATH=src/part5_bed_exit .venv/bin/python \
  src/part5_bed_exit/research/poc/render_review_sheet.py \
  --summary src/part5_bed_exit/results/model_tests/archived_runs/pose_mediapipe_roboflow20_v1/summary.json \
  --output src/part5_bed_exit/results/model_tests/archived_runs/pose_mediapipe_roboflow20_v1/review_sheet.jpg
```

검수 시트 SHA-256: `45c531192446418ac115484c92cf502e01ab4b387d51cfe6312711e623a41d10`
