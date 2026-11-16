# Part 5 실시간 웹캠 실행 중 `Tracked patient lost` 연쇄 오류 및 판정 이력 초기화

- 프로젝트: `bad_ws` (`part5_bed_exit`)
- 발생일: `2026-11-16`
- 기록일: `2026-11-16`
- 상태: 검증 대기
- 관련 파일·Issue·PR·커밋: `src/part5_bed_exit/part5_bed_exit/methods/common.py`, `src/part5_bed_exit/part5_bed_exit/methods/segmentation_method.py`, `src/part5_bed_exit/test/test_segmentation_method.py`, `src/part5_bed_exit/part5_bed_exit/bed_exit_node.py`

## 1. 문제정의

### 기술적으로 관찰한 사실

사용자가 가상환경(`rokey_venv`) 경로를 포함한 환경에서 아래 명령으로 실시간 웹캠 기반 노드를 실행했을 때 `[ERROR] [...] [bed_exit]: Judgment failed; history reset: Tracked patient lost` 로그가 연쇄적으로 출력되었다.

```bash
source /opt/ros/jazzy/setup.bash
source install/setup.bash
PYTHONPATH=/home/mu-02/venvs/rokey_venv/lib/python3.12/site-packages:$PYTHONPATH \
  ros2 run part5_bed_exit bed_exit --ros-args \
    -p method:=part5_bed_exit.methods.segmentation_method:SegmentationBedExitMethod
```

- 관찰된 오류 버스트(로그 타임스탬프 기준 약 30 FPS 간격 `~33 ms`):
  - `1794812221.595` ~ `1794812222.162` (간헐적 4회)
  - `1794812242.361` ~ `1794812242.495` (연속 5프레임)
  - `1794812261.728` ~ `1794812261.795` (연속 3프레임)
  - `1794812263.361` ~ `1794812263.496` (연속 5프레임)
- `BedExitNode`는 `detector.judge(frame)`에서 예외가 발생하면 `self.state = BedExitState()`로 5프레임 다수결 이력을 초기화하고 퍼블리시를 건너뛴다.

### 원인

`PatientTracker.select()`(`src/part5_bed_exit/part5_bed_exit/methods/common.py`)가 `self.previous_box` 잠금 이후 다음 프레임 후보를 오직 `box_iou(self.previous_box, boxes[idx]) >= min_track_iou` (`0.15`) 조건만으로 매칭했다.

1. 대상자가 빠르게 움직이거나 자세·박스 비율이 급변해 단 1프레임만 `IoU < 0.15`가 되어도 `_register_loss('Tracked patient lost')`가 즉시 예외를 발생시켰다.
2. 예외가 발생하면 `self.previous_box`가 갱신되지 않고 이전 좌표에 고정되므로, 사람이 계속 이동한 상태에서는 이후 프레임도 정지된 `previous_box`와 비교되어 `max_lost_frames` (`5`프레임) 동안 연속으로 실패한 뒤에야 `reset()`이 호출되었다(로그의 연속 5프레임 버스트와 일치).

## 2. 해결방법 안들 설정

| 해결안 | 선택 또는 보류 이유 | 결과 |
| --- | --- | --- |
| 매 프레임 `previous_box` 없이 `bed_roi` 겹침만으로 환자 선택 | 환자가 침대 경계 밖으로 나간 뒤 새 방문자가 침대에 들어오면 추적이 방문자로 넘어갈 수 있음 | 보류 |
| IoU 매칭 실패 시 예외 없이 아무 사람이나 무조건 재선택 | 침대 밖에 먼 사람만 남은 빈 침대 상황에서도 잘못된 추적이 이어질 수 있음 | 보류 |
| 1차 `IoU >= 0.15`, 2차 정규화 중심 거리 `<= max_center_jump` (`0.25`), 3차 `bed_roi` 겹침 재획득(`_select_on_bed`) 순으로 매칭하고 모두 실패할 때만 `Tracked patient lost` 유지 | 방문자 드리프트 방지와 안전 예외 정책을 유지하면서도 급격한 자세·위치 변화와 침대 복귀 시 연쇄 실패를 제거할 수 있음 | 선택 및 반영 (단위 테스트 통과, 실웹캠 재검증 대기) |

## 3. 해결방법 실행

AI가 `src/part5_bed_exit/` 내부 코드와 단위 테스트를 수정하고 패키지 빌드를 수행했다.

