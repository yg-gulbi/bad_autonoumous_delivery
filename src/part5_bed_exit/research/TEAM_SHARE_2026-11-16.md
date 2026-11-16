# Part 5 Pose·MediaPipe POC 팀 공유

기준일: 2026-11-16

## 한 줄 결론

Roboflow 20장 정적 POC에서 YOLO Pose는 6/20, MediaPipe Pose는 2/20만 유효 Bool을 냈다. 유효 판정은 검수 초안과 일치했지만 판단 불가율이 각각 70%, 90%이므로 현재 결과는 정확도 근거가 아니라 누운 자세 pose 검출 한계를 확인한 POC다.

## 구현 계약

- 배포 입력 영역: 단일 `roi`
- 모델 원판정: `judge(frame) -> bool` 또는 `JudgmentUnavailable`
- 공개 ROS Bool: 유효한 최근 5 frame 중 3회 `True`
- 분석 전용 지표: 3 frame 연속 `True`; 공개 상태와 분리
- 판단 불가: 이력 초기화, 거짓 정상 `False` 발행 금지
- 공개 MVP 상태: Bool만 사용; `TRACKING_LOST`, `AUTHORIZED_EXIT` 미노출

## 데이터와 모델

- 데이터: Roboflow Bed Exit v4, 848장(train 592/valid 168/test 88), 512×512 JPEG
- 클래스: `bed_exit`, `hospital_bed`, `in_bed`
- POC: test 18장 + valid 2장; bed_exit 6, in_bed 6, 빈 병상 8
- YOLO Pose: `yolo11n-pose.pt`, COCO 17관절 중 공통 13관절 사용
- MediaPipe: `pose_landmarker_full.task`, 33관절 중 동일한 공통 13관절 사용
- POC ROI: 이미지별 `hospital_bed` 최대 box. 배포 ROI가 아님

## 실제 결과

| 방법 | 유효 Bool | 판단 불가 | 평균/중앙값/P95 ms | 유효 결과 |
|---|---:|---:|---:|---|
| YOLO Pose | 6/20 (30%) | 14/20 (70%) | 93.085 / 67.488 / 150.085 | 검수 초안 6/6 일치 |
| MediaPipe Pose | 2/20 (10%) | 18/20 (90%) | 36.512 / 38.820 / 68.196 | 검수 초안 2/2 일치 |

유효 결과 일치는 전체 정확도가 아니다. 20장이 한 촬영 시퀀스 계열이고 유효 판정만 남긴 선택 편향이 있다. 빈 병상 8장은 두 방법 모두 `no_person` 판단 불가로 처리해 `False`를 생성하지 않았다.

주요 실패는 누운 자세의 `incomplete_body_coverage`, `no_person`, 환자 연결 실패와 후보 모호성이다. MediaPipe는 더 빨랐지만 이 표본에서 유효 판정률이 더 낮았다.

## 대표 3장과 코너케이스

- 이탈: `frame-000217_png.rf.1e5e3fc3b018662cf183eeee102c7036`; 두 방법 `True`
- 정상: `frame-000025_png.rf.5ed2ed9dc6e8cfb86e753d5a995a2554`; 두 방법 `False`
- 빈 병상: `frame-000002_png.rf.27565d266f1baeb69b8ccf485ead745a`; 두 방법 판단 불가
- 상태-geometry 불일치: `frame-000114...`는 원본 `bed_exit`지만 단일 frame ROI 기준 정상이고 상체를 세운 조기징후 후보다.
- annotation outlier: `frame-000035...`의 병상 box는 이미지 하단까지 닿는다.

## 라벨 사용 결정

Roboflow 상태 라벨은 원본으로 보존하고 표본 층화·검수 대상으로만 사용한다. 네 사전학습 방법의 입력 class로 직접 사용하지 않는다. 사람 geometry가 필요하면 시각 검수한 derived dataset에서 `bed_exit`/`in_bed` box를 `person`으로 병합하며 원본을 덮어쓰지 않는다.

## 담요 조건

현재 Roboflow에는 담요 상태 label이 없고 검수 20장에는 담요 가림 장면이 없다. 담요 성능 주장은 불가능하다. 후속 SLP 또는 BlanketSet 검토 시 RGB-only, 실제 RGB-D, 실제 압력 track을 분리한다. RGB에서 생성한 pseudo-depth는 성능 또는 안전성 근거로 사용하지 않는다.

## 실제 YOLO 단일 프레임 테스트 결과 (`DetectionBedExitMethod` · `SegmentationBedExitMethod`)

실행 환경: `/home/mu-02/venvs/rokey_venv/bin/python` (`ultralytics 8.4.208`, `torch 2.15.0`, `opencv-python 4.9.0.80`, CPU 추론 `device='cpu'`, `imgsz=480`, `conf=0.35`).  
사용 가중치(`src/part5_bed_exit/models/`, Git 제외): `yolo11n.pt` (5.4 MB), `yolo11n-seg.pt` (5.9 MB).  
입력 이미지: `src/part5_bed_exit/test_images/` (이미지별 `.roi.json` 사이드카 적용).

