"""BRIEF 38: L14 reads the same WAL database as the controller."""
from contextlib import closing
import csv
from io import StringIO
import sqlite3

import pytest

from part1_ui_dashboard.history import open_history
from part1_ui_dashboard.web import Feed, create_app
from part3_fleet_manager.core.mission import Mission
from part3_fleet_manager.core.store import Store


def event(kind, ts, robot=None, mid=None, **detail):
    return {"type":kind, "ts":ts, "robot":robot, "mission_id":mid, "detail":detail}


@pytest.fixture
def journal(tmp_path):
    path = tmp_path / "운영 #1.db"
    store = Store(path)
    jobs = []
    for i in range(55):
        patient = {"chart_no": "T-1", "name": "환자", "tag_id": "손목-4"} if i == 0 else {"chart_no": "T-1"}
        job = Mission(store.next_id(),"robot_a","PATIENT_TRANSPORT",2,[{"zone":"A"},{"zone":"E"}],patient,i)
        job.req_id, job.status, job.requested_by, job.finished_at = f"R-{i}", "DONE", "간호사", i+1
        job.stop_log[0].update(status="CONFIRMED",arrived_at=i+.1,confirmed_at=i+.2,confirmed_by="간호사")
        jobs.append(job)
    call = Mission(store.next_id(),"robot_b","CALL",1,[{"zone":"B","item":"거즈"}],None,56)
    call.status = "CANCELED"
    jobs.append(call)
    events = [event("CONFIRM_STEP",i,"robot_a",jobs[0].id,reason="OK",by="간호사",destination="A") for i in range(55)]
    events.extend([
        event("DEADLOCK_DETECTED",56,card_id="K-1",reason="교착"),
        event("CARD_OPEN",56,card={"card_id":"K-1","kind":"DEADLOCK","robots":["robot_a","robot_b"],
            "title":"교착 해소","reason":"방을 비워야 합니다","created_at":56,"options":[{"id":"yield_a","label":"Robot A 양보"}]}),
        event("CARD_CLOSED",57,card_id="K-1",reason="selected",choice="yield_a",by="책임자"),
        event("YIELD_START",57,"robot_a",source_room="ROOM_B",detected_at=56),
        event("RESOURCE_RELEASE",61,"robot_a",resources=["ROOM_B"]),
        event("RESOURCE_HOLD",62,reason="복도 대기"),event("RESOURCE_HOLD",63,reason="복도 대기"),
        event("PAUSE",64,reason="수동 정지",by="책임자")])
    store.save(jobs, events)
    app = create_app(Feed(),{"names":{"A":"A 구역"}},None,None,db_path=path)
    try:
        yield path, store, app.test_client()
    finally:
        store.close()


def test_38_missions_patients_detail_and_pagination(journal):
    path, store, client = journal
    base = "/api/missions?type=PATIENT_TRANSPORT&robot=robot_a&level=2&status=DONE"
    first = client.get(base).get_json()
    second = client.get(base+"&page=2").get_json()
    assert len(first["items"]) == 50 and first["has_more"]
    assert len(second["items"]) == 5 and not second["has_more"]
    assert first["items"][0]["id"] == "M-0055" and second["items"][-1]["id"] == "M-0001"
    assert len(client.get(base+"&from=12&to=17").get_json()["items"]) == 6
    assert len(client.get("/api/missions?status=CANCELED&robot=robot_b").get_json()["items"]) == 1
    assert not client.get(base+"&page=3").get_json()["items"]
    detail = client.get("/api/missions/M-0001").get_json()
    assert detail["mission"]["patient"]["tag_id"] == "손목-4"
    assert detail["stops"][0]["confirmed_by"] == "간호사" and detail["stops"][1]["seq"] == 2
    assert [e["ts"] for e in detail["events"]] == list(range(55))
    recent = client.get("/api/patients/recent").get_json()["items"]
    assert len(recent) == 10 and recent[0]["name"] == "환자" and recent[0]["id"] == "M-0055"
    assert store.db.execute("SELECT count(*),updated_at FROM patients").fetchone()[:] == (1,54)
    assert client.get("/api/missions/unknown").status_code == 404
    assert client.get(base+"&page=0").status_code == 400
    assert client.get(base+"&from=invalid").status_code == 400