1. `src/part5_bed_exit/part5_bed_exit/methods/common.py`:
   - 프레임 대각선 길이(`np.hypot(h, w)`)로 정규화한 바운딩 박스 중심 거리를 계산하는 `_normalized_center_distance` 추가
   - `PatientTracker`에 `max_center_jump: float = 0.25` 파라미터 및 `_select_on_bed`, `_match_previous` 헬퍼 추가
   - `BaseYoloBedExitMethod.__init__`에 `max_center_jump: float = 0.25` 전달 추가
2. `src/part5_bed_exit/part5_bed_exit/methods/segmentation_method.py`:
   - `SegmentationBedExitMethod.__init__`에 `max_center_jump: float = 0.25` 파라미터 전달 추가
3. `src/part5_bed_exit/test/test_segmentation_method.py`:
   - `test_patient_tracker_handles_rapid_shift_bed_reacquire_and_far_loss` 단위 테스트 추가(IoU=0 급변동 추적 유지, 침대 위 즉시 재획득, 침대 밖 원거리 인원만 있을 때 `Tracked patient lost` 및 `reset()` 유지 검증)

### 검증

- 실행한 확인:
  - `PYTHONPATH=src/part5_bed_exit python3 -m pytest src/part5_bed_exit/test -v`
  - `colcon build --packages-select part5_bed_exit --symlink-install`
- 실제 결과:
  - 단위 테스트 `23 passed in 0.27s` (추가한 추적·재획득·손실 테스트 포함 전체 통과)
  - `colcon build` 종료 코드 0 (`Finished <<< part5_bed_exit [1.15s]`)
- 남은 한계:
  - 수정 후 실제 USB/내장 웹캠에서 `ros2 run part5_bed_exit bed_exit`를 다시 실행한 라이브 동작 확인은 아직 수행하지 않았다(사용자 재실행 확인 대기).

## 4. 경험의 재해석

### 내가 실제로 경험하고 느낀 것

사용자가 `ros2 run part5_bed_exit bed_exit --ros-args -p method:=part5_bed_exit.methods.segmentation_method:SegmentationBedExitMethod` 실행 중 `[ERROR] ... Judgment failed; history reset: Tracked patient lost`가 여러 구간에서 연속 발생하는 현상과 로그를 직접 보고했다(별도의 주관적 소감은 기록되지 않음).

### AI 해석·제안

- 기존 로직이 `IoU >= 0.15` 단일 조건만 허용하고 실패 시 `previous_box`를 고정해 두었기 때문에, 웹캠 테스트처럼 카메라 앞에서 몸을 빠르게 숙이거나 좌우로 크게 움직이면 최대 5프레임(`~165 ms`) 동안 판정 실패와 `BedExitState` 리셋이 연쇄적으로 일어날 수 있었다.
- 또한 기본 침대 ROI(`methods/bed_roi.json`, 정규화 좌표 `[0.2, 0.2] ~ [0.8, 0.85]`)가 실제 침대·웹캠 화각과 어긋난 책상 테스트 환경에서는 사람이 ROI 밖으로 완전히 빠져나가 `No person overlapping bed ROI` 또는 `Tracked patient lost`가 잦아질 수 있다. 라이브 테스트 시 `compare_methods.py --source 0`으로 노란색 침대 ROI 박스, 세그멘테이션 마스크, `outside_ratio` 오버레이를 함께 확인하는 것을 권장한다.

## 5. 정리

- 현재 결론: 중심 거리 보조 추적(`max_center_jump=0.25`)과 침대 ROI 즉시 재획득을 반영해 단위 테스트 23건 통과 및 `colcon build` 완료, 실웹캠 재실행 검증 대기 중.
- 다시 발생하면: `/home/mu-02/venvs/rokey_venv/bin/python src/part5_bed_exit/compare_methods.py --source 0`으로 검출 박스와 침대 ROI 겹침 상태를 눈으로 확인하고, 필요 시 `bed_roi.json` 좌표나 `max_center_jump` / `min_bed_overlap` 값을 카메라 설치 환경에 맞게 조정한다.
- 다음에 반영할 것: 확정 변경은 `common.py`, `segmentation_method.py`, `test_segmentation_method.py`에 반영 완료. 실침대 카메라 설치 후 실제 화각에 맞춘 `bed_roi.json` 캘리브레이션은 추후 진행 권장.
