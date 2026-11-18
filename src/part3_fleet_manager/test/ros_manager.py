import json
import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient  # ROS 2에서 장시간 걸리는 작업(주행 등)을 요청·추적할 때 쓰는 클라이언트

from std_msgs.msg import String         # 센서(장애물·배터리) 데이터를 JSON 문자열로 받기 위한 메시지 타입
from geometry_msgs.msg import PoseStamped  # 로봇의 3차원 위치(x, y, z)와 회전 방향(쿼터니언)을 담는 표준 메시지
from nav2_msgs.action import NavigateToPose  # Nav2 자율주행 스택에 "이 좌표로 이동해!"라고 지시하는 표준 액션 타입

from bad_control.store import ControlStore  # SQLite DB(hospital_internal.db)를 읽고 쓰는 우리 패키지 모듈


class HospitalRosManager(Node):
    """
    [역할] 웹/배차 시스템(SQLite DB)과 실제 로봇(ROS 2 Nav2) 사이를 연결하는 중앙 통신 브릿지 노드.
    - DB를 감시하다 새 임무가 배정되면 로봇의 Nav2에 주행 명령(Action)을 보냅니다.
    - 로봇이 보내는 센서 토픽과 주행 완료 결과를 받아 다시 DB에 기록합니다.
    """

    def __init__(self):
        # ROS 2 네트워크 상에서 이 노드의 고유 이름을 "hospital_ros_manager"로 등록
        super().__init__("hospital_ros_manager")

        # 1. 중앙 SQLite DB 연결 (웹 서버, 배차기, ROS 노드가 이 파일 하나를 함께 공유함)
        self.store = ControlStore("/home/haji/Documents/test/hospital_internal.db")

        # 2. 병동 호실 이름 -> 실제 SLAM 지도(map 프레임) 상의 (x, y) 좌표 및 도착 시 바라볼 방향(z, w) 매핑 사전
        #    * ROS 2에서는 각도(도, radian) 대신 수학적 회전 표현인 '쿼터니언(x, y, z, w)'을 사용합니다.
        #    * 평면(2D) 주행이므로 x, y 회전은 0이고 z, w 값으로 동/서/남/북 방향을 결정합니다.
        self.location_coords = {
            "대기실": {"x": 0.0, "y": 0.0,  "z": 0.0,    "w": 1.0},    # 정면(0도) 방향
            "약국":   {"x": 2.5, "y": 1.0,  "z": 0.0,    "w": 1.0},    # 정면(0도) 방향
            "301호":  {"x": 5.0, "y": 3.2,  "z": 0.707,  "w": 0.707},  # 왼쪽(90도) 방향
            "302호":  {"x": 5.0, "y": -1.5, "z": -0.707, "w": 0.707},  # 오른쪽(-90도) 방향
        }

        # 3. [구독자(Subscriber) 설정] 로봇들이 쏘는 장애물/배터리 상태 토픽을 듣는 귀
        #    - 토픽 이름: /robot_a/obstacle, /robot_b/obstacle
        #    - 큐 크기(10): 메시지가 한꺼번에 몰릴 때 최대 10개까지 버퍼에 쌓아두고 순서대로 처리
        self.create_subscription(String, "/robot_a/obstacle", self.cb_robot_a_sensor, 10)
        self.create_subscription(String, "/robot_b/obstacle", self.cb_robot_b_sensor, 10)

        # 4. [액션 클라이언트(ActionClient) 설정] 각 로봇의 Nav2 자율주행 서버에 이동 명령을 보내는 입
        #    - 단순 토픽(publish)은 '보내고 끝'이지만, 액션(Action)은 '이동 중 피드백'과 '최종 도착 결과'까지 돌려받습니다.
        #    - 실제 TurtleBot 4 실행 시 설정한 네임스페이스(/robot_a, /robot_b)와 정확히 일치해야 합니다.
        self.nav_clients = {
            "robot_a": ActionClient(self, NavigateToPose, "/robot_a/navigate_to_pose"),
            "robot_b": ActionClient(self, NavigateToPose, "/robot_b/navigate_to_pose"),
        }

        # 5. 이미 Nav2에 출발 명령을 내린 임무 번호(task_id)를 기억하는 리스트
        #    * 이 리스트가 없으면 1초마다 타이머가 돌 때마다 똑같은 출발 명령을 중복으로 계속 쏘게 됩니다!
        self.sent_task_ids = []

        # 6. [타이머 설정] 1.0초마다 check_and_send_mission 함수를 자동 실행하여 DB에 새 배차가 떴는지 감시
        self.create_timer(1.0, self.check_and_send_mission)

        self.get_logger().info("=== 병원 다중 로봇 Nav2 액션 관제 브릿지가 시작되었습니다 ===")

    # -------------------------------------------------------------------------
    # [구역 1] 로봇 센서(장애물, 배터리) 데이터 수신 및 DB 반영
    # -------------------------------------------------------------------------
    def cb_robot_a_sensor(self, msg):
        """/robot_a/obstacle 토픽이 들어오면 공통 처리 함수로 로봇 이름과 함께 넘김"""
        self.process_sensor("robot_a", msg.data)

    def cb_robot_b_sensor(self, msg):
        """/robot_b/obstacle 토픽이 들어오면 공통 처리 함수로 로봇 이름과 함께 넘김"""
        self.process_sensor("robot_b", msg.data)

    def process_sensor(self, robot_id, json_str):
        """로봇이 보낸 JSON 문자열을 해석해 DB의 로봇 상태를 최신화하는 함수"""
        try:
            # 예: '{"obstacle": true, "battery": 75}' 문자열을 파이썬 딕셔너리로 변환
            data = json.loads(json_str)
            is_obstacle = data.get("obstacle", False)  # 키가 없으면 기본값 False
            battery = data.get("battery", None)        # 키가 없으면 기본값 None

            # DB에서 해당 로봇의 현재 정보 객체(Robot)를 꺼내옴
            robot = self.store.get_robot_by_id(robot_id)
            if robot is None:
                return

            # 배터리 정보가 포함되어 왔다면 최신 잔량으로 덮어씀
            if battery is not None:
                robot.battery = int(battery)

            # models.py에 정의된 상태 전이 규칙 실행:
            # 이동 중(MOVING)에 장애물(True)이 감지되면 상태를 OBSTACLE(일시정지)로 자동 변경
            robot.handle_obstacle(is_obstacle)

            # 변경된 로봇 상태를 SQLite DB에 영구 저장 (웹 대시보드에서도 즉시 반영됨)
            self.store.save_robot(robot)

        except Exception as e:
            # 잘못된 형식의 JSON이 들어와도 노드 전체가 죽지 않도록 예외 처리
            self.get_logger().error(f"[{robot_id} 센서 파싱 에러] {e}")

    # -------------------------------------------------------------------------
    # [구역 2] DB 감시 및 Nav2 주행 명령(Goal) 발송
    # -------------------------------------------------------------------------
    def check_and_send_mission(self):
        """1초마다 DB의 tasks 테이블을 확인하여 새로 출동해야 할 임무를 찾아내는 함수"""
        tasks = self.store.get_tasks()

        for t in tasks:
            # 조건 1: 배차기(Dispatcher)가 로봇을 배정해서 상태가 'IN_PROGRESS'가 되었고,
            # 조건 2: 아직 이 노드가 로봇에게 Nav2 출발 명령을 보낸 적이 없는 임무라면!
            if t.status == "IN_PROGRESS" and t.task_id not in self.sent_task_ids:
                if t.robot_id not in self.nav_clients:
                    continue

                # 목적지 이름(예: '301호')에 해당하는 지도 좌표를 사전에서 조회
                coords = self.location_coords.get(
                    t.destination, {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
                )

                # 실제 로봇의 Nav2 서버로 비동기 이동 명령 발송
                self.send_nav2_goal(t.robot_id, t.task_id, t.destination, coords)

                # 중복 발송 방지를 위해 완료 목록에 임무 ID 등록
                self.sent_task_ids.append(t.task_id)

    def send_nav2_goal(self, robot_id, task_id, destination, coords):
        """Nav2 표준 규격(NavigateToPose.Goal)에 맞춰 좌표 메시지를 조립하고 전송하는 함수"""
        client = self.nav_clients[robot_id]

        # 1. 로봇의 Nav2 액션 서버가 켜져서 명령을 받을 준비가 되었는지 최대 2초간 확인
        if not client.wait_for_server(timeout_sec=2.0):
            self.get_logger().error(f"[{robot_id}] Nav2 액션 서버(/navigate_to_pose)가 응답하지 않습니다!")
            return

        # 2. Nav2가 요구하는 표준 목표 메시지(Goal) 객체 생성 및 데이터 채우기
        goal_msg = NavigateToPose.Goal()
        goal_msg.pose = PoseStamped()

        # [중요] 기준 좌표계 설정: 로봇 기준(base_link)이 아니라 전체 병원 지도 기준("map")이어야 함
        goal_msg.pose.header.frame_id = "map"
        goal_msg.pose.header.stamp = self.get_clock().now().to_msg()  # 현재 ROS 시간 기록

        # 목표 위치 (x, y) 설정 (2D 평면 로봇이므로 z 높이는 항상 0.0)
        goal_msg.pose.pose.position.x = float(coords["x"])
        goal_msg.pose.pose.position.y = float(coords["y"])
        goal_msg.pose.pose.position.z = 0.0

        # 목표 도착 후 로봇이 바라볼 방향(쿼터니언) 설정
        goal_msg.pose.pose.orientation.z = float(coords["z"])
        goal_msg.pose.pose.orientation.w = float(coords["w"])

        # 3. DB에 로봇의 현재 위치 표시를 '301호(이동중)'으로 업데이트
        robot = self.store.get_robot_by_id(robot_id)
        if robot:
            robot.location = f"{destination}(이동중)"
            self.store.save_robot(robot)

        self.get_logger().info(
            f"[Nav2 출동 명령] {robot_id} -> {destination} (x:{coords['x']}, y:{coords['y']})"
        )

        # 4. [비동기 전송] 명령을 쏜 뒤 로봇이 도착할 때까지 여기서 코드가 멈추지(블로킹되지) 않도록 비동기 호출!
        #    - 만약 멈추면 다른 로봇의 장애물 센서 토픽도 못 받게 됩니다.
        #    - 대신 "로봇이 명령을 수락하면 goal_response_callback 함수를 불러줘"라고 예약(콜백)만 걸어둡니다.
        send_goal_future = client.send_goal_async(goal_msg)
        send_goal_future.add_done_callback(
            lambda future: self.goal_response_callback(future, robot_id, destination)
        )

    # -------------------------------------------------------------------------
    # [구역 3] 비동기 콜백: Nav2 명령 수락 확인 및 최종 도착 처리
    # -------------------------------------------------------------------------
    def goal_response_callback(self, future, robot_id, destination):
        """1단계 콜백: 로봇의 Nav2가 목표 좌표를 받고 '출발 가능(accepted)'인지 답장했을 때 실행"""
        goal_handle = future.result()

        # 지도 밖 좌표이거나 경로를 찾을 수 없어 Nav2가 명령을 거절한 경우
        if not goal_handle.accepted:
            self.get_logger().warn(f"[{robot_id}] Nav2가 이동 명령을 거부했습니다! (갈 수 없는 좌표 등)")
            return

        self.get_logger().info(f"[{robot_id}] Nav2 경로 생성 완료. {destination}(으)로 주행을 시작합니다.")

        # 2단계 콜백 예약: 이제 주행이 완전히 끝나고 최종 도착했을 때 get_result_callback 함수를 실행하라고 등록
        get_result_future = goal_handle.get_result_async()
        get_result_future.add_done_callback(
            lambda future: self.get_result_callback(future, robot_id, destination)
        )

    def get_result_callback(self, future, robot_id, destination):
        """2단계 콜백: 로봇이 목적지에 최종 도착(주행 종료)했을 때 자동 실행되는 함수"""
        # DB에서 해당 로봇을 꺼내 상태를 'ARRIVED'(목적지 도착 · 간호사 수령 대기)로 변경
        robot = self.store.get_robot_by_id(robot_id)
        if robot and robot.status == "MOVING":
            robot.status = "ARRIVED"
            robot.location = f"{destination}(도착)"
            self.store.save_robot(robot)
            self.get_logger().info(f"[Nav2 도착 완료] {robot_id}가 {destination}에 도착했습니다!")


def main():
    # ROS 2 파이썬 클라이언트 라이브러리 초기화
    rclpy.init()

    # 위에서 정의한 관제 브릿지 노드 인스턴스 생성
    node = HospitalRosManager()

    try:
        # 노드가 종료되지 않고 계속 살아있으면서 타이머(1초 감시)와 콜백(센서·Nav2 응답)을 처리하도록 무한 대기
        rclpy.spin(node)
    except KeyboardInterrupt:
        # 터미널에서 Ctrl+C를 눌렀을 때 깔끔하게 종료 메시지 출력
        print("\n사용자에 의해 ROS 2 관제 노드가 종료됩니다.")
    finally:
        # 노드 리소스 해제 및 ROS 2 시스템 종료
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()