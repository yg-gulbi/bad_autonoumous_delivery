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
