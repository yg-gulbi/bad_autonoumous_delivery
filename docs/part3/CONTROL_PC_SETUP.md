# 관제 노트북 처음부터 설치하기 (PART 01 화면 + PART 03 관제)

새 컴퓨터에 아무것도 없을 때 이 순서대로 하면 된다. 명령은 그대로 복사해서 붙인다.

## 0. 어떻게 돌아가나

```
 [PART 05 웹캠 노트북] ── /patient/bed_exit (침상 이탈 true/false) ──┐
                                                                    ▼
                                    ┌──────────── 관제 노트북 (이 컴퓨터) ────────────┐
   브라우저 ◀── http://localhost:8090 ──▶ ui_server (화면, Flask)                      │
                                    │        ▲ 상태 5 Hz      │ 등록·확인·정지 요청      │
                                    │        │                ▼                          │
                                    │     fleet_manager (10 Hz 판단, ~/.bad/bad.db 기록) │
                                    └──────┬───────────────────────────▲──────────────────┘
          /robot2/hospital_nav/command     │ MOVE C 1 · PAUSE ·        │ /robot2/hospital_nav/status
          /robot3/hospital_nav/command     │ RESUME · CANCEL           │ /robot2/amcl_pose (위치) …
                                           ▼                           │
                     [PART 04 노트북 A] 명령 노드 + Nav2 ──▶ TurtleBot4 robot2 (= 화면의 Robot A)
                     [PART 04 노트북 B] 명령 노드 + Nav2 ──▶ TurtleBot4 robot3 (= 화면의 Robot B)
```

**호출 한 건이 처리되는 순서**

1. 간호사가 화면에서 [호출 등록]을 누른다. 화면 서버가 관제에 요청을 보낸다.
2. 관제가 배터리가 많은 대기 로봇을 고른다. 복도를 써도 되는지 판단한 다음 그 로봇의 PART 04 노트북에 `MOVE C 1`을 보낸다.
3. PART 04가 Nav2로 로봇을 움직이고, 1초마다 상태(이동 중·도착·실패)를 보낸다. 관제는 위치(`amcl_pose`)를 받는다.
4. 관제가 위치를 보고 판단해서 필요하면 `PAUSE`·`RESUME`을 보낸다.
   - 복도에서 두 로봇이 마주치면 응급도가 낮은 쪽을 잠깐 세운다.
   - 10초 이상 못 움직이면 막힘을 판단한다.
   - 침상 이탈이 감지되면 로봇을 세운다.
5. 도착하면 화면에 '확인 대기'가 뜨고, 간호사가 [수령 완료]를 누른다. 관제가 로봇을 홈으로 돌려보낸다.
6. 이 과정은 전부 `~/.bad/bad.db`에 남는다. 화면의 운영 기록과 이송 작업 완료 탭이 이 파일을 읽는다.

**발견 서버(Discovery Server)**

- 노드들이 서로를 찾는 '전화번호부'다. 이 팀은 **관제 PC에서** 서버를 띄운다. 로봇과 PART 04 노트북은 모두 이 서버에 붙는다.
- 그래서 관제 PC의 서버가 켜져 있어야 다른 노드들이 서로를 찾는다.
- 관제 노드도 같은 서버에 붙어야 한다. 관제를 띄우는 터미널에 `ROS_DISCOVERY_SERVER=<관제 PC IP>:11811`을 넣는다 (5장).

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
sudo apt install -y python3-flask python3-shapely python3-networkx python3-yaml \
  ros-jazzy-irobot-create-msgs ros-jazzy-nav2-msgs
echo "source /opt/ros/jazzy/setup.bash" >> ~/.bashrc
source ~/.bashrc
```

## 3. 코드 받기와 빌드

팀 가상환경(`rokey_venv`)을 켠 채로 해도 된다. 실행할 때 `ModuleNotFoundError: flask`(또는 `shapely`·`networkx`·`yaml`)가 나면 venv 안에서 한 번 채운다.

```bash
pip install flask shapely networkx pyyaml
```

```bash
git clone -b feat/part1-part3-junseong https://github.com/yg-gulbi/bad_autonoumous_delivery.git ~/bad_ws
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

브라우저에서 http://localhost:8090 을 연다. 로봇 2대가 연결되고, [+ 이송 요청]으로 호출을 넣으면 움직이면 성공이다. 끝나면 `Ctrl+C`.

## 5. 강의실 네트워크 (관제 PC가 발견 서버)

이 팀은 원래 이렇게 켠다. 관제 PC의 IP가 `192.168.108.31`일 때다. IP는 `hostname -I`로 확인한다.

**터미널 1: 발견 서버** (실험하는 동안 계속 켜 둔다)

```bash
/usr/bin/fastdds discovery --server-id 0 --ip-address 192.168.108.31 --port 11811
```

**관제를 띄울 터미널마다** 이걸 넣는다:

```bash
export ROS_DISCOVERY_SERVER=192.168.108.31:11811
export ROS_SUPER_CLIENT=True        # ros2 topic list 같은 명령이 전체 토픽을 보게
echo $ROS_DOMAIN_ID                 # PART 04가 쓰는 값과 같아야 한다 (다르면 export ROS_DOMAIN_ID=같은값)
```

확인 (주행팀이 로봇 노드를 띄운 상태여야 보인다):

```bash
ros2 daemon stop; ros2 daemon start
ros2 topic list | grep -E 'robot2|robot3'     # /robot2/hospital_nav/status 등이 보여야 함
```

⚠ PART 04의 `ros2 run hospital_nav keyboard_control`도 로봇에 명령을 보내는 시험용 관제다. 우리 관제를 켤 때는 그 창에서 `Q`로 끈다. 둘이 같이 켜져 있으면 한 로봇에 명령이 두 군데서 간다.

## 6. 실제 로봇으로 실행

팀원에게 부탁할 것:

- **PART 04:** robot2·robot3 각각 Nav2와 명령 노드를 띄우고, 지도에서 초기 위치를 잡는다.
- **로봇:** 도크에서 떼서 바닥에 둔다. 출발할 때 자동 언도킹은 아직 없다.
- **PART 05:** 침상 감시 웹캠을 켠다. 안 켜면 첫 출발 때 화면 카드에서 [감시 없이 운행]을 누른다.
- **PART 06:** 장애물 분류는 없어도 된다. 없으면 30초 막힘 규칙으로 돈다.

관제 PC (발견 서버는 터미널 1에서 켜 둔 상태. 아래는 터미널 2. 실행 파일과 점검 도구는 단계 4P에서 만든다):

```bash
cd ~/bad_ws && source install/setup.bash
export ROS_DISCOVERY_SERVER=192.168.108.31:11811 ROS_SUPER_CLIENT=True
ros2 run part3_fleet_manager preflight          # 마지막 줄이 "실제 실행 가능"인지 확인
ros2 launch part1_ui_dashboard real.launch.py
```

- 브라우저에서 http://localhost:8090 을 연다.
- 같은 와이파이의 다른 기기에서는 `http://<관제 IP>:8090`으로 연다. 관제 IP는 `hostname -I`로 본다.
- 시험 순서와 비상시 대응은 `docs/part3/REAL_TEST.md`를 따른다.
- 급하면 화면 위쪽 [안전 정지 요청]을 누른다. 로봇 옆에는 항상 사람이 한 명 있는다.
