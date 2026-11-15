# 02. Pose·MediaPipe 실제 POC

## 1. 입력 자산과 환경

### Roboflow 데이터셋

- 데이터셋: Roboflow Bed Exit v4
- 로컬 경로: `src/part5_bed_exit/research/data/datasets/roboflow_bed_exit_v4/raw/`
- export 설명: 2025-08-08 01:40 GMT에 YOLOv8 형식으로 export
- 전처리: EXIF 방향 적용 후 제거, 512×512 stretch, augmentation 없음
- 클래스: `bed_exit`, `hospital_bed`, `in_bed`
- 분할: train 592, valid 168, test 88, 합계 848장
- 이미지/라벨 대응: 각 split에서 이미지 수와 라벨 수 동일, 총 848쌍
- `data.yaml` SHA-256: `ed64e64750307b8094216d0660dc312c48b8877e48d6b876c0b38fbd40c74a40`
- 라이선스 표기: CC BY 4.0
- 현재 셸의 `ROBOFLOW_API_KEY`는 unset이었으나 이미 존재하는 공식 export를 사용했으므로 추가 접근이나 우회는 수행하지 않았다.

### 격리 가상환경

원본 `/home/hyunwoong/venvs/rokey_venv`를 워크스페이스 `.venv/`로 복사했다.

```bash
cp -a /home/hyunwoong/venvs/rokey_venv .venv
.venv/bin/python -m pip install -r src/part5_bed_exit/research/requirements.txt
```

첫 설치 후 원본의 `include-system-site-packages=true` 때문에 로컬 Matplotlib 3.11.2와 시스템 `mpl_toolkits` 3.6 계열이 혼재했다. 복제본의 `.venv/pyvenv.cfg`만 `include-system-site-packages=false`로 변경하고 요구사항을 재동기화했다. 복제본의 `generate-parameter-library-py` 의존성 검사를 위해 `typeguard 4.6.0`도 설치했다.

최종 검증:

- Python 3.12.3
- OpenCV 4.11.0
- Matplotlib 3.11.2
- MediaPipe 0.10.21
- Torch 2.15.0+cu132
- Ultralytics 8.4.210
- PyAV 16.1.0
- `pip check`: `No broken requirements found`
- CUDA 사용 가능 여부: `False`
- 원본 `rokey_venv`: Matplotlib 3.6.3, MediaPipe 미설치 상태 유지

### 모델 가중치

| 모델 | 크기 | SHA-256 | 공식 다운로드 위치 |
|---|---:|---|---|
| `yolo11n-pose.pt` | 6,255,593 bytes | `869e83fcdffdc7371fa4e34cd8e51c838cc729571d1635e5141e3075e9319dc0` | Ultralytics assets v8.3.0 GitHub release |
| `pose_landmarker_full.task` | 9,398,198 bytes | `5134a3aad27a58b93da0088d431f366da362b44e3ccfbe3462b3827a839011b1` | Google MediaPipe pose landmarker full float16 v1 |

가중치와 source receipt는 `src/part5_bed_exit/models/`에 저장했으며 Git 제외 대상이다.

## 2. 실행 설계

- 재현 실행기: `research/poc/run_static_pose_poc.py`
- 고정 표본: `bed_exit` 6장, `in_bed` 6장, 빈 병상 8장
- 선택 순서: test → valid → train, 각 범주 내 파일명 오름차순
- 실제 결과: test 18장과 valid 2장
- 단일 ROI: 각 이미지의 가장 큰 Roboflow `hospital_bed` 박스
- 환자 초기 대응 박스: `bed_exit` 또는 `in_bed` 박스
- 모델 입력: OpenCV BGR 원본 512×512
- 모델별 warm-up 1회 후 CPU 순차 추론
- 출력: raw `judge(frame) -> bool`; 정적 이미지이므로 3/5 투표 미적용
- 빈 병상은 `False`가 아니라 판단 불가가 기대 동작이다.

이 ROI와 기대 bool은 POC용이다. 배포 카메라에서 사람이 검수한 ROI 또는 임상 정답으로 간주하지 않는다.

실행 명령:

```bash
PYTHONPATH=src/part5_bed_exit .venv/bin/python -m pytest \
  src/part5_bed_exit/test -q

PYTHONPATH=src/part5_bed_exit .venv/bin/python \
  -m research.poc.run_static_pose_poc \
  --output-dir src/part5_bed_exit/results/pose_mediapipe_roboflow20_v1
```

