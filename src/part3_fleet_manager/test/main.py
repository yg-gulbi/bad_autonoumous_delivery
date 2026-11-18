import time
from bad_control.store import ControlStore
from bad_control.dispatcher import Dispatcher


def print_dashboard(store):
    """
    [시각화 도우미 함수]
    현재 SQLite DB(hospital_internal.db)에 저장된 모든 로봇과 호출 임무의 최신 상태를
    사람이 한눈에 파악할 수 있도록 터미널 표 형태로 출력합니다.
    - 실제 웹 대시보드(index.html)가 브라우저 화면에 표를 그려주는 것과 똑같은 원리입니다.
    """
    print("\n" + "=" * 60)
    print(" [현재 관제 대시보드 상태]")

    # 1. DB에서 모든 로봇(robot_a, robot_b) 객체를 꺼내어 현재 상태·배터리·위치를 출력
    for r in store.get_robots():
        # :8s 는 문자열을 8칸 폭으로 왼쪽 정렬해 표의 줄을 깔끔하게 맞추는 포맷팅 문법입니다.
        print(
            f"  - {r.robot_id} | 상태: {r.status:8s} | 배터리: {r.battery:3d}% | 장애물: {str(r.obstacle):5s} | 위치: {r.location}"
        )

    print("-" * 60)

    # 2. DB에 기록된 모든 호출 임무(Task) 객체를 꺼내어 목적지·담당로봇·진행상태를 출력
    tasks = store.get_tasks()
    if not tasks:
        print("  - 등록된 호출 임무가 없습니다.")
    else:
        for t in tasks:
            # 담당 로봇이 아직 배정 전(None)일 경우 '미배정'으로 보기 좋게 표시
            assigned_robot = t.robot_id if t.robot_id else "미배정"
            print(
                f"  - 임무 #{t.task_id} | 목적지: {t.destination:5s} | 담당: {assigned_robot:7s} | 상태: {t.status}"
            )

    print("=" * 60 + "\n")


