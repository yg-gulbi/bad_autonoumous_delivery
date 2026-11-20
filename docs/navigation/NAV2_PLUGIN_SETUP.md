# Nav2 플러그인 설정 STEP

목표: 양쪽 벽이 있는 환경에서 Smac Hybrid-A*로 경로를 만들고 MPPI로 추종한다.
대상: ROS 2 Jazzy, 차동구동(DiffDrive) 로봇. 실제 유턴 가능 여부는 통로 폭, 로봇 외형, 전후진 가능 여부에 따라 달라진다.

이 문서는 설정 절차이며 실제 로봇 설정을 변경하거나 주행을 실행하지 않는다. 차동구동의 제자리 회전을 포함하는 경로 계획이 필요하면 Smac State Lattice도 검토한다. Hybrid-A*는 유한한 회전 반경의 곡선 경로를 계획한다.

## STEP 1. ROS 버전과 플러그인 설치 확인

```bash
printenv ROS_DISTRO
ros2 pkg prefix nav2_smac_planner
ros2 pkg prefix nav2_mppi_controller
```

패키지가 없는 경우 설치한다.

```bash
sudo apt update
sudo apt install ros-jazzy-nav2-smac-planner ros-jazzy-nav2-mppi-controller
```

이 작업 환경에서는 Jazzy와 두 패키지의 설치를 확인했다. Humble 등의 다른 배포판에서는 해당 버전의 플러그인 이름과 파라미터를 확인한다.

## STEP 2. 실제 사용하는 YAML 복사

로봇 launch 파일의 `params_file` 인자 또는 Node의 `parameters` 설정을 찾아 실제 입력 YAML을 확인한다. 아래 경로는 사용 중인 경로로 바꾼다.

```bash
cp /실제/경로/nav2_params.yaml /실제/경로/nav2_params_uturn.yaml
```

기존 로봇의 TF, 센서 토픽, 코스트맵, 속도·가속도 제한은 이 복사본에서 유지한다. `main_map/main_maps.yaml`은 지도 이미지 설정이므로 수정 대상이 아니다.

플러그인 ID는 기존 `GridBased`, `FollowPath`를 유지하면 해당 ID를 지정하는 BT와의 연결을 유지하기 쉽다. 기존 ID가 다르면 BT의 `planner_id`, `controller_id`도 확인한다.

## STEP 3. 경로 계획을 Smac Hybrid-A*로 변경

복사본의 `planner_server` 아래 기존 `GridBased` 블록을 다음 구성으로 교체한다. 같은 YAML 키를 두 번 추가하지 않는다.

```yaml
planner_server:
  ros__parameters:
    planner_plugins: ["GridBased"]
    GridBased:
      plugin: "nav2_smac_planner::SmacPlannerHybrid"
      motion_model_for_search: "REEDS_SHEPP"
      minimum_turning_radius: 0.40
      angle_quantization_bins: 72
      allow_unknown: false
      reverse_penalty: 2.0
      analytic_expansion_max_length: 3.0
```

| 항목 | 의미 |
| --- | --- |
| `REEDS_SHEPP` | 전진·후진 경로를 탐색. 전진만 필요하면 `DUBIN` 검토 |
| `minimum_turning_radius` | 계획할 곡선의 최소 반경(m). 0.40은 예시이며 실제 로봇에 맞춰 결정 |
| `angle_quantization_bins` | 방향을 나누는 구간 수 |
| `allow_unknown` | 미탐색 영역 통과 허용 여부. 지도 운용 정책에 맞춤 |
| `reverse_penalty` | 후진 경로에 대한 탐색 페널티 |

`analytic_expansion_max_length`도 회전 반경에 맞춰 조정한다. 공식 안내는 최소 회전 반경의 4~5배보다 작게 설정하지 않도록 권고한다.

## STEP 4. MPPI 기본 설정 가져오기

설치된 버전의 기본 YAML 위치를 확인한다.

```bash
ros2 pkg prefix --share nav2_bringup
```

출력 경로 아래 `params/nav2_params.yaml`에서 `controller_server.ros__parameters.FollowPath` 블록 전체를 참고한다. 기존 컨트롤러가 DWB 또는 RPP이면 `FollowPath` 전체를 MPPI 블록으로 교체한다. 서버의 progress checker와 goal checker는 유지한다.

현재 환경의 기본 YAML에는 이미 MPPI가 설정되어 있다. 실제 로봇 YAML도 MPPI라면 다시 교체할 필요 없이 STEP 5 항목을 점검한다. `plugin` 문자열만 바꾸고 기존 DWB 파라미터를 그대로 사용하는 방식은 피한다.

## STEP 5. 후진과 외형 충돌 검사를 설정

다음은 전체 설정이 아니라 STEP 4에서 가져온 MPPI 블록에서 수정할 항목이다. 같은 위치의 기존 값을 변경한다.