def test_38_events_cards_period_kpi_and_csv(journal):
    _, _, client = journal
    query = "?robot=robot_a&type=CONFIRM_STEP&mission=M-0001&q=간호사"
    first = client.get("/api/events"+query).get_json()
    last = client.get("/api/events"+query+"&page=2").get_json()
    assert len(first["items"]) == 50 and first["has_more"] and len(last["items"]) == 5
    assert first["items"][0]["ts"] == 54 and last["items"][-1]["ts"] == 0
    assert client.get("/api/events?q=확인%20완료&from=10&to=10").get_json()["items"][0]["by"] == "간호사"
    export = client.get("/api/events.csv"+query+"&page=2")
    rows = list(csv.reader(StringIO(export.data.decode("utf-8-sig"))))
    assert export.mimetype == "text/csv" and len(rows) == 56 and rows[1][1:] == ["CONFIRM_STEP","robot_a","M-0001","간호사","확인 완료"]
    card, = client.get("/api/cards?from=56&to=56").get_json()["items"]
    assert (card["title"],card["by"],card["choice_label"],card["close_reason"]) == ("교착 해소","책임자","Robot A 양보","selected")
    assert client.get("/api/cards?to=55").get_json()["items"] == []
    assert client.get("/api/kpi?from=55&to=64").get_json() == {
        "hold_count":2,"deadlock_count":1,"deadlock_resolve_s_avg":5.0,"pause_count":1}
    assert client.get("/api/kpi?from=57").get_json()["deadlock_resolve_s_avg"] is None
    assert client.get("/api/kpi?to=60").get_json()["deadlock_resolve_s_avg"] is None


def test_38_read_only_and_missing_file(journal, tmp_path):
    path, _, _ = journal
    with closing(open_history(path)) as db:
        assert db.execute("SELECT count(*) FROM missions").fetchone()[0] == 56
        with pytest.raises(sqlite3.OperationalError,match="readonly"):
            db.execute("UPDATE patients SET name='다른 이름'")
    missing = tmp_path / "absent.db"
    client = create_app(Feed(),{"zones":{}},None,None,db_path=missing).test_client()
    for url in ("missions","missions/M-1","patients/recent","events","events.csv","kpi","cards"):
        result = client.get("/api/"+url)
        assert result.status_code == 503 and result.get_json()["message"] == "기록 DB에 연결할 수 없습니다"
    assert client.get("/").status_code == client.get("/api/map").status_code == 200
    assert not missing.exists()


def test_38_csv_cap_and_card_second_page(journal):
    _, store, client = journal
    with store.db:
        store.db.executemany("""INSERT INTO events(type,ts,detail_json) VALUES('WARNING',1,?)""",
                             [('{"reason":"=demo"}',)]*10005)
        store.db.executemany("""INSERT INTO cards(id,kind,payload_json,opened_at) VALUES(?,'BLOCKED','{}',1)""",
                             [(f"K-more-{i}",) for i in range(51)])
    rows = csv.reader(StringIO(client.get("/api/events.csv?type=WARNING").data.decode("utf-8-sig")))
    assert sum(1 for _ in rows) == 10001
    assert "'=demo" in client.get("/api/events.csv?type=WARNING").data.decode("utf-8-sig")
    assert len(client.get("/api/cards?to=2&page=2").get_json()["items"]) == 1


def test_41_present_old_and_current_events_in_both_tables(journal):
    from part1_ui_dashboard.ui_api import present_event
    _, store, client = journal
    names = {"A": "A 구역"}
    rows = [
        ("CONFIRM_STEP", {"reason":"MISMATCH"}, "내용 불일치 — 운영자 결정 대기"),
        ("CONFIRM_STEP", {"reason":"OK"}, "확인 완료"),
        ("MISSION_DONE", {"reason":"DONE"}, "모든 정류장 확인 완료"),
        ("CARD_CLOSED", {"reason":"selected","choice":"recheck","card_id":"K-old"}, "'다시 확인' 선택"),
        ("CARD_CLOSED", {"reason":"timeout","card_id":"K-old"}, "시간 초과 — 기본 선택으로 진행"),
        ("CARD_CLOSED", {"reason":"resolved","card_id":"K-old"}, "상황이 풀려 닫힘"),
        ("RESOURCE_REQUEST", {"reason":"robot_a이 A행 구간을 요청했습니다.","destination":"A"},
         "Robot A — A 구역으로 가는 구간 요청"),
        ("WARNING", {"reason":"UNKNOWN_CODE"}, ""),
        ("ARRIVE", {"reason":"A 구역 도착","destination":"A"}, "A 구역 도착")]
    events = [event(k, 90+i, "robot_a", "M-0001", **d) for i,(k,d,_) in enumerate(rows)]
    store.save([],events)
    log = client.get("/api/events?mission=M-0001&from=90").json["items"][::-1]
    detail = client.get("/api/missions/M-0001").json["events"][-len(rows):]
    for (_, _, reason), raw, logged, detailed in zip(rows,events,log,detail,strict=True):
        live = present_event(raw,names)
        assert live["detail"]["reason"] == logged["detail"]["reason"] == detailed["detail"]["reason"] == reason
        assert live["title"] == logged["title"] == detailed["title"]
    assert log[-1]["title"] == "Robot A · A 구역 도착"
