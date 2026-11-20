"""HTTP/SSE interface. The injected publisher and service client own ROS transport."""
from collections import deque
from threading import Condition, Lock
from time import monotonic, time

from flask import Flask, Response, jsonify, request

from part3_fleet_manager.core import schemas
from . import ui_api
from .history import history_api


PHOTO_KINDS = ("raw", "masked")
PHOTO_GRACE_S = 5.0


class Photos:
    """Latest obstacle photo per robot and kind, in memory only (never written to bad.db).

    raw = PART 04 /<ns>/hospital_nav/obstacle_image, masked = PART 06 /<ns>/obstacle_classifier/image/compressed.
    """

    def __init__(self, clock=time):
        self.clock = clock
        self._lock = Lock()
        self._latest = {}

    def put(self, robot, kind, data, at=None):
        with self._lock:
            self._latest[(robot, kind)] = (bytes(data), self.clock() if at is None else at)

    def get(self, robot, kind):
        with self._lock:
            return self._latest.get((robot, kind))


class Feed:
    def __init__(self, limit=50):
        self.changed = Condition()
        self.state = self.received = None
        self.cards = {}
        self.closed = deque(maxlen=100)
        self.events = deque(maxlen=limit)
        self.acks = deque(maxlen=100)
        self.sequence = 0
        self.recent = deque(maxlen=256)

    def receive(self, kind, data):
        with self.changed:
            if kind == "state":
                previous = set(self.state["open_cards"]) if self.state else set()
                self.state, self.received = data, monotonic()
                # A live UI may start after the required webcam's initial loss event.
                card = data.get("bed_exit", {}).get("monitor", {}).get("card")
                if card and card["card_id"] not in self.cards and card["card_id"] not in self.closed:
                    self.cards[card["card_id"]] = card
                    self.sequence += 1
                    self.recent.append((self.sequence, "card", card))
                for card_id in previous - set(data["open_cards"]):
                    self.cards.pop(card_id, None)
                    self.closed.append(card_id)
            elif kind == "card":
                if data["card_id"] in self.closed:
                    return
                self.cards[data["card_id"]] = data
            elif kind == "event":
                self.events.append(data)
                if data["type"] == "CARD_CLOSED":
                    card_id = data["detail"].get("card_id")
                    self.cards.pop(card_id, None)
                    self.closed.append(card_id)
            elif kind == "ack":
                self.acks.append(data)
            self.sequence += 1
            self.recent.append((self.sequence, kind, data))
            self.changed.notify_all()

    def recent_yolo(self, robot):
        with self.changed:
            if self.state is None:
                return None
            report = self.state.get("robots", {}).get(robot, {}).get("yolo")
            if not report:
                return None
            now = self.state["stamp"] + (monotonic() - self.received)
            return report if now < report["at"] + 30 else None

    def obstacle_card(self, robot):
        """The newest open obstacle card (BRIEF 5.5 report mode) for this robot, if any."""
        with self.changed:
            cards = [c for c in self.cards.values() if c.get("obstacle") and robot in c.get("robots", [])]
        return max(cards, key=lambda c: c["created_at"], default=None)

    def stream(self):
        cursor = None
        while True:
            with self.changed:
                if cursor == self.sequence:
                    self.changed.wait_for(lambda: cursor != self.sequence, timeout=10)
                if cursor is None or (self.recent and cursor < self.recent[0][0] - 1):
                    snapshot = {"state": self.state, "cards": list(self.cards.values()),
                                "events": list(self.events), "acks": list(self.acks),
                                "age": None if self.received is None else monotonic() - self.received}
                    batch = [("snapshot", snapshot)]
                else:
                    batch = [(kind, data) for seq, kind, data in self.recent if seq > cursor]
                cursor = self.sequence
            # A slow or disconnected browser must never hold up the ROS executor.
            if not batch:
                yield ": heartbeat\n\n"
            for kind, data in batch:
                yield f"event: {kind}\ndata: {schemas.dumps(data)}\n\n"