테스트 결과: `59 passed in 1.87s`.

## 3. 선택한 20장

| # | 범주 | split | image_id |
|---:|---|---|---|
| 1 | bed_exit | test | `frame-000086_png.rf.6d27d65cfcfa8f6702b8705a4db0e03a` |
| 2 | bed_exit | test | `frame-000114_png.rf.290ef9649c7a5ee41aa867e602d7aa53` |
| 3 | bed_exit | test | `frame-000152_png.rf.033463499d30b5bde61eab1e4cf3284e` |
| 4 | bed_exit | test | `frame-000162_png.rf.73643177102b831cb069cae9a5f6e3ca` |
| 5 | bed_exit | test | `frame-000217_png.rf.1e5e3fc3b018662cf183eeee102c7036` |
| 6 | bed_exit | valid | `frame-000026_png.rf.b952156e9ad5ac03cb527ddf4dcdfd87` |
| 7 | in_bed | test | `frame-000033_png.rf.60a95422c751237970517029c8bccdc3` |
| 8 | in_bed | test | `frame-000116_png.rf.a01c6e6e5b0707674ff92024a96774f9` |
| 9 | in_bed | test | `frame-000186_png.rf.beadfb004c42db9c67f0a5efaa7965bb` |
| 10 | in_bed | test | `frame-000259_png.rf.ad40fe1bdba6c562e3608a9d8c8c3b7d` |
| 11 | in_bed | test | `frame-000310_png.rf.5647d8d8d05fd2c2ac37d30e8bf2f601` |
| 12 | in_bed | valid | `frame-000025_png.rf.5ed2ed9dc6e8cfb86e753d5a995a2554` |
| 13 | empty_bed | test | `frame-000002_png.rf.27565d266f1baeb69b8ccf485ead745a` |
| 14 | empty_bed | test | `frame-000006_png.rf.af81e26e0528ab8874be98f5f1cb6676` |
| 15 | empty_bed | test | `frame-000010_png.rf.10dd809347123df58558fb8678cdd762` |
| 16 | empty_bed | test | `frame-000016_png.rf.e2ed32218c9b1d799d467f431bf1d0ce` |
| 17 | empty_bed | test | `frame-000019_png.rf.a61444a70c2d60bff2abaa0bbc71ff76` |
| 18 | empty_bed | test | `frame-000023_png.rf.b8e572c4ec918673863a67c84c5d93c2` |
| 19 | empty_bed | test | `frame-000034_png.rf.2f1ec40319c5e7d3c700a3e189c43ba1` |
| 20 | empty_bed | test | `frame-000035_png.rf.1decddbdbfdbe5c3ef2b2b0348fc44bb` |

## 4. 40개 실제 추론 결과

`N/A`는 판단 불가이며 정상 `False`로 대체하지 않았다.

