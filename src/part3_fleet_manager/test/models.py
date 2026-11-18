class Robot:
    """
    [역할] 병원 내 자율주행 로봇 1대의 현재 상태와 기본 행동 규칙을 정의하는 설계도(클래스)입니다.
    - 특징: DB(SQLite)나 ROS 2 통신에 대한 코드는 전혀 없으며, 오직 '로봇 그 자체의 논리적 규칙'만 담고 있습니다.
    """

    def __init__(self, robot_id, status="IDLE", battery=100, obstacle=False, location="대기실"):
        """
        [생성자] 로봇 객체가 메모리에 처음 만들어질 때 5가지 핵심 속성(상태값)을 초기화합니다.
        - robot_id : 로봇 고유 식별 이름 (예: 'robot_a', 'robot_b')
        - status   : 로봇의 현재 동작 상태
                     * 'IDLE'     : 대기실 등에서 쉬고 있는 유휴 상태 (호출 배정 가능)
                     * 'MOVING'   : 목적지를 향해 주행 중인 상태
                     * 'OBSTACLE' : 주행 중 전방 장애물을 만나 일시 정지한 상태
                     * 'ARRIVED'  : 목적지에 도착해 간호사의 수령을 기다리는 상태
        - battery  : 현재 배터리 잔량 (0 ~ 100 사이의 정수, 기본값 100)
        - obstacle : 전방 장애물 감지 여부 (True: 장애물 있음 / False: 전방 깨끗함)
        - location : 사람이 대시보드에서 볼 현재 위치 텍스트 (예: '대기실', '301호(이동중)')
        """
        self.robot_id = robot_id
        self.status = status
        self.battery = battery
        self.obstacle = obstacle
        self.location = location

    def is_available(self):
        """
        [출동 가능 여부 자가 진단]
        배차기(Dispatcher)가 "너 지금 새 임무 맡아서 출동할 수 있어?"라고 물어볼 때 호출됩니다.
        아래 3가지 안전 조건을 '모두(AND)' 만족해야만 True를 반환합니다.
        1) 현재 쉬고 있는 상태('IDLE')여야 함 (이미 이동 중이거나 도착 대기 중이면 불가)
        2) 배터리가 20%보다 많이 남아 있어야 함 (주행 중 방전 방지 안전 마진)
        3) 전방에 장애물이 감지되지 않은 상태(False)여야 함
        """
        if self.status == "IDLE" and self.battery > 20 and self.obstacle == False:
            return True
        return False

    def handle_obstacle(self, detected):
        """
        [장애물 센서 반응 규칙 (상태 머신 전이)]
        ROS 2 센서 토픽(/robot_a/obstacle)에서 장애물 감지 신호(detected: True/False)가 들어올 때 실행됩니다.
        단순히 True/False만 기록하는 것이 아니라, 현재 주행 상태(MOVING <-> OBSTACLE)를 안전하게 전환합니다.
        """
        # 최신 센서 감지 값을 로봇 속성에 반영
        self.obstacle = detected

        # # 상황 A: 목적지로 주행 중('MOVING')이었는데 전방에 환자나 카트(True)가 나타난 경우
        # # -> 안전을 위해 로봇 상태를 'OBSTACLE'(일시정지)로 즉시 전환
        # if detected == True and self.status == "MOVING":
        #     self.status = "OBSTACLE"
        #     print(f"  [경고!] {self.robot_id}: 전방 장애물 감지! 이동을 일시정지합니다.")

        # # 상황 B: 장애물 때문에 멈춰('OBSTACLE') 있었는데, 장애물이 비켜주어(False) 경로가 확보된 경우
        # # -> 다시 주행 상태('MOVING')로 복구
        # elif detected == False and self.status == "OBSTACLE":
        #     self.status = "MOVING"
        #     print(f"  [해제!] {self.robot_id}: 장애물이 제거되어 이동을 재개합니다.")

        # # (참고) 만약 로봇이 'IDLE'(대기실 정지) 상태일 때 앞에 사람이 지나가면(True),
        # # self.obstacle 값만 True로 바뀌고 상태는 'IDLE' 그대로 유지됩니다. (출동만 보류됨)

        # 주행 중('MOVING')이거나 복귀 중('RETURNING')일 때 장애물을 만나면 일시정지
        if detected == True and self.status in ("MOVING", "RETURNING"):
            # 복귀 중이었는지 배달 중이었는지 기억해 두기 위해 이전 상태 저장
            self._prev_status = self.status
            self.status = "OBSTACLE"
            print(f"  [경고!] {self.robot_id}: 전방 장애물 감지! 이동을 일시정지합니다.")

        # 장애물이 사라지면 멈추기 직전 상태(MOVING 또는 RETURNING)로 정확히 복구
        elif detected == False and self.status == "OBSTACLE":
            self.status = getattr(self, "_prev_status", "MOVING")
            print(f"  [해제!] {self.robot_id}: 장애물이 제거되어 이동을 재개합니다.")
        


class Task:
    """
    [역할] 간호사가 웹 화면에서 호출한 배송 임무 1건을 표현하는 데이터 클래스입니다.
    - 특징: 별도의 복잡한 행동 함수 없이, 임무의 진행 이력을 깔끔하게 묶어서 전달하는 데이터 바구니 역할을 합니다.
    """

    def __init__(self, task_id, destination, robot_id=None, status="PENDING"):
        """
        [생성자] 호출 임무 1건의 정보를 초기화합니다.
        - task_id     : 임무 고유 번호 (DB에서 1, 2, 3... 순서로 자동 부여됨)
        - destination : 간호사가 호출한 목적지 이름 (예: '301호', '302호', '약국')
        - robot_id    : 이 임무를 배정받은 담당 로봇 ID (배정되기 전 대기 상태일 때는 None)
        - status      : 임무의 생애주기(Lifecycle) 상태
                        * 'PENDING'     : 호출은 접수되었으나 놀고 있는 로봇이 없어 대기열에서 기다리는 중
                        * 'IN_PROGRESS' : 로봇이 배정되어 목적지로 이동하거나 전달 중인 상태
                        * 'DONE'        : 간호사가 물품 수령을 완료하여 최종 종료된 상태
        """
        self.task_id = task_id
        self.destination = destination
        self.robot_id = robot_id
        self.status = status