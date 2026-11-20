"""4M. Feed real receipt times through FleetCore, including requests between ticks."""
from collections import Counter
from dataclasses import replace

import pytest

from fleet_simulation import Simulation
from part3_fleet_manager.core.safety import EXIT_DENIED, MONITOR_DENIED

BED = "/patient/bed_exit"
A = "robot_a"


@pytest.fixture(params=["LEGACY", "SEGMENTS"])
def sim(cfg, tmp_path, request):
    sc = replace(cfg, db_path=str(tmp_path / f"4m-{request.param}.db"),
                 traffic=replace(cfg.traffic, mode=request.param))
    fleet = Simulation(sc)
    yield fleet
    fleet.core.store.close()


def test_flicker_for_three_minutes_stops_once_and_waits_for_manual_resume(sim):
    sim.patient()
    assert sim.tick(0).commands[0].command.startswith("MOVE ")
    sim.event(A, "MISSION_ACCEPTED")
    sim.tick(.05)
    events, wire = Counter(), []
    # 10 Hz receipt, 0.1 to 0.5 seconds between signal edges.
    pattern = [True, False, True, True, False, False, False, False, False, True]
    for i in range(1800):
        stamp = (i + 1) / 10
        sim.core.enqueue(BED, pattern[i % len(pattern)], stamp)
        out = sim.tick(stamp)
        events.update(e["type"] for e in out.events)
        wire.extend((c.robot, c.command) for c in out.commands)
        if i == 0:
            sim.event(A, "PAUSED")
    assert (events["BED_EXIT"], events["BED_EXIT_CLEAR"], events["PAUSE"]) == (1, 0, 1)
    assert wire == [(A, "PAUSE")]
    assert sim.core.snapshot(180)["bed_exit"]["since"] == .1

    for i in range(11):
        stamp = 180.1 + i / 10
        sim.core.enqueue(BED, False, stamp)
        if i == 9:
            assert sim.core.service("/robot/resume", stamp) == (False, EXIT_DENIED)
            sim.cmd("RESUME_ROBOT", robot=A)
        out = sim.tick(stamp)
        assert not out.commands
        assert sum(e["type"] == "BED_EXIT_CLEAR" for e in out.events) == (i == 10)
        if i == 9:
            assert not out.acks[0]["ok"] and EXIT_DENIED in out.acks[0]["message"]
    assert sim.state(A) == "SAFE_PAUSE"
    assert sim.core.snapshot(stamp)["bed_exit"]["value"] is False
    sim.cmd("RESUME_ROBOT", robot=A)
    assert [(c.robot, c.command) for c in sim.tick(181.2).commands] == [(A, "RESUME")]


def test_service_uses_every_buffered_sample_before_tick(sim):
    sim.core.enqueue(BED, True, 0)
    sim.core.enqueue(BED, False, .1)
    assert sim.core.service("/robot/resume", .1) == (False, EXIT_DENIED)
    for stamp in (.5, .9, 1.1):
        sim.core.enqueue(BED, False, stamp)
    assert sim.core.service("/robot/resume", 1.1)[0]
    # The service preview must not consume signals or write duplicate events.
    out = sim.tick(1.1)
    assert [(e["type"], e["ts"]) for e in out.events if e["type"].startswith("BED_EXIT")] == [
        ("BED_EXIT", 0), ("BED_EXIT_CLEAR", 1.1)]
    sim.core.enqueue(BED, True, 1.2)
    sim.core.enqueue(BED, False, 1.3)
    assert sim.core.service("/robot/resume", 1.3) == (False, EXIT_DENIED)
    assert sim.tick(1.3).state["bed_exit"]["value"] is True


def test_silence_does_not_count_towards_normal_or_release_monitor_stop(sim):
    sim.core.enqueue(BED, True, 0)
    sim.core.enqueue(BED, False, .1)
    sim.tick(.1)
    sim.tick(3.2)
    assert sim.core.safety.bed_exit is True
    for stamp in (4, 4.25, 4.5, 4.75, 5):
        sim.core.enqueue(BED, False, stamp)
        out = sim.tick(stamp)
        assert sum(e["type"] == "BED_EXIT_CLEAR" for e in out.events) == (stamp == 5)
    assert sim.core.service("/robot/resume", 5) == (False, MONITOR_DENIED)
    for stamp in (5.5, 6):
        sim.core.enqueue(BED, False, stamp)
    assert any(e["type"] == "BED_MONITOR_BACK" for e in sim.tick(6).events)
    assert sim.core.service("/robot/resume", 6)[0]