| # | method | expected | predicted | ms | 판정 또는 실패 이유 |
|---:|---|---:|---:|---:|---|
| 1 | yolo_pose | true | true | 137.156 | `visible_keypoint_outside` |
| 2 | mediapipe_pose | true | N/A | 45.794 | `incomplete_body_coverage` |
| 3 | yolo_pose | true | N/A | 95.688 | `incomplete_body_coverage` |
| 4 | mediapipe_pose | true | N/A | 39.519 | `no_person` |
| 5 | yolo_pose | true | true | 106.022 | `visible_keypoint_outside` |
| 6 | mediapipe_pose | true | N/A | 48.520 | `patient_unassociated` |
| 7 | yolo_pose | true | true | 103.442 | `visible_keypoint_outside` |
| 8 | mediapipe_pose | true | N/A | 67.538 | `ambiguous_people` |
| 9 | yolo_pose | true | true | 96.844 | `visible_keypoint_outside` |
| 10 | mediapipe_pose | true | true | 80.703 | `visible_keypoint_outside` |
| 11 | yolo_pose | true | true | 395.740 | `visible_keypoint_outside` |
| 12 | mediapipe_pose | true | N/A | 51.590 | `incomplete_body_coverage` |
| 13 | yolo_pose | false | N/A | 90.670 | `incomplete_body_coverage` |
| 14 | mediapipe_pose | false | N/A | 39.363 | `no_person` |
| 15 | yolo_pose | false | N/A | 61.811 | `incomplete_body_coverage` |
| 16 | mediapipe_pose | false | N/A | 60.046 | `no_person` |
| 17 | yolo_pose | false | N/A | 64.695 | `incomplete_body_coverage` |
| 18 | mediapipe_pose | false | N/A | 46.583 | `incomplete_body_coverage` |
| 19 | yolo_pose | false | N/A | 70.134 | `no_person` |
| 20 | mediapipe_pose | false | N/A | 38.276 | `incomplete_body_coverage` |
| 21 | yolo_pose | false | N/A | 61.559 | `incomplete_body_coverage` |
| 22 | mediapipe_pose | false | N/A | 44.843 | `incomplete_body_coverage` |
| 23 | yolo_pose | false | false | 105.150 | `keypoints_inside` |
| 24 | mediapipe_pose | false | false | 37.606 | `keypoints_inside` |
| 25 | yolo_pose | N/A | N/A | 52.964 | `no_person` |
| 26 | mediapipe_pose | N/A | N/A | 16.793 | `no_person` |
| 27 | yolo_pose | N/A | N/A | 53.097 | `no_person` |
| 28 | mediapipe_pose | N/A | N/A | 16.725 | `no_person` |
| 29 | yolo_pose | N/A | N/A | 61.543 | `no_person` |
| 30 | mediapipe_pose | N/A | N/A | 16.680 | `no_person` |
| 31 | yolo_pose | N/A | N/A | 69.349 | `no_person` |
| 32 | mediapipe_pose | N/A | N/A | 17.008 | `no_person` |
| 33 | yolo_pose | N/A | N/A | 65.626 | `no_person` |
| 34 | mediapipe_pose | N/A | N/A | 16.794 | `no_person` |
| 35 | yolo_pose | N/A | N/A | 59.316 | `no_person` |
| 36 | mediapipe_pose | N/A | N/A | 15.322 | `no_person` |
| 37 | yolo_pose | N/A | N/A | 55.929 | `no_person` |
| 38 | mediapipe_pose | N/A | N/A | 15.703 | `no_person` |
| 39 | yolo_pose | N/A | N/A | 54.959 | `no_person` |
| 40 | mediapipe_pose | N/A | N/A | 14.843 | `no_person` |

요약:

| 방법 | 유효 판정 | 판단 불가 | 평균 ms | 중앙값 ms | P95 ms | 유효 판정 중 라벨 proxy 일치 |
|---|---:|---:|---:|---:|---:|---:|
| YOLO Pose | 6/20 | 14/20 | 93.085 | 67.488 | 150.085 | 6/6 |
| MediaPipe Pose | 2/20 | 18/20 | 36.512 | 38.820 | 68.196 | 2/2 |

유효 판정만의 6/6·2/2는 정확도가 아니다. 판단 불가 표본을 제외한 선택 편향 결과이며, 오히려 핵심 결과는 누운 자세·부분 가림에서 유효 판정률이 YOLO Pose 30%, MediaPipe Pose 10%에 불과하다는 점이다. 빈 병상 8장은 두 방법 모두 `no_person`으로 처리해 거짓 정상 Bool을 생성하지 않았다.

MediaPipe 로그의 EGL 실패는 GPU delegate 사용 불가를 의미하며 CPU XNNPACK delegate 생성 후 40행 산출이 완료됐다.

## 5. 산출물과 복구

Git 추적 산출물:

- `research/poc/run_static_pose_poc.py`
- `test/test_static_pose_poc.py`
- 이 보고서

Git 제외 실행 산출물:

- `src/part5_bed_exit/results/pose_mediapipe_roboflow20_v1/results.csv`
- `src/part5_bed_exit/results/pose_mediapipe_roboflow20_v1/summary.json`
- `.venv/`
- `src/part5_bed_exit/models/`
- Roboflow 원본 export

재실행기는 기존 output directory 덮어쓰기를 거부한다. 재현할 때는 새 버전 디렉터리를 지정하거나 기존 결과를 별도 보관한 뒤 실행한다. 결과 CSV는 사용자 요청의 `method`, `model`, `dataset/image_id`, `preprocessing`, `ROI version`, `expected bool`, `predicted bool`, `inference time`, `failure reason`, `notes`를 모두 포함한다.
