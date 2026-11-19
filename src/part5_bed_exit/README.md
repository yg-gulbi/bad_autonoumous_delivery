# PART 05 — Bed Exit Detection

별도 웹캠 영상으로 침대 이탈 여부를 판단하고 ROS 2 토픽
`/patient/bed_exit`에 `std_msgs/msg/Bool`을 발행한다. `True`는 이탈 감지,
`False`는 정상 상태다.

## 실행 구조

운영 노드는 `BedExitNode`가 웹캠·MP4 입력, 판단 메서드, 5프레임 다수결과 ROS 발행을
연결한다. `video_path`가 비어 있으면 `camera_index`의 웹캠을 사용하고, 값이 있으면
해당 MP4를 사용한다. `method` ROS 파라미터에는 내장 방식 이름 또는
`package.module:ClassOrObject` 형식의 외부 모듈을 지정할 수 있다. 내장 방식은
`method_config` JSON에서 ROI, 모델 경로와 실행 장치를 읽는다.

### 판단 방법 선택

운영에서 선택하는 방식은 다음 두 범주다.

1. 내장 모델: `method=segmentation`처럼 이름을 명시하고 `method_config`에 JSON 경로를
   전달한다. `method`를 비우고 JSON 안의 `method`를 읽는 방식은 기존 설정 호환용이다.
2. 외부 플러그인: `method=package.module:ClassName`을 사용한다. 이미 생성된 객체를
   모듈 변수로 공개한 `package.module:object_name`도 지원하지만 클래스 방식이 기본이다.

`BedExitNode(detector=FakeDetector())`는 ROS 운영 설정이 아니라 모델 없이 노드 경계를
검증하기 위한 테스트 전용 주입 방식이다.

카메라 읽기, 판단, ROS 발행과 선택적 프리뷰가 실패하면 예외를 타이머 콜백 밖으로
전파하지 않는다. 실패 프레임은 발행하지 않으며, 판단 연속성이 깨진 경우 투표 이력을
초기화하고 다음 유효 프레임부터 5프레임을 다시 누적한다.

자세한 실행 파라미터와 예시는 [USAGE.md](USAGE.md)를 참고한다.

## 테스트 버전 규칙

테스트 기준선은 `test/<짧은-커밋>_vX_X/` 형식으로 관리한다. `X_X`는 기능 계약이
변경된 major/minor 기준선이며, patch 변경은 같은 `vX_X` 아래에 회귀 테스트를
추가한다. 예를 들어 v1.3.2의 회귀 테스트는 `80805b4_v1_3/`에 추가한다.

각 기준선은 필요에 따라 다음 범위를 가진다.

- `unit/`: 해당 기준선의 내부 클래스와 함수 단위 검증
- `integration/`: 카메라·MP4 입력부터 로더, 투표와 ROS 발행 경계까지 검증
- `integration_benchmark/`: 공통 영상·라벨·지표·산출물 계약 검증

v1.3.2의 `test_v3_mp4_replay_resilience.py`는 연구용 MP4 ROS 노드에
`capture.read()`·publisher·프리뷰 예외를 주입하고, 예외가 콜백 밖으로 전파되지 않는지
검증한다. 모델 정확도 테스트가 아니라 노드 생존성과 실패 프레임 미발행 정책을 확인하는
통합 회귀 테스트다.

## 통합 코드 재사용 계약

내부 리팩터링과 patch 변경은 기존 통합 테스트와 통합 벤치마크 코드를 수정하지 않고
다시 실행할 수 있어야 한다. 이를 위해 다음 공개 계약을 유지한다.

- 운영 로더: `load_bed_exit_method(method, method_config)`
- 판단 객체: `judge(frame) -> bool`, 선택적 `render(frame)`·`close()`
- 투표 상태: `BedExitState.update(bool) -> bool`
- ROS 출력: `/patient/bed_exit`, `std_msgs/msg/Bool`
- 벤치마크 입력: 기존 MP4·설정 JSON·타임라인 JSON
- 벤치마크 출력: `run.json`, `frames.csv`, `comparison.csv`, `comparison.mp4`, `rosbag/`

내부 모듈 이동이나 예외 처리 보강만으로 이 계약을 바꾸지 않는다. 계약을 깨는 변경은
새 `vX_X` 기준선을 만들고, 기존 통합 코드가 재사용되지 않는 이유와 마이그레이션 방법을
문서화해야 한다.

## 검증 명령

```bash
source /opt/ros/jazzy/setup.bash
PYTHONPATH=src/part5_bed_exit python3 -m pytest \
  src/part5_bed_exit/test/80805b4_v1_3/unit \
  src/part5_bed_exit/test/80805b4_v1_3/integration \
  src/part5_bed_exit/test/80805b4_v1_3/integration_benchmark -q
```

실제 모델·1,024프레임 MP4·기존 벤치마크 산출물 검증은
[v1.3 통합 벤치마크 실행 문서](test/80805b4_v1_3/integration_benchmark/README.md)의
환경 변수와 명령을 사용한다.

실험 결과는 `results/<기준선커밋>_vX_X/<실험대상커밋>_vX_X_X/`에 저장한다.
v1.3.2 결과 경로는 `results/80805b4_v1_3/b08be95_v1_3_2/`다.
