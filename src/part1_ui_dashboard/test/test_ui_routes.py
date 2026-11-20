import json

import pytest

from part1_ui_dashboard.web import Feed, create_app


@pytest.fixture
def monitor():
    messages, services = [], []
    feed = Feed()
    def trigger(topic):
        services.append(topic)
        return False, "침상 이탈 해제 전에는 재개할 수 없습니다"
    app = create_app(feed, {"zones": {"A": {"x": 1, "y": 2}}}, lambda *a: messages.append(a), trigger)
    return app.test_client(), messages, services, feed


@pytest.mark.parametrize("path,body,topic,ref,expected", [
    ("mission", {"level": 1, "destination": "B"}, "/ui/mission_req", "req_id",
     {"type": "CALL", "destination": "B", "level": 1, "requested_by": "조작자"}),
    ("command", {"cmd": "GO_ZONE", "robot": "robot_b", "zone": "E"}, "/ui/operator_cmd", "cmd_id",
     {"cmd": "GO_ZONE", "robot": "robot_b", "zone": "E", "by": "조작자"}),
    ("decision", {"card_id": "K-3", "choice": "yield_a"}, "/ui/decision", "card_id",
     {"choice": "yield_a", "by": "조작자"}),
])
def test_posts(monitor, path, body, topic, ref, expected):
    client, messages, _, _ = monitor
    res = client.post("/api/" + path, json={**body, "operator": "조작자"})
    assert res.status_code == 202
    (actual, text), = messages
    assert actual == topic
    assert json.loads(text) == {"v": 1, ref: res.json["ref"], **expected}


def test_invalid_requests_do_not_publish(monitor):
    client, messages, _, _ = monitor
    for body in ([], {"level": 4, "destination": "A"}, {"level": 1}):
        assert client.post("/api/mission", json=body).status_code == 400
    assert client.post("/api/command", json={"cmd": "MISSING"}).status_code == 400
    assert client.post("/api/missing", json={}).status_code == 404
    assert not messages


def test_services_and_static_files(monitor):
    client, _, services, _ = monitor
    for action in ("pause", "resume"):
        reply = client.post("/api/service/" + action)
        assert reply.json == {"success": False, "message": "침상 이탈 해제 전에는 재개할 수 없습니다"}
    assert services == ["/robot/pause", "/robot/resume"]
    assert client.get("/api/map").json["zones"]["A"]["y"] == 2
    for path in ("/", "/static/app.js", "/static/map.js", "/static/style.css"):
        assert client.get(path).status_code == 200


def test_unavailable_ros_peer_is_reported_to_the_browser():
    def disconnected(topic, text):
        raise ConnectionError("관제 요청 구독자가 연결되지 않았습니다.")

    def timeout(topic):
        raise TimeoutError("관제 서비스 응답 시간이 초과됐습니다.")

    client = create_app(Feed(), {}, disconnected, timeout).test_client()
    res = client.post("/api/mission", json={"level": 1, "destination": "A"})
    assert res.status_code == 503 and "구독자" in res.json["message"]
    res = client.post("/api/service/pause")
    assert res.status_code == 503 and res.json["success"] is False


def unpack(packet):
    return json.loads(packet.decode().split("data: ", 1)[1])


def test_live_sse_reconnect_retention_and_card_close(monitor):
    client, _, _, feed = monitor
    for n in range(52):
        feed.receive("event", {"type": "ARRIVE", "detail": {"n": n}})
    feed.receive("state", {"open_cards": []})
    feed.receive("card", {"card_id": "K-1"})
    stream = client.get("/events", buffered=False)
    assert stream.mimetype == "text/event-stream"
    snap = unpack(next(stream.response))
    assert len(snap["events"]) == 50 and snap["events"][0]["detail"]["n"] == 2
    assert snap["cards"] == [{"card_id": "K-1"}] and snap["age"] >= 0
    feed.receive("ack", {"ref": "mon-test"})
    assert unpack(next(stream.response)) == {"ref": "mon-test"}
    stream.close()
    feed.receive("event", {"type": "CARD_CLOSED", "detail": {"card_id": "K-1"}})
    feed.receive("card", {"card_id": "K-1"})
    with client.get("/events", buffered=False) as reopened:
        snap = unpack(next(reopened.response))
        assert not snap["cards"] and snap["acks"] == [{"ref": "mon-test"}]


