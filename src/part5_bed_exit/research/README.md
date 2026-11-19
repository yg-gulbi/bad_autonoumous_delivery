# Part 5 침대 이탈 감지 모듈 (`part5_bed_exit`)

웹캠의 BGR 이미지(`numpy` 배열)를 선택된 판단 객체의 `judge(frame)` 메서드에 전달한다. 반환값은 Python `bool`이어야 한다 (`True`: 이탈 감지 / unsafe, `False`: 정상 / safe). 신체 인식, ROI 설정, 모델 초기화 및 전처리는 각 메서드 클래스가 담당한다.

최근 5개 프레임 중 `True`가 3개 이상이면 내부 `state.unsafe`와 출력이 `True`가 된다. 초기 4개 프레임은 `False`이며, 정상 판단된 프레임마다 `/patient/bed_exit` (`std_msgs/msg/Bool`)을 발행한다.

## 네 사전학습 클래스와 로컬 노트북

두 노트북의 첫 실행 코드 셀은 현재 커널의 의존성을 점검하고 필요한 패키지를 설치한다. 설치가 발생하면 `Kernel → Restart Kernel`로 재시작한 뒤 첫 셀부터 다시 실행한다. Matplotlib 3.6.3과 matplotlib-inline 0.2.x가 섞이면 `RcParams._get` 오류가 발생하므로 Matplotlib은 3.10 이상을 사용한다. 디스크의 패키지를 업데이트해도 이미 실행 중인 커널에는 이전 모듈이 남아 있어 재시작이 필요하다.