def create_app(feed, geometry, publish, trigger, *, meta=None, image=None, dev_tools=False, set_bed_mode=None, db_path=None,
               photos=None):
    app = Flask(__name__, static_folder="static")
    app.register_blueprint(history_api(db_path, geometry.get("names", {}), geometry.get("robot_names")))
    robots = tuple(geometry.get("robot_names", schemas.ROBOT_IDS))
    routes = {"mission": ("/ui/mission_req", ui_api.mission, "req_id"),
              "command": ("/ui/operator_cmd", lambda form: ui_api.command(form, robots=robots), "cmd_id"),
              "decision": ("/ui/decision", ui_api.decision, "card_id")}

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/api/map")
    def map_config():
        return jsonify(geometry)

    @app.get("/map/meta")
    def map_metadata():
        return jsonify(meta or {"available": False})

    @app.get("/map/image")
    def map_image():
        return Response(image, mimetype="image/x-portable-graymap") if image else ("", 404)

    @app.get("/api/obstacle_image/<robot>")
    def obstacle_image(robot):
        """Return the photo for an open obstacle card or the specified recent YOLO report."""
        kind = request.args.get("kind", "raw")
        if robot in robots and photos is not None and kind == "yolo":
            report = feed.recent_yolo(robot)
            photo = photos.get(robot, kind)
            if (not report or not photo or request.args.get("at", type=float) != report["at"]
                    or abs(photo[1] - report["at"]) > PHOTO_GRACE_S):
                return jsonify(message="이 감지에 해당하는 사진이 없습니다."), 404
            return Response(photo[0], mimetype="image/jpeg", headers={"Cache-Control": "no-store"})
        card = feed.obstacle_card(robot) if robot in robots and kind in PHOTO_KINDS else None
        item = photos.get(robot, kind) if photos is not None and card else None
        if item is None:
            return jsonify(message="표시할 장애물 사진이 없습니다."), 404
        data, received = item
        reported = card["obstacle"].get("reported_at")
        since = min(card["created_at"], reported) if isinstance(reported, (int, float)) else card["created_at"]
        if received < since - PHOTO_GRACE_S:
            return jsonify(message="이 카드보다 오래된 사진입니다."), 404
        return Response(data, mimetype="image/jpeg",
                        headers={"Cache-Control": "no-store", "X-Received-At": f"{received:.3f}"})

    @app.errorhandler(ConnectionError)
    def unavailable(error):
        return jsonify(message=str(error)), 503

    @app.post("/api/test/<action>")
    def test_tool(action):
        if not dev_tools:
            return jsonify(message="실기 모드에서는 시험 도구를 사용할 수 없습니다."), 403
        form = request.get_json(silent=True)
        if not isinstance(form, dict):
            return jsonify(message="시험 값을 확인하세요."), 400
        if action == "bed" and form.get("mode") in ("off", "normal", "exit"):
            set_bed_mode(form["mode"])
            return jsonify(mode=form["mode"], message="가짜 침상 감시 설정을 적용했습니다.")
        elif action in ("stall", "drop_link", "fail_next", "reject_next", "battery", "obstacle", "yolo") and form.get("robot") in robots:
            data = {"robot": form["robot"], "action": action}
            if action == "obstacle":
                if form.get("kind") not in ("medical_box", "person", "object", "none", "error", "silent"):
                    return jsonify(message="장애물 분류 결과를 선택하세요."), 400
                data["kind"] = form["kind"]
            if action in ("stall", "drop_link", "battery"):
                from math import isfinite
                key = "value" if action == "battery" else "seconds"
                value = form.get(key)
                if type(value) not in (int, float) or not 0 <= value or not isfinite(value) or (action == "battery" and value > 100):
                    return jsonify(message="시험 수치를 확인하세요."), 400
                data[key] = value
            publish("/mock/control", schemas.dumps(data))
        else:
            return jsonify(message="알 수 없는 시험 요청입니다."), 400
        return jsonify(message="시험 입력을 보냈습니다.")

    @app.get("/events")
    def events():
        return Response(feed.stream(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/<action>")
    def post(action):
        if action not in routes:
            return jsonify(message="알 수 없는 요청입니다."), 404
        form = request.get_json(silent=True)
        if not isinstance(form, dict):
            return jsonify(message="JSON 객체가 필요합니다."), 400
        topic, build, key = routes[action]
        try:
            payload = build(form)
        except schemas.SchemaError as exc:
            return jsonify(message=exc.user_message()), 400
        except ValueError as exc:
            return jsonify(message=str(exc)), 400
        publish(topic, schemas.dumps(payload))
        return jsonify(ref=payload[key]), 202

    @app.post("/api/service/<action>")
    def service(action):
        if action not in ("pause", "resume"):
            return jsonify(message="알 수 없는 서비스입니다."), 404
        try:
            ok, message = trigger("/robot/" + action)
        except TimeoutError as exc:
            return jsonify(success=False, message=str(exc)), 503
        return jsonify(success=ok, message=message)

    return app