```yaml
controller_server:
  ros__parameters:
    controller_plugins: ["FollowPath"]
    FollowPath:
      plugin: "nav2_mppi_controller::MPPIController"
      motion_model: "DiffDrive"
      vx_max: 0.30
      vx_min: -0.15
      wz_max: 1.0
      CostCritic:
        consider_footprint: true
      PathAlignCritic:
        use_path_orientations: true
      PathAngleCritic:
        mode: 2
      PreferForwardCritic:
        enabled: false
```

| 설정 | 목적 |
| --- | --- |
| `vx_min < 0` | MPPI의 후진 속도 허용 |
| `consider_footprint: true` | 로봇 외형을 고려한 충돌 비용 계산 |
| `use_path_orientations: true` | 계획 경로의 방향 정보 반영 |
| `PathAngleCritic.mode: 2` | 계획 경로의 방향을 고려하는 모드 |
| `PreferForwardCritic.enabled: false` | 초기 후진 검증에서 전진 선호 페널티 제거. 검증 후 필요에 따라 조정 |

속도는 예시이며 가속도 제한, velocity smoother, 로봇 드라이버에도 실제 구동 가능한 전후진 범위를 반영한다. 회전 반경이 있는 Ackermann 로봇이면 `DiffDrive` 대신 해당 모델과 제약 조건을 구성해야 한다.

## STEP 6. 코스트맵의 로봇 외형 확인

local/global costmap 모두 실제 로봇을 나타내는 `footprint` 또는 원형 로봇의 `robot_radius`를 확인한다. 직사각형 로봇은 회전 시 모서리까지 고려할 수 있도록 외형 좌표를 설정한다.

```yaml
# 예: base_link 원점이 차체 중심이고 가로 0.4m, 세로 0.6m인 경우만 해당
# 실제 로봇의 원점·크기로 바꿔 local/global costmap에 각각 적용한다.
footprint: "[[0.30, 0.20], [0.30, -0.20], [-0.30, -0.20], [-0.30, 0.20]]"
```

`inflation_radius`는 외형과 통로 여유를 기준으로 조정한다. 유턴 실패를 해결하려고 실제 외형을 작게 입력하지 않는다. 로컬 코스트맵 크기도 회전 궤적과 MPPI 예측 범위를 포함해야 한다.

## STEP 7. 수정한 YAML로 재시작

기존 로봇 전용 launch를 사용한다면 그 launch가 복사본을 읽도록 설정하고 기존 Nav2를 종료한 후 재시작한다. 별도의 Nav2를 중복 실행하지 않는다.

표준 launch를 사용하는 경우의 예:

```bash
ros2 launch nav2_bringup navigation_launch.py \
  params_file:=/실제/경로/nav2_params_uturn.yaml \
  use_sim_time:=false
```

이 launch는 지도 서버·위치 추정·센서·TF·로봇 드라이버가 이미 준비된 상황을 가정한다. 시뮬레이션에서는 `use_sim_time:=true`를 사용한다.

## STEP 8. 적용 확인

네임스페이스가 있으면 아래 노드 이름 앞에 추가한다.

```bash
ros2 param get /planner_server GridBased.plugin
ros2 param get /planner_server GridBased.motion_model_for_search
ros2 param get /controller_server FollowPath.plugin
ros2 param get /controller_server FollowPath.vx_min
ros2 lifecycle get /planner_server
ros2 lifecycle get /controller_server
```

기대 결과는 `SmacPlannerHybrid`, `REEDS_SHEPP`, `MPPIController`, 음수 `vx_min`이며 두 서버는 `active`여야 한다. 파라미터 확인만으로 주행 동작 검증을 대신할 수는 없다.

## STEP 9. 유턴 동작 확인

| 순서 | 확인할 내용 |
| --- | --- |
| 1 | 시뮬레이션 또는 여유 공간에서 일반 전진 경로가 정상인지 확인 |
| 2 | 로봇 뒤쪽 목표로 후진 경로와 실제 후진 명령이 동작하는지 확인 |
| 3 | 양쪽 벽이 있는 조건에서 반대 방향의 목표 자세를 지정 |
| 4 | RViz에서 계획 경로, local/global costmap, footprint를 함께 확인 |
| 5 | 경로 생성 실패인지, 경로 추종 실패인지, 드라이버의 속도 제한인지 구분 |

복도 폭이 물리적인 회전 궤적보다 좁으면 플러그인 교체만으로 해결되지 않는다. 제자리 회전이나 여러 번의 전후진이 필요한지 구분해 계획 모델을 선택한다.

## 참고

- [Smac Hybrid-A* 공식 설정](https://docs.nav2.org/jazzy/configuration_and_development/configuration_guide/planners_plugins/smac/smac_hybrid/configuring_smac_hybrid/)
- [MPPI 공식 설정](https://ros-navigation.github.io/mkdocs.nav2.org/jazzy/configuration_and_development/configuration_guide/controller_plugins/mppi_controller/configuring_mppic/)
- 설치된 MPPI 예시: `/opt/ros/jazzy/share/nav2_bringup/params/nav2_params.yaml`

작성 시 확인한 범위: 로컬 ROS 배포판, 플러그인 설치, 기본 YAML 및 플러그인 XML. 실제 로봇 YAML 변경과 주행 테스트는 수행하지 않았다.