def test_sse_clients_receive_independent_updates_and_slow_reader_resynchronizes(monitor):
    client, _, _, feed = monitor
    with client.get("/events", buffered=False) as slow, client.get("/events", buffered=False) as fast:
        for stream in (slow, fast):
            assert unpack(next(stream.response))["state"] is None
        for tick in range(300):
            state = {"stamp": tick, "open_cards": []}
            feed.receive("state", state)
            assert unpack(next(fast.response)) == state
        snapshot = unpack(next(slow.response))
        assert snapshot["state"]["stamp"] == 299


@pytest.mark.parametrize("action,form,expected", [
    *[("bed", {"mode": mode}, ("bed", mode)) for mode in ("off", "normal", "exit")],
    ("stall",{"robot":"robot_a","seconds":40},None),
    ("drop_link",{"robot":"robot_b","seconds":10},None),
    ("fail_next",{"robot":"robot_b"},None),("reject_next",{"robot":"robot_a"},None),
    ("battery",{"robot":"robot_a","value":25},None),
    ("yolo",{"robot":"robot_b"},None),
])
def test_demo_tools_only(action,form,expected):
    published=[]
    for enabled,code in [(False,403),(True,200)]:
        app=create_app(Feed(),{},lambda *args:published.append(args),None,dev_tools=enabled,
                       set_bed_mode=lambda mode: published.append(("bed", mode)))
        assert app.test_client().post("/api/test/"+action,json=form).status_code==code
    assert len(published)==1
    if expected:assert published[0]==expected
    else:assert published[0][0]=="/mock/control" and json.loads(published[0][1])=={**form,"action":action}


def test_map_routes_with_and_without_map(tmp_path):
    from part1_ui_dashboard.ui_api import map_meta
    path=tmp_path/"map.yaml"
    for available in [False,True]:
        meta,image=map_meta(path)
        client=create_app(Feed(),{},None,None,meta=meta,image=image).test_client()
        assert client.get("/map/meta").json["available"] is available
        resp=client.get("/map/image")
        assert resp.status_code==(200 if available else 404)
        if available:
            from part1_ui_dashboard.occupancy import read_pgm
            w,h,max_value,pixels = read_pgm(resp.data)
            assert resp.data.startswith(b"P5\n") and (w,h)==(meta["width"],meta["height"])
            assert max_value==255 and set(pixels)=={205}  # two isolated wall and floor cells removed
        else:
            path.write_text("image: map.pgm\n")
            (tmp_path/"map.pgm").write_bytes(b"P2\n# map\n2 2\n255\n0 0 255 255\n")

def test_mock_bed_rejects_one_shot_or_unknown_mode():
    client = create_app(Feed(), {}, None, None, dev_tools=True).test_client()
    for form in ({"value": False}, {"mode": "sometimes"}, {}):
        assert client.post("/api/test/bed", json=form).status_code == 400

def test_ui_starting_after_monitor_outage_recovers_pending_card():
    feed = Feed()
    card = {"card_id": "K-camera", "kind": "BED_MONITOR_LOST"}
    state = {"open_cards": ["K-camera"], "bed_exit": {"monitor": {"card": card}}}
    feed.receive("state", state)
    feed.receive("state", state)
    assert list(feed.cards.values()) == [card]
    assert [kind for _, kind, _ in feed.recent].count("card") == 1
    feed.receive("event", {"type": "CARD_CLOSED", "detail": {"card_id": "K-camera"}})
    feed.receive("state", state)
    assert not feed.cards