**이미지 한 장으로 먼저 실행하려면 [part5_single_image.ipynb](notebooks/part5_single_image.ipynb)를 연다.** 공개 Bed Exit 예시 이미지를 `research/data/sample/bed_exit_sample.jpg`로 자동 다운로드하고 같은 BGR 프레임을 네 클래스에 전달한다. 원본 크기는 1920×1080이며 출처는 [helppai / Bed Exit](https://universe.roboflow.com/helppai/bed-exit), 제공자가 표시한 라이선스는 CC BY 4.0이다. 이 공개 예시의 v4 split 소속은 확인하지 않았다.

이 노트북의 ROI·환자 선택 박스는 해당 샘플의 연결 확인용 예시다. 한 이미지의 다수결 전 bool만 비교하며 영상 시간 지표나 5프레임 성공으로 보고하지 않는다. 현재 커널에 MediaPipe가 없으면 노트북 첫 안내의 설치 명령을 실행하고 커널을 재시작한다.

Roboflow v4 export가 로컬에 있으면 고정 20장에 YOLO Pose와 MediaPipe Pose를 실제 실행해 프레임 원판정, 시간, 실패 이유를 CSV로 남길 수 있다. `hospital_bed` 박스는 이 정적 POC의 이미지별 ROI일 뿐 배포 카메라의 검수 완료 ROI가 아니다.

```bash
PYTHONPATH=src/part5_bed_exit .venv/bin/python -m research.poc.run_static_pose_poc \
  --output-dir src/part5_bed_exit/results/model_benchmarks/roboflow_pose_20_images_rerun
```

실행기는 Bool·시간·실패 이유뿐 아니라 Pose 추론 근거를 함께 저장한다.

- `yolo_pose/*.jpg`: YOLO Pose 관절·스켈레톤·ROI·판정 이미지 20장
- `mediapipe_pose/*.jpg`: MediaPipe Pose 관절·스켈레톤·ROI·판정 이미지 20장
- `comparison/*.jpg`: 동일 입력의 두 모델 좌우 비교 이미지 20장
- `pose_comparison_sheet.jpg`: 20개 좌우 비교 결과를 한 장으로 모은 검수 시트
- `keypoints.jsonl`: 전체 pose 후보, 선택 환자, 관절 좌표·confidence, 공통 관절과 ROI 이탈 여부 40행
- `results.csv`, `summary.json`: 기존 판정·시간·실패 원인과 실행 환경

시각화 색상은 공통 관절 ROI 내부=초록, 외부=빨강, 비교에 쓰지 않는 관절=청록,
선택되지 않은 pose 후보=회색이다. `UNAVAILABLE` 이미지도 검출된 후보가 있으면 관절을
보존하므로 `incomplete_body_coverage`, `patient_unassociated`, `ambiguous_people`를 직접
검토할 수 있다. 빈 병상의 `no_person`은 ROI만 표시된다.

이불 가림·비가림 이미지 두 장처럼 임의의 RGB pair만 확인하려면 다음 실행기를 사용한다.

```bash
PYTHONPATH=src/part5_bed_exit .venv/bin/python \
  -m research.poc.run_pose_pair_poc \
  --uncovered /path/to/uncovered.png \
  --covered /path/to/covered.png \
  --roi-config /path/to/reviewed-pair-roi.json \
  --output-dir src/part5_bed_exit/results/model_benchmarks/slp_pose_pair_2_images_rerun
```

이 실행기는 입력을 같은 표시 폭으로 확대하고 `roi_reviewed=true`인 단일 Bed ROI 설정을
필수로 사용한다. ROI는 pair 이미지에서 직접 검수한 정규화 다각형이어야 하며 배포 카메라
ROI와 구분한다. 모델별 이미지, 좌우 비교, 관절 JSONL, CSV와 제한사항을 기록한 summary를
생성하며 기존 결과 디렉터리를 덮어쓰지 않는다.

[notebooks/part5_bed_exit.ipynb](notebooks/part5_bed_exit.ipynb)를 실행하면 모델 다운로드부터 데이터셋 접근·다운로드, ROI 확인, 영상/웹캠 실행, 이벤트 정량평가까지 진행할 수 있다. 학습은 하지 않는다.

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -r src/part5_bed_exit/research/requirements.txt
python -m ipykernel install --user --name part5-bed-exit
jupyter lab src/part5_bed_exit/research/notebooks/part5_bed_exit.ipynb
```

커널을 `part5-bed-exit`로 선택한다. ROS와 함께 사용할 때는 ROS Jazzy를 source한 셸에서 Jupyter/노드를 실행한다. 노트북의 `MODEL_DIR`을 이미 받은 가중치 폴더로 지정하면 재사용할 수 있다. 가중치·데이터·결과는 패키지 내부 `.gitignore`로 제외한다.

입력 영상은 데이터셋 셀의 기본 `DATASET_ACTION='auto_video'`로 연결한다. `data/`와 `BED_EXIT_DATA_DIR`에 있는 원본 영상을 자동 탐색하고, 없으면 FallVision 목록에서 원본 RGB 침대 MP4/ZIP을 찾아 다운로드·압축 해제·선택한다. 선택 경로를 출력하고 다음 셀에서 첫 프레임을 표시한다. 마스크/관절 시각화·Depth·IR·처리 결과 경로는 자동 선택에서 제외한다. 파일명 기반 선택이므로 실제 원본 RGB 여부는 표시된 화면과 출처로 확인한다.

모델 가중치 다운로드는 입력 영상 다운로드와 별도다. 제공처가 봇 확인·인증·크기 제한으로 다운로드를 허용하지 않으면 실패 이유와 공식 주소를 출력한다. 수동으로 확보한 원본은 `data/fallvision/raw/`에 두면 다음 실행에서 연결한다. 원본/가공본 구분이 불명확한 파일은 임의 다운로드하지 않는다. 영상 연결은 ROI나 이벤트 정답을 검수 완료로 바꾸지 않는다.

| `method` 선택값 | 클래스 | 사전학습 인식 → 원판정 |
| --- | --- | --- |
| `detection` | `DetectionROI` | YOLO11n 사람 박스 → 단일 ROI 밖 박스 면적 비율 |
| `segmentation` | `SegmentationROI` | YOLO11n-seg 사람 마스크 → 단일 ROI 밖 신체 면적 비율 |
| `yolo_pose` | `YoloPoseROI` | YOLO11n-pose COCO 관절 → 단일 ROI 밖 공통 관절 |
| `mediapipe_pose` | `MediaPipePoseROI` | MediaPipe Full 33 관절 → 단일 ROI 밖 대응 관절 |

네 클래스는 [methods/bed_exit_core.py](../part5_bed_exit/methods/bed_exit_core.py)에 있으며 모두 기존 `BedExitMethod` 인터페이스를 상속한다. 공통 호출부는 클래스별 분기 없이 `judge(frame)`을 사용한다.

```python
from part5_bed_exit.bed_exit_method import BedExitMethod
from part5_bed_exit.pretrained import create_method

detector: BedExitMethod = create_method('methods.local.json', 'yolo_pose')
try:
    unsafe = detector.judge(bgr_frame)  # 유효 프레임: Python bool, 다수결 전
finally:
    detector.close()
```

정상 출력은 `SAFE=False`, `EXIT=True` 두 가지다. 사람/관절 미검출, 모호한 환자 선택, 잘못된 프레임은 `JudgmentUnavailable` 예외다. ROS가 이를 잡아 이력을 초기화하고 발행을 생략한다. 모델은 무인 이탈 허가 여부를 판단하지 않는다.

운영 단일 ROI는 카메라별로 지정한다. [examples/methods.template.json](../examples/methods.template.json)을 `methods.local.json`으로 복사하고 실제 화면의 정규화 다각형 좌표로 교체한다. `model_dir` 상대 경로는 JSON 파일 위치를 기준으로 해석한다.

기본 `any_body`는 신체 일부의 이탈을 보는 실험 정책이다. Detection/Segmentation/Pose가 보는 신체 표현은 다르므로 팔만 나옴·이불 가림·몸통 이탈을 각각 평가한다. `outside_fraction=0.05`, `point_conf=0.5`, `tolerance_px=3.0`은 미검증 초기값이다. 추가 상태나 행동 분류기는 넣지 않았다.

### 운영 ROS 노드와 네 방법 비교의 경계

운영 ROS 노드는 `method_config`의 `method`로 모델 하나를 선택한다. 네 방법을 동시에
비교하는 실행은 이 디렉터리의 벤치마크 도구에서만 수행한다. 운영 실행에는 실제 검수한
`method_config`를 지정한다.

```bash
source /opt/ros/jazzy/setup.bash
colcon build --packages-select part5_bed_exit --symlink-install
source install/setup.bash
ros2 run part5_bed_exit bed_exit --ros-args \
  -p method_config:="$(pwd)/src/part5_bed_exit/examples/methods.local.json" \
  -p output_topic:=/patient/bed_exit \
  -p camera_index:=0 \
  -p capture_fps:=30.0
```

`BedExitMonitor`는 노트북 미리보기용으로 투표를 포함한다. ROS에는 원판정 클래스를 연결해 투표를 한 번만 적용한다. 메서드의 모델 초기화·전처리는 객체가 담당하고 공통 노드는 카메라·투표·발행만 담당한다.

### 데이터셋과 정량평가

[datasets/dataset_catalog.json](datasets/dataset_catalog.json)에 기존 조사와 출처를 보존했다. 노트북은 Roboflow v4 키 기반 다운로드, SLP 조건별 RGB 샘플 Range 추출, FallVision 파일 목록 조회·선택 다운로드·ZIP 해제를 제공한다. SLP 비밀번호/Roboflow 키는 환경변수로만 받는다. 접근 제한 자료는 우회하지 않으며 제공자가 허용한 파일을 로컬로 사용한다. 다운로드 실패를 성공으로 기록하지 않는다.

정적 이미지에는 이벤트 시각 정답이 없다. 실제 평가는 [templates/testbed.template.json](templates/testbed.template.json)을 복사해 원본 영상 해시·ROI·참가자/세션 분리·위험 시작/경계 이탈/종료·환자 존재 구간을 검수한 뒤 실행한다. `events=[]`는 검수된 정상 영상에만 사용한다.

평가 결과는 `results/<run>/comparison.csv`, `comparison.json`, 메서드/클립별 `frames.csv`, `summary.json`, 조건별 `conditions.json`에 저장된다. 모델별로 순차 실행하며 Event Precision/Recall, Early Warning Recall, FA/10m·FA/h, Lead Time, Delay P95, FPS, 추론 P95, 시간 가중 Tracking Lost Rate를 계산한다. 연속 경보는 한 이벤트로 집계하고 유효 원판정 SAFE 5회 연속 후 재무장한다. `three_consecutive_exit`은 원판정 3회 연속 여부를 비교하기 위한 분석 열일 뿐이며, ROS 공개 Bool은 항상 최근 5프레임 중 3회 규칙을 사용한다.

`signal_only`는 영상 시각 기준, 기본 지표는 처리 시간이 누적되는 FIFO 시뮬레이션 기준이다. 실제 ROS 웹캠 프레임 유실·정지 명령 지연을 실측한 결과는 아니다. 분모가 없으면 지표/목표 통과는 `null`이다. Notion 목표값은 제안이며 실측 성능으로 취급하지 않는다. 5 FPS에서는 시작 후 5프레임 수집만 약 0.8초여서 Delay P95 0.1초 목표와 양립하기 어렵다.

구현 근거: [Notion 요구사항](https://app.notion.com/p/419e8369193381589056f77090c43086), [네 방법론](https://app.notion.com/p/418e836919338100aa5ffb0eb12345cb), [데이터셋 조사](https://app.notion.com/p/419e8369193381018cbad16a32a4af98), [Ultralytics BGR/Results API](https://docs.ultralytics.com/modes/predict/), [MediaPipe API](https://ai.google.dev/edge/mediapipe/solutions/vision/pose_landmarker/python).

재사용 출처는 `/home/hv-02/rokey_ws/vision/bed_exit`의 네 전략·다운로드·평가 코드다. 원본은 변경하지 않았다. 통합본에서는 판단 불가 시 이전 bool을 반환하던 동작을 예외로 바꾸고, 집계기를 기존 `BedExitState`로 교체했다. Figma 내용은 수정하지 않았으며 도구 호출 한도로 현행 보드 대조가 제한되었다. 토픽/타입은 이 저장소 `AGENTS.md`의 확정 계약을 따른다.

## 메서드 클래스 구성 및 객체 선정

공통 인터페이스(`part5_bed_exit.bed_exit_method.BedExitMethod`)를 상속하거나 `judge(self, frame) -> bool` 메서드를 가진 클래스로 구현한다. 팀원 구현용 단일 클래스 양식은 `part5_bed_exit/method_template.py`의 `BedExitMethodTemplate`을 참고한다.

```python
from part5_bed_exit.bed_exit_method import BedExitMethod


class YourBedExitMethod(BedExitMethod):
    def __init__(self) -> None:
        # 모델 가중치 로드, ROI, 객체 내부 상태를 초기화한다.
        ...

    def judge(self, frame) -> bool:
        # BGR 이미지를 판정한 결과를 Python bool로 반환한다.
        return bool(...)

    def close(self) -> None:
        # 필요 시 노드 종료 시점에 리소스를 정리한다.
        ...
```

- **운영 객체 선정**: 노드는 검수 설정으로 `SegmentationROI`를 생성하며 임의 클래스 경로를 받지 않는다.
- **코드 직접 주입**: 단위 테스트에서는 `BedExitNode(detector=FakeDetector())`처럼 테스트 대역을 전달할 수 있다.
- 실제 판단 알고리즘(YOLO 추론 등)은 이 공통 노드 골격과 분리해 각 메서드 클래스 내부에 구현한다.

## 빌드와 실행

ROS 2 Jazzy를 `source`한 뒤 `bad_ws` 워크스페이스 루트에서 실행한다.

```bash
source /opt/ros/jazzy/setup.bash
colcon build --packages-select part5_bed_exit --symlink-install
source install/setup.bash
ros2 run part5_bed_exit bed_exit --ros-args \
  -p method_config:="$(pwd)/src/part5_bed_exit/examples/methods.local.json" \
  -p output_topic:=/patient/bed_exit \
  -p camera_index:=0 \
  -p capture_fps:=30.0
```

`capture_fps`는 프레임 수집 시도 주기다. 실제 처리율은 카메라와 메서드 실행 시간에 제한되며, 호출은 순차 처리되며 모든 센서 프레임 처리를 보장하지 않는다.

카메라 수신 실패 또는 판단 예외·잘못된 반환값은 로그로 남기고 5프레임 이력을 초기화한다. 오류를 정상 판정(`False`)으로 바꾸어 발행하지 않으며 해당 프레임의 토픽은 발행하지 않는다. 복구 후 다시 5프레임을 수집한다.

## ROS 2 없이 메서드 클래스 비교 테스트 (`compare_methods.py`)

나와 팀원이 만든 여러 메서드 클래스(`judge(frame) -> bool`)를 ROS 2 실행 없이 웹캠이나 영상 파일로 바로 비교할 때는 `compare_methods.py`를 사용한다.

1. [compare_methods.py](evaluation/compare_methods.py) 상단의 `METHODS` 딕셔너리에 비교할 메서드 클래스나 객체를 등록한다.
2. 필요 시 `SELECTED_METHODS`에 실행할 키 목록(또는 전체 실행 시 `None`)과 `DEFAULT_SOURCE`를 지정한다.
3. 워크스페이스 루트(`bad_ws/`)에서 실행한다.

```bash
python3 src/part5_bed_exit/research/evaluation/compare_methods.py --source 0
# 또는 영상 파일로 화면 없이 요약표만 확인
python3 src/part5_bed_exit/research/evaluation/compare_methods.py --source sample.mp4 --no-show
# 네 사전학습 방법을 설정 파일로 연결 (동시 비교용, 정식 속도 평가는 노트북에서 독립 실행)
python3 src/part5_bed_exit/research/evaluation/compare_methods.py \
  --config src/part5_bed_exit/examples/methods.local.json \
  --methods detection segmentation yolo_pose mediapipe_pose --source sample.mp4 --no-show
```
