import sqlite3
from bad_control.models import Robot, Task


class ControlStore:
    """
    [역할] 로봇의 실시간 상태와 간호사의 호출 임무 목록을 SQLite 파일(hospital_internal.db)에
    영구 저장하고 꺼내오는 '중앙 데이터 창고지기' 클래스입니다.
    - 근본 원리: 웹 서버(app.py)와 ROS 2 노드(ros_manager.py)는 서로 다른 운영체제 프로세스이므로
      파이썬 변수(RAM)를 직접 공유할 수 없습니다. 대신 하드디스크의 SQLite 파일 하나를
      '공용 게시판'처럼 함께 읽고 쓰면서 실시간으로 상태를 동기화합니다.
    """

    def __init__(self, db_path="/home/haji/Documents/test/hospital_internal.db"):
        """
        [생성자] DB 파일 경로를 지정하고, 테이블이 없으면 자동으로 생성합니다.
        """
        self.db_path = db_path
        self.init_db()

    def connect(self):
        """
        [DB 연결 생성기] SQLite 파일에 접속하는 통로(Connection)를 열어 반환합니다.
        - timeout=10.0 : 웹 서버와 ROS 노드가 0.001초 차이로 동시에 DB를 쓰려고 할 때,
                         바로 에러를 내지 않고 최대 10초간 차례를 기다리도록 설정합니다.
        - row_factory  : 조회 결과를 단순 튜플 ('robot_a', 'IDLE', 85)이 아니라
                         컬럼 이름으로 꺼낼 수 있는 딕셔너리 형태 row['robot_id']로 만들어 줍니다.
        """
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        """
        [초기 테이블 및 기본 로봇 세팅]
        프로그램이 시작될 때 필요한 테이블 2개(robots, tasks)가 없으면 새로 만듭니다.
        * [실전 개선] 기존에는 실행할 때마다 DELETE FROM으로 데이터를 다 지웠으나,
          웹 서버와 ROS 노드를 따로 켜도 기존 데이터가 보존되도록 'INSERT OR IGNORE'를 사용합니다.
        """
        conn = self.connect()
        try:
            cur = conn.cursor()

            # 1. [동시성 향상] WAL(Write-Ahead Logging) 모드 활성화
            #    한쪽 프로그램이 DB에 데이터를 쓰는(Write) 동안에도 다른 프로그램이
            #    멈추지 않고 동시에 읽기(Read)를 수행할 수 있게 해주는 SQLite 핵심 설정입니다.
            cur.execute("PRAGMA journal_mode=WAL;")

            # 2. robots 테이블 생성 (models.py의 Robot 클래스 속성과 1:1 대응)
            #    - robot_id : 기본키(PRIMARY KEY)로 지정해 똑같은 이름의 로봇이 중복 등록되지 않게 방지
            #    - obstacle : SQLite에는 True/False 타입이 없으므로 정수 1(감지됨), 0(정상)으로 저장
            cur.execute("""
                CREATE TABLE IF NOT EXISTS robots (
                    robot_id TEXT PRIMARY KEY,
                    status TEXT,
                    battery INTEGER,
                    obstacle INTEGER,
                    location TEXT
                )
            """)

            # 3. tasks 테이블 생성 (models.py의 Task 클래스 속성과 1:1 대응)
            #    - id : AUTOINCREMENT를 붙여 새 호출이 들어올 때마다 1, 2, 3... 자동 증가
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    destination TEXT,
                    robot_id TEXT,
                    status TEXT
                )
            """)

            # 4. 초기 로봇 2대 등록 (INSERT OR IGNORE)
            #    - DB에 'robot_a'가 이미 존재하면 무시하고 기존 상태(배터리, 위치 등)를 그대로 유지합니다.
            #    - DB를 처음 생성했을 때만 초기값(IDLE, 배터리 85%/95%, 대기실)으로 등록됩니다.
            cur.execute(
                "INSERT OR IGNORE INTO robots VALUES ('robot_a', 'IDLE', 85, 0, '대기실')"
            )
            cur.execute(
                "INSERT OR IGNORE INTO robots VALUES ('robot_b', 'IDLE', 95, 0, '대기실')"
            )

            # 변경 사항을 하드디스크 파일에 최종 확정 기록 (commit을 안 하면 저장이 안 됨!)
            conn.commit()
        except Exception as e:
            print(f"[DB 초기화 오류] {e}")
        finally:
            # 작업이 끝나면 반드시 연결을 닫아 다른 프로그램이 접근할 수 있게 풀어줌
            conn.close()

    def reset_db(self):
        """
        [시뮬레이션/테스트 전용 초기화 함수]
        실제 운영 중에는 호출하지 않으며, main.py(시뮬레이터)처럼 처음부터 깨끗한 상태로
        테스트를 다시 돌리고 싶을 때만 명시적으로 호출해 데이터를 리셋합니다.
        """
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM robots")
            cur.execute("DELETE FROM tasks")
            # autoincrement 번호표도 1번부터 다시 시작하도록 초기화
            cur.execute("DELETE FROM sqlite_sequence WHERE name='tasks'")
            cur.execute("INSERT INTO robots VALUES ('robot_a', 'IDLE', 85, 0, '대기실')")
            cur.execute("INSERT INTO robots VALUES ('robot_b', 'IDLE', 95, 0, '대기실')")
            conn.commit()
        except Exception as e:
            print(f"[DB 리셋 오류] {e}")
        finally:
            conn.close()

    # -------------------------------------------------------------------------
    # [구역 1] 로봇(Robot) 정보 조회 및 상태 저장
    # -------------------------------------------------------------------------
    def get_robots(self):
        """
        DB의 robots 테이블에서 모든 행을 읽어와
        파이썬 객체 리스트([Robot('robot_a', ...), Robot('robot_b', ...)])로 변환해 반환합니다.
        """
        conn = self.connect()
        robot_list = []
        try:
            rows = conn.execute("SELECT * FROM robots").fetchall()
            for row in rows:
                # DB에 저장된 정수(1 또는 0)를 파이썬 논리값(True 또는 False)으로 복원
                is_obs = True if row["obstacle"] == 1 else False

                # 날것의 DB 행(Row) 데이터를 models.py의 Robot 설계도에 넣어 객체로 조립
                robot = Robot(
                    robot_id=row["robot_id"],
                    status=row["status"],
                    battery=row["battery"],
                    obstacle=is_obs,
                    location=row["location"],
                )
                robot_list.append(robot)
        except Exception as e:
            print(f"[로봇 조회 오류] {e}")
        finally:
            conn.close()
        return robot_list

    def get_robot_by_id(self, robot_id):
        """특정 로봇 1대(예: 'robot_a')의 최신 상태 객체만 콕 집어서 가져오는 도우미 함수"""
        robots = self.get_robots()
        for r in robots:
            if r.robot_id == robot_id:
                return r
        return None

    def save_robot(self, robot):
        """
        메모리에서 상태가 바뀐 Robot 객체(예: status가 MOVING으로 바뀜)를 받아
        DB의 해당 로봇 행에 덮어쓰기(UPDATE) 저장합니다.
        """
        conn = self.connect()
        try:
            # 파이썬 불리언(True/False)을 SQLite 저장용 정수(1/0)로 변환
            obs_num = 1 if robot.obstacle else 0

            # [보안/안전] SQL 인젝션 방지 및 특수문자 오류를 막기 위해 '?' 플레이스홀더 사용
            conn.execute(
                """
                UPDATE robots 
                SET status = ?, battery = ?, obstacle = ?, location = ?
                WHERE robot_id = ?
                """,
                (robot.status, robot.battery, obs_num, robot.location, robot.robot_id),
            )
            conn.commit()
        except Exception as e:
            print(f"[로봇 저장 오류] {e}")
        finally:
            conn.close()

    # -------------------------------------------------------------------------
    # [구역 2] 호출 임무(Task) 등록, 조회 및 상태 갱신
    # -------------------------------------------------------------------------
    def add_task(self, destination):
        """
        간호사가 웹에서 호출 버튼을 눌렀을 때 새 임무를 DB에 등록합니다.
        - 처음 등록될 때는 아직 담당 로봇이 없으므로 robot_id는 NULL, 상태는 'PENDING'(대기)입니다.
        """
        conn = self.connect()
        try:
            conn.execute(
                "INSERT INTO tasks (destination, robot_id, status) VALUES (?, NULL, 'PENDING')",
                (destination,),
            )
            conn.commit()
            print(f"[호출 접수] '{destination}'에서 로봇을 호출했습니다.")
        except Exception as e:
            print(f"[임무 추가 오류] {e}")
        finally:
            conn.close()

    def get_tasks(self, only_pending=False):
        """
        DB에 쌓인 임무 목록을 Task 객체 리스트로 반환합니다.
        - only_pending=True  : 배차기(Dispatcher)가 아직 배정 안 된 대기 임무만 찾을 때 사용
        - only_pending=False : 웹 대시보드나 ROS 브릿지가 전체 임무 현황을 볼 때 사용
        - ORDER BY id ASC    : 먼저 호출한 병동부터 순서대로(선입선출) 처리하기 위해 오름차순 정렬
        """
        conn = self.connect()
        task_list = []
        try:
            if only_pending:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE status = 'PENDING' ORDER BY id ASC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tasks ORDER BY id ASC"
                ).fetchall()

            for row in rows:
                task = Task(
                    task_id=row["id"],
                    destination=row["destination"],
                    robot_id=row["robot_id"],
                    status=row["status"],
                )
                task_list.append(task)
        except Exception as e:
            print(f"[임무 조회 오류] {e}")
        finally:
            conn.close()
        return task_list

    def update_task(self, task):
        """
        배차기가 로봇을 배정했거나(IN_PROGRESS), 간호사가 수령 완료(DONE)했을 때
        바뀐 Task 객체의 담당 로봇과 진행 상태를 DB에 반영합니다.
        """
        conn = self.connect()
        try:
            conn.execute(
                "UPDATE tasks SET robot_id = ?, status = ? WHERE id = ?",
                (task.robot_id, task.status, task.task_id),
            )
            conn.commit()
        except Exception as e:
            print(f"[임무 수정 오류] {e}")
        finally:
            conn.close()