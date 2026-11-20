"""BRIEF 41: generate public event types with real fleet components, then read them through Flask."""
import re
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path

from part1_ui_dashboard.ui_api import EVENTS, present_event
from part1_ui_dashboard.web import Feed, create_app
from part3_fleet_manager.core.config import load_config
from part3_fleet_manager.core.deadlock import DeadlockManager
from part3_fleet_manager.core.resource_manager import ResourceManager, RobotReport
from part3_fleet_manager.core.segments import SegmentManager
from part3_fleet_manager.core.store import Store

A, B = "robot_a", "robot_b"


def traffic(cfg):
    convoy = ResourceManager(replace(cfg, corridor=replace(cfg.corridor, mode="DIRECTIONAL")))
    reports = {A:RobotReport(cfg.zones["D"],True), B:RobotReport(cfg.zones["A"],True)}
    for r in reports:
        convoy.request(r,"F",1,0)
    convoy.tick(1,reports)
    reports[A] = RobotReport((-1.55,-2.8),True,safe_pause=True)
    reports[B] = RobotReport((-1.55,-1.5),True)
    convoy.tick(2,reports)
    reports[A] = RobotReport((-1.55,-3.0),True)
    convoy.tick(3,reports)
    yield from convoy.events

    for timeout in (False, True):
        rm = ResourceManager(cfg)
        rm.request(A,"F",1,0)
        rm.request(B,"A",3,0)
        rm.tick(1,{A:RobotReport(cfg.zones["A"],True), B:RobotReport(cfg.zones["F"],True)})
        reports = {A:RobotReport((-1.55,-2),True), B:RobotReport((-1.55,-3),True)}
        rm.tick(2,reports)
        if not timeout:
            reports[B] = RobotReport((-1.55,-1.5),True)
        rm.tick(12,reports)
        reports[A] = RobotReport(cfg.zones["A"],False)
        rm.tick(13,reports)
        rm.clear_unknown("CORRIDOR","현장 담당자",14)
        yield from rm.events

    # Deadlocks need the 4I room rule (a robot standing in a room keeps it).
    rm = ResourceManager(replace(cfg, corridor=replace(cfg.corridor, room_mode="ROOM")))
    dl = DeadlockManager(rm)
    reports = {A:RobotReport(cfg.zones["B"],True), B:RobotReport(cfg.zones["E"],True)}
    for robot, dest in [(A,"E"),(B,"B")]:
        rm.request(robot,dest,2,0)
    for ts in (0,2,22,22.1):
        rm.tick(ts,reports)
        dl.tick(ts)
    dl.arrived(dl.yielding[B].detour,23)
    yield from rm.events

    # 4O segment preemption event
    sm = SegmentManager(replace(cfg, traffic=replace(cfg.traffic, mode="SEGMENTS")))
    seg_reports = {A: RobotReport(cfg.zones["C"], True, departed=True), B: RobotReport(cfg.zones["E"], True)}
    sm.request(A, "A", 2, 0, mission_id="M-PRE-A")
    sm.tick(1, seg_reports)
    sm.request(B, "B", 3, 2, mission_id="M-PRE-B")
    sm.tick(3, seg_reports)
    yield from sm.events