def run_simulation():
    """
    [단독 검증 시뮬레이터 (Mock-up Test)]
    - 근본 목적: 실제 TurtleBot 4 하드웨어나 Nav2, Flask 웹 서버를 모두 띄우기 전에,
      우리 시스템의 핵심 3요소인 [DB(store) + 배차 두뇌(dispatcher) + 로봇 규칙(models)]이
      4가지 현장 시나리오에서 논리적 모순 없이 맞물려 돌아가는지 5초 만에 검증합니다.
    """
    print(">>> 병원 다중 로봇 중앙 관제 내부 시뮬레이션을 시작합니다.\n")

    # 1. 중앙 SQLite DB 창고 연결
    store = ControlStore("/home/haji/Documents/test/hospital_internal.db")

    # 2. [중요: 실전용 store.py 반영]
    #    실전용 store.py는 기존 데이터를 지우지 않도록 고쳤기 때문에,
    #    시뮬레이터를 돌릴 때는 이전 테스트 찌꺼기가 남지 않도록 명시적으로 reset_db()를 호출해 초기화합니다.
    #    (초기값: robot_a 배터리 85%, robot_b 배터리 95%, 임무 0건)
    store.reset_db()

    # 3. DB 창고를 제어할 배차 두뇌(Dispatcher) 생성
    dispatcher = Dispatcher(store)

    # [초기 상태 확인] 로봇 2대 모두 IDLE(대기중) 상태인지 출력
    print_dashboard(store)
    time.sleep(1)

    # -------------------------------------------------------------------------
    # [상황 1] 보유 로봇(2대)보다 많은 호출(3건)이 동시에 들어왔을 때의 배차 및 대기열(Queue) 검증
    # - 검증 포인트:
    #   ① 배터리가 더 많은 로봇(robot_b 95% > robot_a 85%)을 먼저 출동시키는가?
    #   ② 놀고 있는 로봇이 0대가 되었을 때, 3번째 호출을 억지로 배정하지 않고 'PENDING'으로 줄 세우는가?
    # -------------------------------------------------------------------------
    print("--- [상황 1] 병동에서 3건의 호출이 연속으로 발생 ---")
    store.add_task("301호")  # 임무 #1 등록 (status='PENDING')
    store.add_task("약국")   # 임무 #2 등록 (status='PENDING')
    store.add_task("302호")  # 임무 #3 등록 (status='PENDING')

    # 배차기 3회 연속 가동 (웹에서는 호출 버튼 1번 누를 때마다 run_dispatch()가 1번씩 실행됨)
    dispatcher.run_dispatch()  # 1회차 -> 배터리가 가장 높은 robot_b(95%)가 #1(301호)을 맡아 MOVING
    dispatcher.run_dispatch()  # 2회차 -> 남은 robot_a(85%)가 #2(약국)를 맡아 MOVING
    dispatcher.run_dispatch()  # 3회차 -> 출동 가능 로봇 0대 -> #3(302호)은 배정 보류(PENDING 유지)
    print_dashboard(store)
    time.sleep(1)

    # -------------------------------------------------------------------------
    # [상황 2] 주행 중인 로봇 전방에 환자·이동침대(장애물)가 나타났을 때의 안전 정지 검증
    # - 검증 포인트:
    #   실제로는 ros_manager.py가 '/robot_b/obstacle' 토픽을 받아 실행하는 동작을,
    #   여기서는 직접 DB에서 robot_b를 꺼내 handle_obstacle(True)를 호출해 상태 전이를 확인합니다.
    # -------------------------------------------------------------------------
    print("--- [상황 2] 301호로 이동 중인 robot_b 전방에 환자(장애물) 감지 ---")
    robot_b = store.get_robot_by_id("robot_b")
    robot_b.handle_obstacle(True)  # models.py 규칙에 의해 MOVING -> OBSTACLE(일시정지)로 상태 변경
    store.save_robot(robot_b)      # 변경된 상태를 SQLite DB에 영구 저장
    print_dashboard(store)
    time.sleep(1)

    # -------------------------------------------------------------------------
    # [상황 3] 장애물이 비켜주었을 때의 주행 자동 재개 검증
    # - 검증 포인트:
    #   센서 값이 False로 돌아오면 OBSTACLE -> MOVING으로 안전하게 복구되는지 확인합니다.
    # -------------------------------------------------------------------------
    print("--- [상황 3] 장애물이 비켜주어 robot_b 주행 재개 ---")
    robot_b.handle_obstacle(False)  # models.py 규칙에 의해 OBSTACLE -> MOVING으로 자동 복구
    store.save_robot(robot_b)       # 복구된 상태를 DB에 영구 저장
    print_dashboard(store)
    time.sleep(1)

    # -------------------------------------------------------------------------
    # [상황 4] 임무 1건 완료 시 배터리 차감 및 대기열에 밀려있던 다음 임무(#3) 자동 배차 검증
    # - 검증 포인트:
    #   간호사가 301호 물품 수령 완료를 눌렀을 때(complete_and_return(1)),
    #   ① 임무 #1이 'DONE'이 되고 robot_b가 대기실로 복귀(배터리 95% -> 90%)하는가?
    #   ② 대기열에 밀려 있던 임무 #3('302호')이 사람의 추가 개입 없이 방금 복귀한 robot_b에게 자동 배정되는가?
    # -------------------------------------------------------------------------
    print("--- [상황 4] 301호 물품 수령 완료 -> 밀려있던 302호 자동 배차 ---")
    dispatcher.complete_and_return(task_id=1)
    print_dashboard(store)


# 터미널에서 'python3 main.py'로 직접 실행하거나,
# 'ros2 run bad_control sim_test' 명령어로 실행될 때 가장 먼저 호출되는 진입점(Entry Point)
if __name__ == "__main__":
    run_simulation()