| 이미지 | 메서드 | 판정 | 세부 지표 | 신뢰도(`conf`) | 1차 실행 시간 | 2차(웜) 실행 시간 |
| --- | --- | --- | --- | ---: | ---: | ---: |
| `01_uncovered_slp.jpg` (비가림) | `detection` (`yolo11n.pt`) | `SAFE(False)` | `outside=0.4%` (`overlap=99.6%`), `center=IN` | `0.87` | `1411.4ms` | `994.0ms` |
| `01_uncovered_slp.jpg` (비가림) | `segmentation` (`yolo11n-seg.pt`) | `SAFE(False)` | `outside=0.0%` (`thr=10%`) | `0.85` | `71.5ms` | `59.0ms` |
| `02_covered_arm_out_slp.jpg` (이불 가림) | `detection` (`yolo11n.pt`) | `SAFE(False)` | `outside=0.0%` (`overlap=100.0%`), `center=IN` | `0.54` | `19.3ms` | `22.5ms` |
| `02_covered_arm_out_slp.jpg` (이불 가림) | `segmentation` (`yolo11n-seg.pt`) | `SAFE(False)` | `outside=0.0%` (`thr=10%`) | `0.59` | `24.9ms` | `24.1ms` |

- **집계 요약 (2차 실행 기준)**: `detection` `Valid=2, Err=0, Exit=0, Safe=2, Avg=508.28ms (2.0 FPS)` / `segmentation` `Valid=2, Err=0, Exit=0, Safe=2, Avg=41.56ms (24.1 FPS)` / 메서드 간 불일치 `0/2` (`100.0%` 일치). 첫 프레임 `detection` 소요 시간에는 PyTorch 모델 초기화 비용이 포함됨.
- **시각화 산출물 확인 (`src/part5_bed_exit/outputs/`, Git 제외)**:
  - `outputs/detection/01_uncovered_slp.png`, `outputs/segmentation/01_uncovered_slp.png`: 비가림 전신 BBox·중심점과 전신 세그멘테이션 마스크가 모두 침대 ROI 내부 녹색으로 정확히 검출됨.
  - `outputs/detection/02_covered_arm_out_slp.png`, `outputs/segmentation/02_covered_arm_out_slp.png`: 이불에 가려진 상태에서도 드러난 머리·상체·팔 영역을 사람으로 검출해 ROI 내부 `SAFE(False)`로 정상 판정함.
- **한계**: 위 2장은 모두 침대 내부 정상 자세(`SAFE`) 표본이며, 실제 침대 이탈(`EXIT`) 프레임, 다중 인물(방문자 동시 등장), 연속 5프레임 다수결 영상 및 ROS 실카메라 구동은 아직 미검증.

## 검증 상태

- 정리 후 단위 테스트(`PYTHONPATH=src/part5_bed_exit /home/mu-02/venvs/rokey_venv/bin/python -m pytest src/part5_bed_exit/test -v`): **26 passed in 0.19s** (`test_bed_exit_method.py`, `test_bed_exit_state.py`, `test_compare_methods.py`, `test_compare_single_frame.py`, `test_methods.py`)
- 실제 YOLO 단일 프레임 순전파(`compare_single_frame.py`): 2장 × 2방법 = 4건 추론·시각화 저장 완료 (`Err=0`, `100.0%` 일치)
- 이전 Pose·MediaPipe POC 기록: 독립 `.venv` 60건 `colcon test` 통과, Roboflow 20장 정적 추론 40행 CSV 완료 (관련 연구 스크립트는 브랜치 정리 단계에서 제거됨)
- 미검증 범위: 실제 이탈(`EXIT`) 장면, 실제 영상 3/5 시간 투표, 배포 카메라 ROI, 장시간 ROS 노드 구동

## 재현 명령

```bash
# 1) Part 5 단위 테스트 (26건)
PYTHONPATH=src/part5_bed_exit python3 -m pytest src/part5_bed_exit/test -v

# 2) 실제 YOLO 모델 단일 프레임 순전파·시각화 비교 (test_images/ -> outputs/)
/home/mu-02/venvs/rokey_venv/bin/python src/part5_bed_exit/compare_single_frame.py
```

## 다음 팀 결정

1. 배포 카메라의 단일 ROI 좌표 검수·버전 확정
2. 시간 연속 영상으로 3/5 공개 판정과 3연속 분석 지표 비교
3. 담요 보충 데이터셋·라이선스·합격 기준 확정
4. 누운 자세에서 Pose 판단 불가율을 허용할지 Detection/Segmentation fallback을 결합할지 결정