def test_41_actual_fleet_event_titles_reasons_and_journal(tmp_path,monkeypatch):
    package = Path(__file__).resolve().parents[2] / "part3_fleet_manager"
    monkeypatch.syspath_prepend(str(package / "test"))
    from fleet_simulation import Simulation

    cfg = load_config(package / "config/fleet.yaml")
    cfg = replace(cfg, safety=replace(cfg.safety, use_hazard_topic=True), assignment=replace(cfg.assignment, mode="IDLE_BEST_BATTERY"), corridor=replace(cfg.corridor, mode="SHARED_YIELD"))
    events = list(traffic(cfg))
    with ExitStack() as cleanup:
        def simulator(mode="inspect"):
            # 4K turned the NONE retry off in fleet.yaml; this scenario still produces OBSTACLE_RETRY.
            sim = Simulation(replace(cfg,db_path=":memory:",obstacle=replace(cfg.obstacle,mode=mode,retry_if_clear=True)))
            cleanup.callback(sim.core.store.close)
            return sim

        sim = simulator()
        def collect(sim,ts):
            out = sim.tick(ts)
            events.extend(out.events)
            return out

        sim.pending.extend([("/ui/mission_req","{",None),("status","invalid",A)])
        sim.call("first","A")
        sim.battery(A,80)
        collect(sim,0)
        sim.event(A,"MISSION_ACCEPTED")
        sim.event(A,"ARRIVED")
        collect(sim,.1)
        sim.confirm(A,"first","MISMATCH")
        mismatch = collect(sim,.2).cards[0]
        sim.choose(mismatch,"recheck")
        collect(sim,.3)
        sim.confirm(A,"first")
        collect(sim,.4)
        sim.call("second","B")
        collect(sim,.5)
        sim.cmd("CANCEL_MISSION",mission_id=sim.ids["second"])
        collect(sim,.6)

        sim = simulator()
        sim.battery(A,80)
        sim.call("stall","C")
        collect(sim,0)
        collect(sim,1)
        sim.event(A,"MISSION_ACCEPTED")
        sim.frames[A]["pose"] = sim.pose((-1.55,-1.7))
        collect(sim,2)
        collect(sim,12)
        collect(sim,18)
        collect(sim,32)
        sim.frames[A]["percent"] = 20
        collect(sim,33)
        sim.event(A,"MISSION_FAILED",reason="Nav2: ABORTED")
        collect(sim,34)
        sim.pending.extend([("hazards",["BUMP"],A), ("/patient/bed_exit",True,None)])
        collect(sim,35)
        sim.cmd("RESUME_ALL")
        collect(sim,36)
        for stamp in (36, 36.25, 36.5, 36.75, 37):
            sim.core.enqueue("/patient/bed_exit", False, stamp)
        collect(sim,37)
        sim.cmd("RESUME_ROBOT",robot=A)
        collect(sim,37.1)
        loss = collect(sim,40).cards[0]
        sim.choose(loss,"bypass")
        collect(sim,40.1)
        for i in range(22):
            sim.pending.append(("/patient/bed_exit",False,None))
            collect(sim,41+i/10)
        sim.frames.clear()
        collect(sim,50)

        # 4J report mode: Nav2 gives up, PART 04 sends a photo, PART 06 sees nothing, then a box.
        sim = simulator("report")
        sim.battery(A,80)
        sim.call("box","C")
        collect(sim,0)
        collect(sim,1)
        for verdict, ts in ((False, 2), ("medical_box", 10)):
            sim.event(A,"MISSION_ACCEPTED")
            sim.frames[A]["pose"] = sim.pose((-1.55,-1.7))
            collect(sim,ts)
            sim.event(A,"MISSION_FAILED",reason="Nav2: ABORTED")
            sim.event(A,"OBSTACLE",lidar_m=0.42,image_topic="/robot3/hospital_nav/obstacle_image")
            sim.core.enqueue("obstacle_result",'{"status":"ok","obstacle_present":%s,"detections":%s}' % (
                "true" if verdict else "false",
                '[{"label":"medical_box","confidence":0.93}]' if verdict else "[]"),ts+.5,robot=A)
            collect(sim,ts+.5)
            collect(sim,ts+4)
            collect(sim,ts+4.1)

        sim = simulator()
        sim.battery(A,80)
        sim.call("unanswered","A")
        for ts in (0,3,6):
            collect(sim,ts)

        # 4K: PAUSE resent while STATE stays MOVING, the unconfirmed-stop card, then an arrival known
        # only from STATE because the STATUS ARRIVED was lost.
        sim = simulator()
        sim.battery(A,80)
        sim.call("stop","C")
        collect(sim,0)
        collect(sim,1)
        sim.event(A,"MISSION_ACCEPTED")
        collect(sim,2)
        sim.cmd("PAUSE_ROBOT",robot=A)
        for i in range(13):  # PAUSE at 3, again at 4.5, 6 and 7.5, the card at 9
            collect(sim,3+i/2)
        sim.event(A,"PAUSED")
        collect(sim,9.5)
        sim.cmd("RESUME_ROBOT",robot=A)
        sim.event(A,"RESUMED")
        collect(sim,10)
        sim.frames[A].update(state="COMPLETE",percent=100,pose=sim.pose("C"))
        collect(sim,11)

        sim = simulator()
        sim.cmd("GO_ZONE", robot=A, zone="F")
        collect(sim,0)
        sim.frames[B]["pose"] = sim.pose("A")
        collect(sim,1)
        sim.event(A,"MISSION_ACCEPTED")
        sim.frames[B]["pose"] = sim.pose((-.6,.9))
        collect(sim,2)
        sim.event(A,"PAUSED")
        sim.frames[B]["pose"] = sim.pose("C")
        collect(sim,3)

    assert {e["type"] for e in events} == set(EVENTS)
    path = tmp_path / "bad.db"
    store = Store(path)
    try:
        store.save([],events)
        client = create_app(Feed(),{"names":cfg.zone_names},None,None,db_path=path).test_client()
        rows = []
        while True:
            page = client.get("/api/events",query_string={"page":len(rows)//50+1}).get_json()
            rows.extend(page["items"])
            if not page["has_more"]:
                break
        codes = re.compile(r"robot_[ab]|\b(?:[A-Z]{2,}(?:_[A-Z]+)*|selected|resolved|timeout)\b")
        for original,row in zip(events,reversed(rows),strict=True):
            live = present_event(original,cfg.zone_names)
            reason = original["detail"]["reason"]
            assert reason and not codes.search(reason), (original["type"],reason)
            assert live["title"] == row["title"] and not codes.search(live["title"])
            assert live["detail"]["reason"] == row["detail"]["reason"] == reason
            if original["type"] == "CARD_OPEN":
                card = original["detail"]["card"]
                assert not codes.search(card["title"] + card["reason"])
    finally:
        store.close()


def test_yolo_sighting_has_its_own_history_title():
    reason = "주행 중 장애물 감지 · medical_box 0.93 · 약 0.85 m · 계속 주행"
    event = {"type": "OBSTACLE_REPORT", "robot": B, "detail": {"source": "yolo", "reason": reason}}
    row = present_event(event, {}, {B: "약품 운송 로봇"})
    assert row["title"] == "약품 운송 로봇 주행 중 장애물 감지"
    assert row["detail"]["reason"] == reason
