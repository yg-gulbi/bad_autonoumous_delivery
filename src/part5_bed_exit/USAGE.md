# Part 5 침대 이탈 감지 모듈 (`part5_bed_exit`)

웹캠의 BGR 이미지(`numpy` 배열)를 선택된 판단 객체의 `judge(frame)` 메서드에 전달한다. 반환값은 Python `bool`이어야 한다 (`True`: 이탈 감지 / unsafe, `False`: 정상 / safe). 신체 인식, ROI 설정, 모델 초기화 및 전처리는 각 메서드 클래스가 담당한다.

최근 5개 프레임 중 `True`가 3개 이상이면 내부 `state.unsafe`와 출력이 `True`가 된다. 초기 4개 프레임은 `False`이며, 정상 판단된 프레임마다 `/patient/bed_exit` (`std_msgs/msg/Bool`)을 발행한다.

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

- **ROS 파라미터로 객체 선정**: `-p method:=your_package.your_method:YourBedExitMethod`처럼 `module:ClassName`을 지정하면 노드가 무인자 생성자(`cls()`)로 객체를 생성하며, `module:detector_obj`처럼 이미 생성된 인스턴스 객체를 지정할 수도 있다.
- **코드 직접 주입**: Python 코드에서 `BedExitNode(detector=YourBedExitMethod())`처럼 객체를 직접 생성해 전달하는 방식도 지원한다.
- 실제 판단 알고리즘(YOLO 추론 등)은 이 공통 노드 골격과 분리해 각 메서드 클래스 내부에 구현한다.

## 빌드와 실행

ROS 2 Jazzy를 `source`한 뒤 `bad_ws` 워크스페이스 루트에서 실행한다.

```bash
source /opt/ros/jazzy/setup.bash
colcon build --packages-select part5_bed_exit --symlink-install
source install/setup.bash
ros2 run part5_bed_exit bed_exit --ros-args \
  -p method:=your_package.your_method:YourBedExitMethod \
  -p output_topic:=/patient/bed_exit \
  -p camera_index:=0 \
  -p capture_fps:=30.0
```

`capture_fps`는 프레임 수집 시도 주기다. 실제 처리율은 카메라와 메서드 실행 시간에 제한되며, 호출은 순차 처리되며 모든 센서 프레임 처리를 보장하지 않는다.

카메라 수신 실패 또는 판단 예외·잘못된 반환값은 로그로 남기고 5프레임 이력을 초기화한다. 오류를 정상 판정(`False`)으로 바꾸어 발행하지 않으며 해당 프레임의 토픽은 발행하지 않는다. 복구 후 다시 5프레임을 수집한다.

## ROS 2 없이 메서드 클래스 비교 테스트 (`compare_methods.py`)

나와 팀원이 만든 여러 메서드 클래스(`judge(frame) -> bool`)를 ROS 2 실행 없이 웹캠이나 영상 파일로 바로 비교할 때는 `compare_methods.py`를 사용한다.

1. [compare_methods.py](compare_methods.py) 상단의 `METHODS` 딕셔너리에 비교할 메서드 클래스나 객체를 등록한다.
2. 필요 시 `SELECTED_METHODS`에 실행할 키 목록(또는 전체 실행 시 `None`)과 `DEFAULT_SOURCE`를 지정한다.
3. 워크스페이스 루트(`bad_ws/`)에서 실행한다.

```bash
python3 src/part5_bed_exit/compare_methods.py --source 0
# 또는 영상 파일로 화면 없이 요약표만 확인
python3 src/part5_bed_exit/compare_methods.py --source sample.mp4 --no-show
```