@pytest.mark.parametrize("cmd", ["PAUSE_ALL", "RESUME_ALL"])
def test_42_global_dashboard_commands_include_operator(monitor,cmd):
    client, messages, services, _ = monitor
    assert client.post("/api/command",json={"cmd":cmd,"operator":"김당직"}).status_code == 202
    [(topic,payload)] = messages
    data = json.loads(payload)
    assert topic == "/ui/operator_cmd" and (data["cmd"],data["by"]) == (cmd,"김당직") and not services


def test_39_per_robot_mock_classifier_control():
    messages = []
    client = create_app(Feed(),{},lambda *m: messages.append(m),None,dev_tools=True).test_client()
    for robot,kind in (("robot_a","person"),("robot_b","silent")):
        assert client.post("/api/test/obstacle",json={"robot":robot,"kind":kind}).status_code == 200
        topic,text = messages[-1]
        assert topic == "/mock/control" and json.loads(text) == {"robot":robot,"action":"obstacle","kind":kind}
    assert client.post("/api/test/obstacle",json={"robot":"robot_a","kind":"invalid"}).status_code == 400
    assert len(messages) == 2


def test_field_commands_and_mock_controls_use_configured_robot_ids():
    sent = []
    client = create_app(Feed(), {"robot_names": {"porter": "운반 로봇"}}, lambda *msg: sent.append(msg),
                        None, dev_tools=True).test_client()
    for robot, expected in [("robot_a", 400), ("porter", 202)]:
        assert client.post("/api/command", json={"cmd": "GO_ZONE", "robot": robot, "zone": "D"}).status_code == expected
        response = client.post("/api/test/stall", json={"robot": robot, "seconds": 1})
        assert response.status_code == (400 if expected == 400 else 200)
    assert [json.loads(msg)["robot"] for _, msg in sent] == ["porter", "porter"]



def test_v2_medication_stops_keep_the_selected_order_and_each_item(monitor):
    client, messages, _, _ = monitor
    for zones in ("E", "DBAFEC"):
        stops = [{"zone": z, "item": f"시험 약품 {i}"} for i, z in enumerate(zones, 1)]
        response = client.post("/api/mission", json={"type": "MED_ROUND", "level": 3, "stops": stops, "operator": "당직"})
        assert response.status_code == 202
        topic, raw = messages[-1]
        assert topic == "/ui/mission_req"
        assert json.loads(raw) == {"v": 1, "req_id": response.json["ref"], "requested_by": "당직",
                                   "type": "MED_ROUND", "level": 3, "stops": stops}


def test_v2_fonts_served_offline_and_colors_scoped_to_tokens(monitor):
    import re

    client, *_ = monitor
    css = client.get("/static/style.css").get_data(as_text=True)
    root = re.search(r":root\s*\{[^}]*\}", css)
    assert root and not re.search(r"#[\da-fA-F]{3,8}\b|rgba?\(", css[root.end():])
    assert "https://" not in css and "https://" not in client.get("/").get_data(as_text=True)
    for face in ("Regular", "Medium", "SemiBold", "Bold"):
        url = f"fonts/IBMPlexSansKR-{face}.woff2"
        assert url in css and client.get("/static/" + url).data.startswith(b"wOF2")
    assert b"SIL OPEN FONT LICENSE" in client.get("/static/fonts/OFL.txt").data


