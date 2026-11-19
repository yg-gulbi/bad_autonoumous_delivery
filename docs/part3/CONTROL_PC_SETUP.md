# 관제 PC 처음부터 설치하기 (PART 01 화면 + PART 03 관제)

새 컴퓨터에 아무것도 없을 때 이 순서대로 한다. 명령은 그대로 복사해서 붙인다.

- 시스템이 어떻게 돌아가는지는 레포 루트 [`README.md`](../../README.md), 관제 규칙은 [`BRIEF.md`](BRIEF.md)에 있다.
- 설치가 끝나면 강의실 네트워크 연결과 실제 로봇 시험은 [`REAL_TEST.md`](REAL_TEST.md)를 따른다.

## 1. 준비물 확인 (새 컴퓨터)

```bash
lsb_release -rs     # 24.04 이어야 한다 (ROS 2 Jazzy는 Ubuntu 24.04 전용)
```

- 24.04가 아니면 여기서 멈추고 OS부터 맞춘다.
- 로봇과 같은 와이파이에 연결한다.

## 2. ROS 2 Jazzy와 필요한 패키지 설치 (처음 한 번, 15~30분)

공식 설치 문서와 같은 순서다: https://docs.ros.org/en/jazzy/Installation/Ubuntu-Install-Debs.html

```bash
sudo apt update && sudo apt install -y software-properties-common curl git
sudo add-apt-repository -y universe
export ROS_APT_SOURCE_VERSION=$(curl -s https://api.github.com/repos/ros-infrastructure/ros-apt-source/releases/latest | grep -F "tag_name" | awk -F\" '{print $4}')
curl -L -o /tmp/ros2-apt-source.deb "https://github.com/ros-infrastructure/ros-apt-source/releases/download/${ROS_APT_SOURCE_VERSION}/ros2-apt-source_${ROS_APT_SOURCE_VERSION}.$(. /etc/os-release && echo ${UBUNTU_CODENAME:-${VERSION_CODENAME}})_all.deb"
sudo dpkg -i /tmp/ros2-apt-source.deb
sudo apt update && sudo apt upgrade -y
sudo apt install -y ros-jazzy-desktop ros-dev-tools
```

관제·화면이 쓰는 것:

```bash
sudo apt install -y python3-flask python3-shapely python3-networkx python3-yaml python3-psutil \
  ros-jazzy-irobot-create-msgs ros-jazzy-nav2-msgs
echo "source /opt/ros/jazzy/setup.bash" >> ~/.bashrc
source ~/.bashrc
```

## 3. 코드 받기와 빌드

팀 가상환경(`rokey_venv`)을 켠 채로 해도 된다. 실행할 때 `ModuleNotFoundError: flask`(또는 `shapely`·`networkx`·`yaml`·`psutil`)가 나면 venv 안에서 한 번 채운다.

```bash
pip install flask shapely networkx pyyaml psutil
```

```bash
git clone https://github.com/yg-gulbi/bad_autonoumous_delivery.git ~/bad_ws   # main에 합쳐져 있다
cd ~/bad_ws
colcon build --packages-select part3_fleet_manager part1_ui_dashboard
source install/setup.bash
```

- 저장소가 비공개면 GitHub 아이디를 묻는다. 비밀번호 자리에는 GitHub 계정 비밀번호가 아니라 토큰을 넣는다 (GitHub → Settings → Developer settings → Personal access tokens에서 만든다).
- 코드가 바뀌면 이렇게 받는다.

```bash
cd ~/bad_ws && git pull
colcon build --packages-select part3_fleet_manager part1_ui_dashboard
source install/setup.bash
```

- 실행은 항상 `~/bad_ws`에서 한다. 화면 설정의 맵 경로 `main_map/main_maps.yaml`이 이 폴더 기준이다.

## 4. 먼저 로봇 없이 확인 (가짜 로봇)

가짜 로봇도 `/robot2`·`/robot3` 이름을 쓴다. 강의실 네트워크에 새어 나가면 진짜 로봇 노트북과 섞인다. 그래서 **이 터미널에서만 이 컴퓨터 안으로 막는다**.

```bash
cd ~/bad_ws && source install/setup.bash
unset ROS_DISCOVERY_SERVER ROS_SUPER_CLIENT
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST ROS_DOMAIN_ID=81
ros2 launch part1_ui_dashboard demo_mock.launch.py
```

브라우저에서 http://localhost:8090 을 연다. 로봇 2대가 연결되고, [+ 이송 요청]으로 호출을 넣었을 때 지도에서 움직이면 성공이다. 끝나면 `Ctrl+C`.

## 5. 다음: 실제 로봇

[`REAL_TEST.md`](REAL_TEST.md)를 따른다. 요약하면 다음과 같다.

1. 터미널 1: 관제 PC에서 발견 서버 `/usr/bin/fastdds discovery --server-id 0 --ip-address <관제 PC IP> --port 11811` (계속 켜 둔다).
2. 터미널 2: `export ROS_DISCOVERY_SERVER=<관제 PC IP>:11811 ROS_SUPER_CLIENT=True` → `ros2 run part3_fleet_manager preflight` → `ros2 launch part1_ui_dashboard real.launch.py`.
3. 4장의 격리 설정(`LOCALHOST`, 도메인 81)이 남아 있는 터미널은 쓰지 않는다. 새 터미널을 연다.
4. PART 04의 `keyboard_control`은 끈다.
