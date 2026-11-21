"""Stage 4O follow-up: quick send (GO_ZONE) with a Level, and the Level change of a mission (SET_LEVEL).

PLAN plans again with the new Level (nothing else changes); LEGACY refuses SET_LEVEL.
"""
import pytest

from kinematic_mock import KinematicSim
from part3_fleet_manager.core import schemas

A, B = "robot_a", "robot_b"


def _run(sim, seconds):
    acks = []
    end = sim.now + seconds
    while sim.now < end - 1e-9:
        acks.extend(sim.step(0.2).acks)
    return acks


def _ack(acks, ref):
    return next(a for a in acks if a["ref"] == ref)


@pytest.mark.parametrize("mode", ["PLAN", "LEGACY"])
def test_quick_send_carries_the_chosen_level_and_defaults_to_manual_level(cfg, mode):
    sim = KinematicSim(cfg, mode=mode)
    try:
        sim.cmd("GO_ZONE", robot=A, zone="C", level=3)
        sim.cmd("GO_ZONE", robot=B, zone="E")
        _run(sim, 1.0)
        assert sim.core.mission.robots[A].job.level == 3
        assert sim.core.mission.robots[B].job.level == cfg.mission.manual_level
        _run(sim, 60.0)
        moves = {}
        for _, robot, command in sim.all_commands:
            if command.startswith("MOVE"):
                moves.setdefault(robot, command)
        assert moves[A].split()[2] == "3"
        assert moves[B].split()[2] == str(cfg.mission.manual_level)
    finally:
        sim.close()


@pytest.mark.parametrize("fields,ok", [
    ({"cmd": "GO_ZONE", "robot": A, "zone": "C"}, True),
    ({"cmd": "GO_ZONE", "robot": A, "zone": "C", "level": 2}, True),
    ({"cmd": "GO_ZONE", "robot": A, "zone": "C", "level": 4}, False),
    ({"cmd": "GO_ZONE", "robot": A, "zone": "C", "level": "2"}, False),
    ({"cmd": "SET_LEVEL", "mission_id": "M-0001", "level": 3}, True),
    ({"cmd": "SET_LEVEL", "mission_id": "M-0001"}, False),
    ({"cmd": "SET_LEVEL", "mission_id": "M-0001", "level": 0}, False),
])
def test_level_fields_in_the_operator_command(fields, ok):
    text = schemas.dumps({"v": 1, "cmd_id": "c-1", "by": "간호사", **fields})
    if ok:
        assert schemas.parse_operator_cmd(text)["cmd"] == fields["cmd"]
    else:
        with pytest.raises(schemas.SchemaError, match="level"):
            schemas.parse_operator_cmd(text)


def test_plan_level_change_of_a_running_mission_only_plans_again(cfg):
    sim = KinematicSim(cfg, mode="PLAN")
    try:
        ref = sim.submit_med(("E", "B"), level=1)
        _run(sim, 3.0)
        mid = sim.ids[ref]
        leg = sim.core.resources.active.get(B) or sim.core.resources.pending.get(B)
        assert leg is not None and leg.level == 1
        cid = sim.cmd("SET_LEVEL", mission_id=mid, level=3)
        acks = _run(sim, 0.4)
        assert _ack(acks, cid)["ok"]
        assert sim.core.mission.robots[B].job.level == 3
        assert sim.core.resources.level_of(leg) == 3            # the same leg, weighed with Level 3
        change, = [e for e in sim.all_events if e["type"] == "LEVEL_CHANGE"]
        assert change["mission_id"] == mid and change["detail"]["previous"] == 1 and change["detail"]["level"] == 3
        assert change["detail"]["reason"] == "Level 1 → 3"
        assert any(e["type"] == "PLAN_UPDATE" and "응급도 변경" in e["detail"]["reason"] for e in sim.all_events)
        assert next(m for m in sim.core.snapshot(sim.now)["missions"] if m["mission_id"] == mid)["level"] == 3
        stored, = sim.core.store.db.execute("SELECT level FROM missions WHERE id=?", (mid,)).fetchone()
        assert stored == 3
        # The next leg of the mission is requested with the new Level.
        sim.run_until(sim.now + 60.0)
        assert all(leg.level == 3 for leg in (sim.core.resources.active.get(B), sim.core.resources.pending.get(B))
                   if leg is not None and leg.mission_id == mid)
    finally:
        sim.close()


def test_plan_level_change_of_a_waiting_mission_and_refusals(cfg):
    sim = KinematicSim(cfg, mode="PLAN")
    try:
        first = sim.submit_med(("E", "B"), level=1)
        second = sim.submit_call("D", level=1)                  # waits in robot_b's queue
        _run(sim, 1.0)
        waiting = sim.ids[second]
        assert any(job.id == waiting for job in sim.core.mission.robots[B].queue)
        ok = sim.cmd("SET_LEVEL", mission_id=waiting, level=2)
        same = sim.cmd("SET_LEVEL", mission_id=sim.ids[first], level=1)
        unknown = sim.cmd("SET_LEVEL", mission_id="M-9999", level=2)
        acks = _run(sim, 0.4)
        assert _ack(acks, ok)["ok"] and next(j for j in sim.core.mission.robots[B].queue if j.id == waiting).level == 2
        assert _ack(acks, same)["ok"]                            # nothing to change: no record
        assert [e["mission_id"] for e in sim.all_events if e["type"] == "LEVEL_CHANGE"] == [waiting]
        assert _ack(acks, unknown)["code"] == "E-409"
    finally:
        sim.close()


def test_legacy_refuses_the_level_change(cfg):
    sim = KinematicSim(cfg, mode="LEGACY")
    try:
        ref = sim.submit_med(("E", "B"), level=1)
        _run(sim, 2.0)
        cid = sim.cmd("SET_LEVEL", mission_id=sim.ids[ref], level=3)
        ack = _ack(_run(sim, 0.4), cid)
        assert not ack["ok"] and ack["code"] == "E-409" and "LEGACY" in ack["message"]
        assert sim.core.mission.robots[B].job.level == 1
        assert not any(e["type"] == "LEVEL_CHANGE" for e in sim.all_events)
    finally:
        sim.close()
