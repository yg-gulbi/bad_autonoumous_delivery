#cat << 'EOF' > ~/Documents/test/web/app.py
import sys
from flask import Flask, render_template, request, redirect, url_for

# 기존 ROS 2 패키지 안의 store.py, dispatcher.py를 그대로 가져오기 위해 경로 추가
sys.path.append("/home/haji/Documents/test/src/bad_control")

from bad_control.store import ControlStore
from bad_control.dispatcher import Dispatcher

app = Flask(__name__)

# ROS 2 노드(ros_manager.py)와 동일한 SQLite DB 파일을 공유함
DB_PATH = "/home/haji/Documents/test/hospital_internal.db"
store = ControlStore(DB_PATH)
dispatcher = Dispatcher(store)


@app.route("/")
def index():
    """메인 관제 화면: DB에서 현재 로봇 상태와 임무 목록을 읽어 화면에 표시"""
    robots = store.get_robots()
    tasks = store.get_tasks()
    return render_template("index.html", robots=robots, tasks=tasks)


@app.route("/call", methods=["POST"])
def call_robot():
    """간호사 호출 버튼 클릭 시: DB에 임무 등록 후 즉시 배차기(Dispatcher) 가동"""
    destination = request.form.get("destination")
    if destination:
        store.add_task(destination)
        dispatcher.run_dispatch()  # 놀고 있는 로봇 중 배터리가 가장 많은 로봇 자동 배정
    return redirect(url_for("index"))


@app.route("/complete/<int:task_id>", methods=["POST"])
def complete_task(task_id):
    """수령 완료 버튼 클릭 시: 임무 종료 및 로봇 대기실 복귀 + 대기열 자동 재할당"""
    dispatcher.complete_and_return(task_id)
    return redirect(url_for("index"))


if __name__ == "__main__":
    # 0.0.0.0으로 열어두면 같은 Wi-Fi의 태블릿/스마트폰에서도 접속 가능
    app.run(host="0.0.0.0", port=5000, debug=True)