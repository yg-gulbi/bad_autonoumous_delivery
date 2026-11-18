class Dispatcher:
    """
    [역할] 대기 중인 호출 임무를 확인하고, 현재 가장 적합한 로봇을 골라 일을 배정하는 '배차 두뇌' 클래스입니다.
    - [실전 개선] 간호사가 [수령 완료]를 눌렀을 때 로봇을 즉시 IDLE로 풀지 않고
      'RETURNING'(대기실 복귀 주행 중) 상태로 전환하여, 실제 로봇이 대기실 좌표(0, 0)에
      물리적으로 도착한 뒤에 다음 임무를 배정받도록 안전장치를 추가했습니다.
    """

    def __init__(self, store):
        # 외부(웹 서버, ROS 2 브릿지 노드, 시뮬레이터 등)에서 주입받은 중앙 DB 창고 연결
        self.store = store

    def run_dispatch(self):
        """
        [핵심 배차 알고리즘]
        1) 가장 오래 기다린 대기 임무(PENDING) 1건을 찾고,
        2) 출동 가능한 로봇(IDLE, 배터리 > 20%, 장애물 없음) 중 배터리가 가장 많은 로봇 1대를 뽑아 매칭합니다.
        """
        # 1. DB에서 아직 담당 로봇이 정해지지 않은(PENDING) 임무를 접수 순서(id 오름차순)로 조회
        pending_tasks = self.store.get_tasks(only_pending=True)
        if len(pending_tasks) == 0:
            print("  [관제] 현재 대기 중인 호출 임무가 없습니다.")
            return None

        # 2. DB에 등록된 전체 로봇 중 지금 당장 출동 가능한 로봇만 필터링
        #    * 참고: 'MOVING', 'OBSTACLE', 'ARRIVED', 'RETURNING' 상태인 로봇은 is_available()이 False이므로 자동 제외됨!
        robots = self.store.get_robots()
        available_robots = [r for r in robots if r.is_available()]

        # 출동 가능한 로봇이 0대라면 배정하지 않고 대기열(PENDING)에 그대로 유지
        if len(available_robots) == 0:
            print("  [관제 대기] 모든 로봇이 작업 또는 복귀 중이므로 대기열에서 기다립니다.")
            return None

        # 3. [최적 로봇 선발] 출동 가능한 후보 로봇들 중 배터리 잔량이 가장 높은 로봇 1대 선택
        selected_robot = max(available_robots, key=lambda r: r.battery)

        # 4. [매칭 및 상태 전환] 선입선출(FIFO) 원칙에 따라 0번 인덱스(가장 오래된) 임무 배정
        target_task = pending_tasks[0]
        target_task.robot_id = selected_robot.robot_id  # 임무에 담당 로봇 이름 기록
        target_task.status = "IN_PROGRESS"              # 임무 상태: PENDING -> IN_PROGRESS

        selected_robot.status = "MOVING"                # 로봇 상태: IDLE -> MOVING

        # 5. 변경된 임무와 로봇 상태를 DB에 영구 저장 (ros_manager.py가 1초 내로 감지해 Nav2 출동 명령 발송)
        self.store.update_task(target_task)
        self.store.save_robot(selected_robot)

        print(
            f"  [배차 완료] 임무 #{target_task.task_id}({target_task.destination}) -> "
            f"{selected_robot.robot_id}(배터리 {selected_robot.battery}%) 출동!"
        )
        return target_task

    def complete_and_return(self, task_id):
        """
        [실전 수령 완료 및 물리적 대기실 복귀 지시]
        간호사가 웹 화면에서 [수령 완료] 버튼을 클릭했을 때 실행됩니다.
        1) 해당 배송 임무(task_id)를 'DONE'으로 마감하고,
        2) 로봇 상태를 'RETURNING'으로 바꾸어 ros_manager.py가 실제 대기실 좌표(0, 0)로 Nav2 주행을 시키도록 유도합니다.
        """
        tasks = self.store.get_tasks()
        for t in tasks:
            # 전달받은 task_id와 일치하고 현재 진행 중(IN_PROGRESS)인 임무를 찾음
            if t.task_id == task_id and t.status == "IN_PROGRESS":
                # 1. 임무 상태를 '완료(DONE)'로 마감 처리 후 DB 저장
                t.status = "DONE"
                self.store.update_task(t)

                # 2. 해당 임무를 수행하던 로봇을 찾아 'RETURNING'(대기실 복귀 주행) 상태로 전환
                robot = self.store.get_robot_by_id(t.robot_id)
                if robot:
                    robot.status = "RETURNING"          # ros_manager.py가 이 상태를 보고 대기실(0,0)로 Nav2 명령 발송!
                    robot.location = "대기실(복귀중)"    # 웹 대시보드 표시용 위치 텍스트 갱신
                    # 배터리는 0% 미만으로 내려가지 않도록 안전하게 5% 차감
                    robot.battery = max(0, robot.battery - 5)
                    self.store.save_robot(robot)
                    print(
                        f"  [수령 완료] 임무 #{t.task_id} 종료! {robot.robot_id}에게 대기실 복귀 주행을 지시했습니다. "
                        f"(남은 배터리: {robot.battery}%)"
                    )

                # [중요] 여기서는 바로 run_dispatch()를 부르지 않습니다!
                # 로봇이 실제로 대기실(0, 0)에 도착한 뒤 ros_manager.py의 도착 콜백에서 IDLE 전환 및 다음 배차를 실행합니다.
                return