def test_63_obstacle_photo_is_memory_only_and_tied_to_the_open_card(tmp_path):
    from part1_ui_dashboard.web import Photos
    feed, photos = Feed(), Photos(clock=lambda: 0.0)
    client = create_app(feed, {"robot_names": {"robot_a": "침상형 이송 로봇", "robot_b": "약품 운송 로봇"}},
                        None, None, photos=photos, db_path=tmp_path / "missing.db").test_client()
    jpeg = b"\xff\xd8\xff\xe0fake-jpeg\xff\xd9"
    # No open obstacle card yet: nothing to show even with a photo in memory.
    photos.put("robot_b", "raw", jpeg, at=100.0)
    assert client.get("/api/obstacle_image/robot_b?kind=raw").status_code == 404
    card = {"v": 1, "card_id": "K-1", "kind": "BLOCKED", "robots": ["robot_b"], "title": "경로 막힘",
            "reason": "의료용 상자가 길을 막고 있음 (로봇 앞 약 0.4 m)", "options": [{"id": "retry", "label": "치웠음, 다시 출발"}],
            "recommended": None, "default": None, "timeout_s": None, "created_at": 104.0,
            "obstacle": {"kind": "OBJECT", "reported_at": 103.5, "label_ko": "의료용 상자", "confidence": 0.93}}
    feed.receive("card", card)
    reply = client.get("/api/obstacle_image/robot_b?kind=raw")
    assert reply.status_code == 200 and reply.mimetype == "image/jpeg" and reply.data == jpeg
    assert reply.headers["X-Received-At"] == "100.000" and reply.headers["Cache-Control"] == "no-store"
    assert client.get("/api/obstacle_image/robot_b?kind=masked").status_code == 404  # no PART 06 photo
    photos.put("robot_b", "masked", b"\xff\xd8masked\xff\xd9", at=103.9)
    assert client.get("/api/obstacle_image/robot_b?kind=masked").data.startswith(b"\xff\xd8masked")
    # An old photo (from an earlier stop) is not shown with this card.
    photos.put("robot_b", "raw", jpeg, at=98.4)
    assert client.get("/api/obstacle_image/robot_b?kind=raw").status_code == 404
    for path in ("/api/obstacle_image/robot_a?kind=raw", "/api/obstacle_image/robot_b?kind=thumb",
                 "/api/obstacle_image/robot_x?kind=raw"):
        assert client.get(path).status_code == 404
    feed.receive("event", {"type": "CARD_CLOSED", "detail": {"card_id": "K-1"}})
    photos.put("robot_b", "raw", jpeg, at=104.0)
    assert client.get("/api/obstacle_image/robot_b?kind=raw").status_code == 404


def test_63_photo_reference_is_the_report_time_when_the_verdict_timed_out():
    from part1_ui_dashboard.web import Photos
    feed, photos = Feed(), Photos()
    client = create_app(feed, {"robot_names": {"robot_a": "A"}}, None, None, photos=photos).test_client()
    feed.receive("card", {"card_id": "K-2", "kind": "BLOCKED", "robots": ["robot_a"], "created_at": 209.0,
                          "obstacle": {"kind": "UNKNOWN", "reported_at": 203.0}})
    photos.put("robot_a", "raw", b"\xff\xd8x\xff\xd9", at=202.9)  # card opened 6 s after the photo
    assert client.get("/api/obstacle_image/robot_a").status_code == 200


def test_yolo_photo_matches_current_sighting_without_replacing_the_abort_photo(monkeypatch):
    from part1_ui_dashboard import web

    clock = [100.0]
    monkeypatch.setattr(web, "monotonic", lambda: clock[0])
    feed, photos = Feed(), web.Photos(clock=lambda: clock[0])
    client = create_app(feed, {"robot_names": {"robot_b": "약품 운송 로봇"}}, None, None, photos=photos).test_client()
    feed.receive("state", {"stamp": 100.0, "open_cards": [], "robots": {"robot_b": {"yolo": {"at": 100.0}}}})
    url = "/api/obstacle_image/robot_b"
    photos.put("robot_b", "yolo", b"old", at=89.0)
    assert client.get(url + "?kind=yolo&at=100").status_code == 404
    photos.put("robot_b", "yolo", b"boxed")
    assert client.get(url + "?kind=yolo&at=89").status_code == 404
    assert client.get(url + "?kind=yolo&at=100").data == b"boxed"
    assert client.get(url + "?kind=raw").status_code == 404
    feed.receive("card", {"card_id": "K-failure", "robots": ["robot_b"], "created_at": 104.0,
                          "obstacle": {"reported_at": 103.0}})
    photos.put("robot_b", "raw", b"abort", at=103.0)
    assert client.get(url + "?kind=yolo&at=100").data == b"boxed"
    assert client.get(url + "?kind=raw").data == b"abort"
    clock[0] = 130.0  # No fresh fleet state, even with a browser keeping the old link.
    assert client.get(url + "?kind=yolo&at=100").status_code == 404
