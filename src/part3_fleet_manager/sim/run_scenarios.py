#!/usr/bin/env python3
"""Automated runner for PART 03 Gazebo simulation scenarios a-g (3 trials each).

Features:
  - Clean per-trial reset: cancel missions/cards via API -> Gazebo set_pose ->
    /robotN/initialpose -> clear local/global costmaps -> GO_ZONE to start zone
  - Strict pre-trial validation (IDLE, 0 open cards, exact zone/slot, <= 0.35 m on AMCL & GT);
    any violation or non-200/202 HTTP response marks INVALID and aborts the scenario.
  - Moving-only GT & AMCL min distance after command acceptance, wall + sim times, RTF.
  - Scenario g: robot3 parked at D1, robot2 A->F (3x) and F->A (3x), plus D->C deadlock check if BLOCKED opens.
"""
import argparse
import json
import math
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

from align_events import analyze_csv, format_report, load_csv, stop_distance as calc_stop_dist
from sim_ctl import capture_screenshot, post, snapshot

WS = Path(__file__).resolve().parents[3]
SIM_DIR = Path(__file__).resolve().parent
DATA_DIR = WS / "docs" / "part3" / "sim" / "data"
SCREEN_DIR = WS / "docs" / "part3" / "sim"
DB_PATH = Path("~/.bad/bad_sim.db").expanduser()

SLOT1 = {
    "A": (-2.383, 1.027),
    "B": (-2.500, -1.250),
    "C": (-2.758, -5.123),
    "D": (-1.000, 0.800),
    "E": (-0.400, -1.400),
    "F": (-1.400, -5.400),
}
SLOT2 = {
    "A": (-2.458, 0.282),
    "B": (-2.600, -2.800),
    "C": (-2.738, -4.573),
    "D": (-0.750, -0.150),
    "E": (-0.500, -2.950),
    "F": (-1.350, -4.700),
}
CROSSROAD = (-1.500, -2.180)
TOP_MOUTH = (-1.37, -0.55)
BOTTOM_MOUTH = (-1.72, -4.20)


class InvalidTrialError(RuntimeError):
    """Raised when pre-trial conditions or HTTP command acceptance fail."""


def get_state() -> dict:
    return snapshot()["state"]


def send_checked(path: str, body: dict) -> dict:
    code, resp = post(path, body)
    if code not in (200, 202):
        raise InvalidTrialError(f"HTTP {code} on {path} {body}: {resp}")
    return resp


def query_events(t0: float, t1: float) -> list[dict]:
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = list(con.execute(
        "SELECT id, ts, robot, mission_id, type, detail_json FROM events "
        "WHERE ts BETWEEN ? AND ? ORDER BY ts, id",
        (t0, t1),
    ))
    con.close()
    return [
        {
            "id": r["id"], "ts": r["ts"], "robot": r["robot"],
            "mission_id": r["mission_id"], "type": r["type"],
            "detail": json.loads(r["detail_json"] or "{}"),
        }
        for r in rows
    ]


def start_logger(csv_path: Path, label: str) -> subprocess.Popen:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    if csv_path.exists():
        csv_path.unlink()
    cmd = [
        sys.executable, str(SIM_DIR / "gt_distance_logger.py"),
        "--out", str(csv_path), "--label", label,
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 15.0:
        if csv_path.exists():
            lines = csv_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            if len(lines) >= 2:
                return proc
        time.sleep(0.2)
    raise InvalidTrialError("gt_distance_logger did not write initial GT row within 15s")


def stop_logger(proc: subprocess.Popen) -> str:
    proc.send_signal(signal.SIGINT)
    try:
        out, _ = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
    return out


def wait_started(robots, timeout_s: float = 8.0) -> bool:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        st = get_state()
        if any(st["robots"][r]["state"] != "IDLE" for r in robots):
            return True
        time.sleep(0.15)
    return False


def wait_both_idle(timeout_s: float = 150.0, stop_on_card: bool = False, poll_s: float = 0.5, on_tick=None):
    t0 = time.monotonic()
    cards_seen = []
    while time.monotonic() - t0 < timeout_s:
        snap = snapshot()
        st = snap["state"]
        ra, rb = st["robots"]["robot_a"], st["robots"]["robot_b"]
        if on_tick:
            on_tick(st, time.monotonic() - t0)
        if snap.get("cards"):
            for c in snap["cards"]:
                if c["card_id"] not in [x["card_id"] for x in cards_seen]:
                    cards_seen.append(c)
                    print(f"  [CARD] {c['kind']} robots={c['robots']} reason={c.get('reason')}", flush=True)
            if stop_on_card:
                return False, st, cards_seen
        if ra["state"] == "IDLE" and rb["state"] == "IDLE" and not st["open_cards"]:
            return True, st, cards_seen
        time.sleep(poll_s)
    return False, get_state(), cards_seen


def _run_sim_reset(r3_pose, r2_pose) -> dict:
    cmd = [
        sys.executable, str(SIM_DIR / "sim_reset.py"),
        "--r3", f"{r3_pose[0]:.4f}", f"{r3_pose[1]:.4f}", f"{r3_pose[2]:.4f}",
        "--r2", f"{r2_pose[0]:.4f}", f"{r2_pose[1]:.4f}", f"{r2_pose[2]:.4f}",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    print(f"  [sim_reset] rc={res.returncode} {res.stdout.strip()}", flush=True)
    if res.returncode != 0:
        raise InvalidTrialError(f"sim_reset failed: {res.stdout} {res.stderr}")
    return {"rc": res.returncode, "out": res.stdout.strip()}


def _read_gt() -> dict:
    code = (
        "import json; from sim_reset import read_gt_poses; "
        "print(json.dumps(read_gt_poses()))"
    )
    res = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(SIM_DIR), capture_output=True, text=True, timeout=15, check=True,
    )
    return json.loads(res.stdout.strip())


def clear_fleet_state():
    """Cancel active missions (including GO_ZONE on robots), resume pauses, and close cards via API."""
    for _ in range(4):
        snap = snapshot()
        st = snap["state"]
        # 1. Cancel any active/queued/fault missions (both st["missions"] AND robot["mission_id"] for GO_ZONE)
        mids = set()
        for m in st.get("missions", []):
            if m.get("status") not in ("DONE", "CANCELED") and m.get("mission_id"):
                mids.add(m["mission_id"])
        for rid in ("robot_a", "robot_b"):
            mid = st["robots"][rid].get("mission_id")
            if mid:
                mids.add(mid)
        for mid in mids:
            post("/api/command", {"cmd": "CANCEL_MISSION", "mission_id": mid, "operator": "sim_reset"})
        if mids:
            time.sleep(0.6)
            snap = snapshot()
            st = snap["state"]
        # 2. Resume any manual/safety pause so robots aren't latched in SAFE_PAUSE
        if st["robots"]["robot_a"]["pause_causes"] or st["robots"]["robot_b"]["pause_causes"]:
            post("/api/command", {"cmd": "RESUME_ALL", "operator": "sim_reset"})
        # 3. Close any remaining open cards
        for card in snap.get("cards", []):
            opts = [o["id"] for o in card.get("options", [])]
            choice = next((c for c in ("cancel", "hold", "peer_first", "retry", "bypass") if c in opts), opts[0] if opts else None)
            if choice:
                post("/api/decision", {"card_id": card["card_id"], "choice": choice, "operator": "sim_reset"})
        # 4. Clear any UNKNOWN resource if present
        for rname, rinfo in st.get("resources", {}).items():
            if rinfo.get("state") == "UNKNOWN":
                post("/api/command", {"cmd": "RESOURCE_CLEAR_CONFIRM", "resource": rname, "operator": "sim_reset"})
        time.sleep(1.0)
        snap = snapshot()
        st = snap["state"]
        ra, rb = st["robots"]["robot_a"], st["robots"]["robot_b"]
        done_p4 = ("IDLE", "COMPLETE", "CANCELED", "FAILED")
        if (ra["state"] == "IDLE" and rb["state"] == "IDLE"
                and ra.get("part4_state") in done_p4 and rb.get("part4_state") in done_p4
                and not st["open_cards"]):
            return
    # Wait up to 25s for Nav2 cancel/abort to settle completely before any teleport
    t0 = time.monotonic()
    while time.monotonic() - t0 < 25.0:
        st = get_state()
        ra, rb = st["robots"]["robot_a"], st["robots"]["robot_b"]
        done_p4 = ("IDLE", "COMPLETE", "CANCELED", "FAILED")
        if (ra["state"] == "IDLE" and rb["state"] == "IDLE"
                and ra.get("part4_state") in done_p4 and rb.get("part4_state") in done_p4
                and not st["open_cards"]):
            return
        time.sleep(0.5)


def reset_for_trial(a_zone: str, b_zone: str, a_slot: int = 1, b_slot: int = 1):
    """Full clean trial reset + strict precondition verification (raises InvalidTrialError on any mismatch)."""
    print(f"  [reset] preparing A->{a_zone}{a_slot}, B->{b_zone}{b_slot}", flush=True)
    clear_fleet_state()

    # Choose teleport poses: for B/E, place at CROSSROAD facing the room so GO_ZONE cleanly enters & sets _in_room.
    def tp_pose(zone, slot):
        if zone == "B":
            return (CROSSROAD[0], CROSSROAD[1], math.pi)
        if zone == "E":
            return (CROSSROAD[0], CROSSROAD[1], 0.0)
        xy = SLOT1[zone] if slot == 1 else SLOT2[zone]
        return (xy[0], xy[1], 0.0)

    r3_tp = tp_pose(a_zone, a_slot)
    r2_tp = tp_pose(b_zone, b_slot)
    _run_sim_reset(r3_tp, r2_tp)
    # Wait for PART 04 STATE (1 Hz) to propagate updated AMCL poses to fleet_manager
    time.sleep(2.5)
    # Clear any residual cards/holds that could only clear once poses separated
    clear_fleet_state()

    # Send GO_ZONE to each robot's own start zone so fleet_manager records zone & slot cleanly.
    # If one targets B/E (needs crossroad), run it first, then the other.
    order = [("robot_a", a_zone), ("robot_b", b_zone)]
    if b_zone in ("B", "E") and a_zone not in ("B", "E"):
        order = [("robot_b", b_zone), ("robot_a", a_zone)]

    for rid, z in order:
        send_checked("/api/command", {"cmd": "GO_ZONE", "robot": rid, "zone": z, "operator": "sim_reset"})
        wait_started([rid], timeout_s=5.0)
        ok, st, cards = wait_both_idle(timeout_s=75.0, stop_on_card=True, poll_s=0.5)
        if not ok:
            raise InvalidTrialError(f"reset GO_ZONE {rid}->{z} failed (cards={[c['kind'] for c in cards]})")

    time.sleep(1.0)
    # Verify ALL strict start conditions!
    snap = snapshot()
    st = snap["state"]
    gt_info = _read_gt()
    reasons = []
    if st["open_cards"]:
        reasons.append(f"open_cards={len(st['open_cards'])}")
    for rid, ns, z, sl in (("robot_a", "robot3", a_zone, a_slot), ("robot_b", "robot2", b_zone, b_slot)):
        r = st["robots"][rid]
        target_xy = SLOT1[z] if sl == 1 else SLOT2[z]
        if r["state"] != "IDLE":
            reasons.append(f"{rid}.state={r['state']}!=IDLE")
        if r.get("part4_state") not in ("IDLE", "COMPLETE", "CANCELED"):
            reasons.append(f"{rid}.part4_state={r.get('part4_state')}")
        if r.get("pause_causes"):
            reasons.append(f"{rid}.pause_causes={r.get('pause_causes')}")
        if r.get("zone") != z:
            reasons.append(f"{rid}.zone={r.get('zone')}!={z}")
        if r.get("slot") != sl:
            reasons.append(f"{rid}.slot={r.get('slot')}!={sl}")
        p = r.get("pose")
        if not p:
            reasons.append(f"{rid}.amcl_pose=None")
        else:
            d_amcl = math.hypot(p["x"] - target_xy[0], p["y"] - target_xy[1])
            if d_amcl > 0.35:
                reasons.append(f"{rid}.amcl_err={d_amcl:.3f}m>0.35m")
        gp = gt_info.get("gt", {}).get(ns)
        if not gp:
            reasons.append(f"{rid}.gt_pose=None")
        else:
            d_gt = math.hypot(gp[0] - target_xy[0], gp[1] - target_xy[1])
            if d_gt > 0.35:
                reasons.append(f"{rid}.gt_err={d_gt:.3f}m>0.35m")
    if reasons:
        raise InvalidTrialError("Start condition failed: " + "; ".join(reasons))

    ra, rb = st["robots"]["robot_a"], st["robots"]["robot_b"]
    print(
        f"  [verified OK] A={ra['zone']}{ra['slot']} amcl=({ra['pose']['x']:.2f},{ra['pose']['y']:.2f}) "
        f"gt=({gt_info['gt']['robot3'][0]:.2f},{gt_info['gt']['robot3'][1]:.2f}) | "
        f"B={rb['zone']}{rb['slot']} amcl=({rb['pose']['x']:.2f},{rb['pose']['y']:.2f}) "
        f"gt=({gt_info['gt']['robot2'][0]:.2f},{gt_info['gt']['robot2'][1]:.2f})",
        flush=True,
    )


def _corridor_proj(xy) -> float:
    dx, dy = BOTTOM_MOUTH[0] - TOP_MOUTH[0], BOTTOM_MOUTH[1] - TOP_MOUTH[1]
    return ((xy[0] - TOP_MOUTH[0]) * dx + (xy[1] - TOP_MOUTH[1]) * dy) / math.hypot(dx, dy)


def _extract_metrics(csv_path: Path, md_path: Path, since_ts: float, until_ts: float | None = None) -> dict:
    an = analyze_csv(csv_path, DB_PATH, since_ts=since_ts, until_ts=until_ts)
    if "error" in an:
        return {"error": an["error"]}
    md_path.write_text(format_report(csv_path, an), encoding="utf-8")
    return {
        "samples": an["samples"],
        "moving_samples": an["moving_samples"],
        "wall_duration_s": an["wall_duration_s"],
        "sim_duration_s": an["sim_duration_s"],
        "avg_rtf": an["avg_rtf"],
        "min_gt_moving": an["min_gt_moving"],
        "min_amcl_moving": an["min_amcl_moving"],
        "min_gt_all": an["min_gt_all"],
        "gt_ge_045": an["gt_ge_045"],
    }


# ----------------------------------------------------------------- Scenarios

def run_scenario_a(trials: int):
    results = []
    for i in range(1, trials + 1):
        first_robot = "robot_a" if i % 2 == 1 else "robot_b"
        second_robot = "robot_b" if first_robot == "robot_a" else "robot_a"
        print(f"\n=== Scenario A · Trial {i}/{trials} (first={first_robot}: A D->C, B F->A) ===", flush=True)
        try:
            reset_for_trial("D", "F")
            csv_path = DATA_DIR / f"a_trial{i}.csv"
            md_path = DATA_DIR / f"a_trial{i}_aligned.md"
            logger = start_logger(csv_path, f"a_{i}")
            t_cmd = time.time()

            if first_robot == "robot_a":
                send_checked("/api/command", {"cmd": "GO_ZONE", "robot": "robot_a", "zone": "C", "operator": "sim"})
                time.sleep(0.15)
                send_checked("/api/command", {"cmd": "GO_ZONE", "robot": "robot_b", "zone": "A", "operator": "sim"})
            else:
                send_checked("/api/command", {"cmd": "GO_ZONE", "robot": "robot_b", "zone": "A", "operator": "sim"})
                time.sleep(0.15)
                send_checked("/api/command", {"cmd": "GO_ZONE", "robot": "robot_a", "zone": "C", "operator": "sim"})
            wait_started(["robot_a", "robot_b"])

            max_corridor_holders = 0
            hold_obs = []

            def on_tick(st, dt):
                nonlocal max_corridor_holders
                holders = st["resources"]["CORRIDOR"]["holders"]
                max_corridor_holders = max(max_corridor_holders, len(holders))
                r2 = st["robots"][second_robot]
                if r2["state"] == "HOLD" and r2.get("wait_reason"):
                    item = (r2.get("region"), r2.get("wait_reason"))
                    if item not in hold_obs:
                        hold_obs.append(item)
                        print(f"  [{dt:5.1f}s] {second_robot} HOLD region={item[0]} reason={item[1]}", flush=True)

            ok, st, cards = wait_both_idle(timeout_s=180.0, stop_on_card=True, poll_s=0.5, on_tick=on_tick)
            time.sleep(0.4)
            stop_logger(logger)
            t1 = time.time()

            evs = query_events(t_cmd - 0.2, t1)
            accept_ts = next((e["ts"] for e in evs if e["type"] in ("LEG_START", "RESOURCE_GRANT")), t_cmd)
            metrics = _extract_metrics(csv_path, md_path, since_ts=accept_ts)

            dir_holds = [
                e for e in evs
                if e["type"] == "RESOURCE_HOLD" and e["robot"] == second_robot
                and e["detail"].get("rule") == "DIRECTION"
            ]
            direction_wait_ok = bool(dir_holds) or any("반대 방향" in rsn for _, rsn in hold_obs)
            waited_outside = all(reg != "CORRIDOR" for reg, _ in hold_obs) if hold_obs else False
            arrived_ok = (st["robots"]["robot_a"].get("zone") == "C" and st["robots"]["robot_b"].get("zone") == "A")
            prox_evs = [e for e in evs if e["type"] == "PROXIMITY_STOP"]
            passed = ok and arrived_ok and max_corridor_holders <= 1 and direction_wait_ok and waited_outside

            summary = {
                "scenario": "a", "trial": i, "status": "PASS" if passed else "FAIL",
                "first_robot": first_robot, "second_robot": second_robot,
                "arrived_ok": arrived_ok, "max_corridor_holders": max_corridor_holders,
                "direction_wait_ok": direction_wait_ok, "waited_outside_corridor": waited_outside,
                "hold_observations": hold_obs, "direction_hold_events": len(dir_holds),
                "proximity_stops": len(prox_evs), "cards": [c["kind"] for c in cards],
                **metrics,
            }
            mg = metrics.get("min_gt_moving") or {}
            print(
                f"  RESULT A-{i}: {summary['status']} first={first_robot} arrived={arrived_ok} "
                f"max_holders={max_corridor_holders} dir_wait={direction_wait_ok} "
                f"min_GT={mg.get('dist_m')}m (>=0.45:{metrics.get('gt_ge_045')}) "
                f"wall={metrics.get('wall_duration_s')}s sim={metrics.get('sim_duration_s')}s RTF={metrics.get('avg_rtf')}",
                flush=True,
            )
            results.append(summary)
        except InvalidTrialError as exc:
            print(f"  RESULT A-{i}: INVALID — {exc}", flush=True)
            results.append({"scenario": "a", "trial": i, "status": "INVALID", "error": str(exc)})
            break
    return results


def run_scenario_b(trials: int):
    results = []
    for i in range(1, trials + 1):
        print(f"\n=== Scenario B · Trial {i}/{trials} (Same-direction following: A D->C, B A->F) ===", flush=True)
        try:
            reset_for_trial("D", "A")
            csv_path = DATA_DIR / f"b_trial{i}.csv"
            md_path = DATA_DIR / f"b_trial{i}_aligned.md"
            logger = start_logger(csv_path, f"b_{i}")
            t_cmd = time.time()

            send_checked("/api/command", {"cmd": "GO_ZONE", "robot": "robot_a", "zone": "C", "operator": "sim"})
            time.sleep(0.15)
            send_checked("/api/command", {"cmd": "GO_ZONE", "robot": "robot_b", "zone": "F", "operator": "sim"})
            wait_started(["robot_a", "robot_b"])

            follow_log = []

            def on_tick(st, dt):
                fs = st["resources"]["CORRIDOR"].get("follow_stop")
                if fs and (not follow_log or follow_log[-1][1] != fs):
                    follow_log.append((round(dt, 1), fs))
                    print(f"  [{dt:5.1f}s] follow_stop={fs}", flush=True)

            ok, st, cards = wait_both_idle(timeout_s=180.0, stop_on_card=True, poll_s=0.5, on_tick=on_tick)
            time.sleep(0.4)
            stop_logger(logger)
            t1 = time.time()

            evs = query_events(t_cmd - 0.2, t1)
            accept_ts = next((e["ts"] for e in evs if e["type"] in ("LEG_START", "RESOURCE_GRANT")), t_cmd)
            metrics = _extract_metrics(csv_path, md_path, since_ts=accept_ts)

            # Compute exact GT/AMCL gap at CORRIDOR_FOLLOW_STOP and CORRIDOR_FOLLOW_CLEAR
            rows = load_csv(csv_path)
            ts_list = [r["wall_ts"] for r in rows]
            from align_events import nearest_row
            fstops = [e for e in evs if e["type"] == "CORRIDOR_FOLLOW_STOP"]
            fclears = [e for e in evs if e["type"] == "CORRIDOR_FOLLOW_CLEAR"]
            stop_gaps = []
            for e in fstops:
                nr = nearest_row(rows, ts_list, e["ts"])
                gt_axial = abs(_corridor_proj((nr["r3_x"], nr["r3_y"])) - _corridor_proj((nr["r2_x"], nr["r2_y"]))) if nr else None
                stop_gaps.append({
                    "rule": e["detail"].get("rule"),
                    "db_gap_m": round(e["detail"]["gap_m"], 3) if e["detail"].get("gap_m") is not None else None,
                    "gt_dist_m": round(nr["dist_m"], 3) if nr else None,
                    "gt_axial_gap_m": round(gt_axial, 3) if gt_axial is not None else None,
                    "sim_ts": round(nr["sim_ts"], 2) if nr and nr["sim_ts"] is not None else None,
                })
            clear_gaps = []
            for e in fclears:
                nr = nearest_row(rows, ts_list, e["ts"])
                gt_axial = abs(_corridor_proj((nr["r3_x"], nr["r3_y"])) - _corridor_proj((nr["r2_x"], nr["r2_y"]))) if nr else None
                clear_gaps.append({
                    "reason": e["detail"].get("reason"),
                    "gt_dist_m": round(nr["dist_m"], 3) if nr else None,
                    "gt_axial_gap_m": round(gt_axial, 3) if gt_axial is not None else None,
                    "amcl_dist_m": round(nr["amcl_dist_m"], 3) if nr and nr["amcl_dist_m"] is not None else None,
                    "sim_ts": round(nr["sim_ts"], 2) if nr and nr["sim_ts"] is not None else None,
                })

            arrived_ok = (st["robots"]["robot_a"].get("zone") == "C" and st["robots"]["robot_b"].get("zone") == "F")
            prox_evs = [e for e in evs if e["type"] == "PROXIMITY_STOP"]
            passed = ok and arrived_ok and bool(fstops) and bool(fclears)

            summary = {
                "scenario": "b", "trial": i, "status": "PASS" if passed else "FAIL",
                "arrived_ok": arrived_ok, "stop_gaps": stop_gaps, "clear_gaps": clear_gaps,
                "proximity_stops": len(prox_evs), "cards": [c["kind"] for c in cards],
                **metrics,
            }
            mg = metrics.get("min_gt_moving") or {}
            print(
                f"  RESULT B-{i}: {summary['status']} arrived={arrived_ok} stops={stop_gaps} clears={clear_gaps} "
                f"min_GT={mg.get('dist_m')}m wall={metrics.get('wall_duration_s')}s sim={metrics.get('sim_duration_s')}s",
                flush=True,
            )
            results.append(summary)
        except InvalidTrialError as exc:
            print(f"  RESULT B-{i}: INVALID — {exc}", flush=True)
            results.append({"scenario": "b", "trial": i, "status": "INVALID", "error": str(exc)})
            break
    return results


def run_scenario_c(trials: int):
    results = []
    for i in range(1, trials + 1):
        print(f"\n=== Scenario C · Trial {i}/{trials} (B at F1, A->F(F2), then B exits F1->D) ===", flush=True)
        try:
            reset_for_trial("D", "F")
            csv_path = DATA_DIR / f"c_trial{i}.csv"
            md_path = DATA_DIR / f"c_trial{i}_aligned.md"
            logger = start_logger(csv_path, f"c_{i}")
            t_p1 = time.time()

            # Phase 1: A -> F (expects F2 without waiting)
            send_checked("/api/command", {"cmd": "GO_ZONE", "robot": "robot_a", "zone": "F", "operator": "sim"})
            wait_started(["robot_a"])
            ok1, st1, cards1 = wait_both_idle(timeout_s=120.0, stop_on_card=True, poll_s=0.5)
            a_zone, a_slot = st1["robots"]["robot_a"].get("zone"), st1["robots"]["robot_a"].get("slot")
            print(f"  Phase 1 done: ok={ok1} A={a_zone}{a_slot} pose={st1['robots']['robot_a'].get('pose')}", flush=True)

            if i == 1:
                capture_screenshot(SCREEN_DIR / "screen-c-f2.png")

            # Phase 2: B leaves F1 -> D while A sits at F2
            t_p2 = time.time()
            send_checked("/api/command", {"cmd": "GO_ZONE", "robot": "robot_b", "zone": "D", "operator": "sim"})
            wait_started(["robot_b"])
            ok2, st2, cards2 = wait_both_idle(timeout_s=120.0, stop_on_card=True, poll_s=0.5)
            if not ok2 and cards2 and i == 1:
                capture_screenshot(SCREEN_DIR / "screen-c-proximity.png")
            time.sleep(0.4)
            stop_logger(logger)
            t1 = time.time()

            evs = query_events(t_p1 - 0.2, t1)
            accept_p1 = next((e["ts"] for e in evs if e["type"] == "LEG_START" and e["robot"] == "robot_a"), t_p1)
            accept_p2 = next((e["ts"] for e in evs if e["ts"] >= t_p2 - 0.1 and e["type"] == "LEG_START" and e["robot"] == "robot_b"), t_p2)
            metrics_all = _extract_metrics(csv_path, md_path, since_ts=accept_p1)
            p2_an = analyze_csv(csv_path, DB_PATH, since_ts=accept_p2)

            zone_holds = [e for e in evs if e["type"] == "ZONE_OCCUPIED_HOLD"]
            prox_stops = [e for e in evs if e["type"] == "PROXIMITY_STOP"]
            p1_ok = ok1 and a_zone == "F" and a_slot == 2 and not zone_holds
            p2_ok = ok2 and st2["robots"]["robot_b"].get("zone") == "D"
            passed = p1_ok and p2_ok

            summary = {
                "scenario": "c", "trial": i, "status": "PASS" if passed else "FAIL",
                "phase1_f2_ok": p1_ok, "phase2_exit_ok": p2_ok,
                "a_slot": a_slot, "zone_holds": len(zone_holds),
                "proximity_stops": [e["detail"] for e in prox_stops],
                "cards": [c["kind"] for c in (cards1 + cards2)],
                "p2_min_gt_moving": p2_an.get("min_gt_moving"),
                "p2_min_amcl_moving": p2_an.get("min_amcl_moving"),
                "p2_gt_ge_045": p2_an.get("gt_ge_045"),
                **metrics_all,
            }
            p2g = (p2_an.get("min_gt_moving") or {}).get("dist_m")
            print(
                f"  RESULT C-{i}: {summary['status']} p1_f2={p1_ok} p2_exit={p2_ok} "
                f"p2_min_GT={p2g}m (>=0.45:{p2_an.get('gt_ge_045')}) prox={len(prox_stops)} cards={summary['cards']}",
                flush=True,
            )
            results.append(summary)
        except InvalidTrialError as exc:
            print(f"  RESULT C-{i}: INVALID — {exc}", flush=True)
            results.append({"scenario": "c", "trial": i, "status": "INVALID", "error": str(exc)})
            break
    return results


def run_scenario_d(trials: int):
    results = []
    for i in range(1, trials + 1):
        print(f"\n=== Scenario D · Trial {i}/{trials} (A at B1, B->B via crossroad->B2, then simultaneous exit) ===", flush=True)
        try:
            reset_for_trial("B", "F")
            csv_path = DATA_DIR / f"d_trial{i}.csv"
            md_path = DATA_DIR / f"d_trial{i}_aligned.md"
            logger = start_logger(csv_path, f"d_{i}")
            t_p1 = time.time()

            # Phase 1: B -> B (must route via crossroad -> B2)
            send_checked("/api/command", {"cmd": "GO_ZONE", "robot": "robot_b", "zone": "B", "operator": "sim"})
            wait_started(["robot_b"])
            saw_via = False
            max_room_b_holders = 0

            def on_tick_d1(st, dt):
                nonlocal saw_via, max_room_b_holders
                max_room_b_holders = max(max_room_b_holders, len(st["resources"]["ROOM_B"]["holders"]))
                if st["robots"]["robot_b"].get("via"):
                    saw_via = True

            ok1, st1, cards1 = wait_both_idle(timeout_s=120.0, stop_on_card=True, poll_s=0.5, on_tick=on_tick_d1)
            b_zone, b_slot = st1["robots"]["robot_b"].get("zone"), st1["robots"]["robot_b"].get("slot")
            print(f"  Phase 1 done: ok={ok1} B={b_zone}{b_slot} saw_via={saw_via} max_room_b={max_room_b_holders}", flush=True)

            # Phase 2: Both exit B simultaneously (A->D, B->F) -> check door & crossroad never shared
            t_p2 = time.time()
            send_checked("/api/command", {"cmd": "GO_ZONE", "robot": "robot_a", "zone": "D", "operator": "sim"})
            time.sleep(0.15)
            send_checked("/api/command", {"cmd": "GO_ZONE", "robot": "robot_b", "zone": "F", "operator": "sim"})
            wait_started(["robot_a", "robot_b"])

            both_near_crossroad = False
            p2_wait_reasons = []

            def on_tick_d2(st, dt):
                nonlocal max_room_b_holders, both_near_crossroad
                max_room_b_holders = max(max_room_b_holders, len(st["resources"]["ROOM_B"]["holders"]))
                pa, pb = st["robots"]["robot_a"].get("pose"), st["robots"]["robot_b"].get("pose")
                if pa and pb:
                    da = math.hypot(pa["x"] - CROSSROAD[0], pa["y"] - CROSSROAD[1])
                    db = math.hypot(pb["x"] - CROSSROAD[0], pb["y"] - CROSSROAD[1])
                    if da <= 0.6 and db <= 0.6:
                        both_near_crossroad = True
                for rid in ("robot_a", "robot_b"):
                    r = st["robots"][rid]
                    if r["state"] == "HOLD" and r.get("wait_reason"):
                        item = (rid, r.get("wait_reason"))
                        if item not in p2_wait_reasons:
                            p2_wait_reasons.append(item)
                            print(f"  [{dt:5.1f}s] {rid} HOLD: {r.get('wait_reason')}", flush=True)

            ok2, st2, cards2 = wait_both_idle(timeout_s=240.0, stop_on_card=True, poll_s=0.5, on_tick=on_tick_d2)
            time.sleep(0.4)
            stop_logger(logger)
            t1 = time.time()

            evs = query_events(t_p1 - 0.2, t1)
            accept_p1 = next((e["ts"] for e in evs if e["type"] == "LEG_START" and e["robot"] == "robot_b"), t_p1)
            metrics = _extract_metrics(csv_path, md_path, since_ts=accept_p1)

            # Also check GT crossroad overlap directly on CSV
            rows = load_csv(csv_path)
            gt_both_crossroad = any(
                math.hypot(r["r3_x"] - CROSSROAD[0], r["r3_y"] - CROSSROAD[1]) <= 0.6
                and math.hypot(r["r2_x"] - CROSSROAD[0], r["r2_y"] - CROSSROAD[1]) <= 0.6
                for r in rows
            )

            p2_holds = [e for e in evs if e["ts"] >= t_p2 - 0.1 and e["type"] == "RESOURCE_HOLD"]
            door_rules = [e["detail"].get("rule") for e in p2_holds if e["detail"].get("rule") in ("CAPACITY", "CROSSROAD", "DIRECTION", "NO_PREEMPT", "REQUEST_TIME")]
            door_hold_ok = any("B 구역 출입" in rsn or "교차로" in rsn for _, rsn in p2_wait_reasons)
            arrived_p2 = (st2["robots"]["robot_a"].get("zone") == "D" and st2["robots"]["robot_b"].get("zone") == "F")
            prox_stops = [e for e in evs if e["type"] == "PROXIMITY_STOP"]
            passed = (
                ok1 and b_zone == "B" and b_slot == 2
                and ok2 and arrived_p2
                and max_room_b_holders <= 1 and not both_near_crossroad and not gt_both_crossroad
                and door_hold_ok
            )
            summary = {
                "scenario": "d", "trial": i, "status": "PASS" if passed else "FAIL",
                "phase1_b2_ok": ok1 and b_zone == "B" and b_slot == 2,
                "phase2_exit_ok": ok2 and arrived_p2,
                "saw_via": saw_via, "max_room_b_holders": max_room_b_holders,
                "both_near_crossroad_amcl": both_near_crossroad,
                "both_near_crossroad_gt": gt_both_crossroad,
                "door_hold_rules": door_rules, "p2_wait_reasons": p2_wait_reasons,
                "proximity_stops": len(prox_stops), "cards": [c["kind"] for c in (cards1 + cards2)],
                **metrics,
            }
            mg = (metrics.get("min_gt_moving") or {}).get("dist_m")
            print(
                f"  RESULT D-{i}: {summary['status']} p1_b2={summary['phase1_b2_ok']} p2_exit={summary['phase2_exit_ok']} "
                f"max_room_b={max_room_b_holders} gt_xroad_overlap={gt_both_crossroad} rules={door_rules} min_GT={mg}m",
                flush=True,
            )
            results.append(summary)
        except InvalidTrialError as exc:
            print(f"  RESULT D-{i}: INVALID — {exc}", flush=True)
            results.append({"scenario": "d", "trial": i, "status": "INVALID", "error": str(exc)})
            break
    return results


def run_scenario_e(trials: int):
    results = []
    for i in range(1, trials + 1):
        print(f"\n=== Scenario E · Trial {i}/{trials} (PAUSE_ROBOT in corridor: stop distance & confirmation timing) ===", flush=True)
        try:
            reset_for_trial("D", "F")
            csv_path = DATA_DIR / f"e_trial{i}.csv"
            md_path = DATA_DIR / f"e_trial{i}_aligned.md"
            logger = start_logger(csv_path, f"e_{i}")
            t0 = time.time()

            send_checked("/api/command", {"cmd": "GO_ZONE", "robot": "robot_a", "zone": "C", "operator": "sim"})
            wait_started(["robot_a"])

            entered = False
            tw = time.monotonic()
            while time.monotonic() - tw < 90.0:
                st = get_state()
                ra = st["robots"]["robot_a"]
                p = ra.get("pose")
                if ra["state"] == "MOVING" and ra.get("part4_state") == "MOVING" and p and -2.6 < p["y"] < -1.1:
                    entered = True
                    break
                time.sleep(0.15)
            if not entered:
                raise InvalidTrialError("robot_a did not reach corridor midpoint (-2.6 < y < -1.1) before PAUSE")

            cmd_ts = time.time()
            send_checked("/api/command", {"cmd": "PAUSE_ROBOT", "robot": "robot_a", "operator": "sim"})
            print(f"  Sent PAUSE_ROBOT at {cmd_ts:.3f}", flush=True)

            t_pause_cause = None
            t_stopping_pause = None
            t_paused_confirm = None
            tp = time.monotonic()
            while time.monotonic() - tp < 10.0:
                st = get_state()
                now_t = time.time()
                ra = st["robots"]["robot_a"]
                p4 = ra.get("part4_state")
                if t_pause_cause is None and "MANUAL" in (ra.get("pause_causes") or []):
                    t_pause_cause = now_t
                if t_stopping_pause is None and p4 == "STOPPING_PAUSE":
                    t_stopping_pause = now_t
                if t_paused_confirm is None and p4 == "PAUSED":
                    t_paused_confirm = now_t
                    print(f"  PAUSED confirmed at +{t_paused_confirm - cmd_ts:.3f}s wall", flush=True)
                if t_paused_confirm is not None and now_t - t_paused_confirm >= 1.5:
                    break
                time.sleep(0.1)

            if i == 1:
                capture_screenshot(SCREEN_DIR / "screen-e-stop.png")

            send_checked("/api/command", {"cmd": "RESUME_ROBOT", "robot": "robot_a", "operator": "sim"})
            time.sleep(0.5)
            ok_end, st_end, cards = wait_both_idle(timeout_s=120.0, stop_on_card=True, poll_s=0.5)
            time.sleep(0.4)
            stop_logger(logger)
            t1 = time.time()

            evs = query_events(t0 - 0.2, t1)
            accept_ts = next((e["ts"] for e in evs if e["type"] == "LEG_START" and e["robot"] == "robot_a"), t0)
            metrics = _extract_metrics(csv_path, md_path, since_ts=accept_ts)

            rows = load_csv(csv_path)
            ts_list = [r["wall_ts"] for r in rows]
            from align_events import nearest_row
            nr_cmd = nearest_row(rows, ts_list, cmd_ts)
            nr_conf = nearest_row(rows, ts_list, t_paused_confirm) if t_paused_confirm else None
            confirm_sim_dt = (
                round(nr_conf["sim_ts"] - nr_cmd["sim_ts"], 3)
                if (nr_cmd and nr_conf and nr_cmd["sim_ts"] is not None and nr_conf["sim_ts"] is not None) else None
            )
            sd_cmd = calc_stop_dist(rows, ts_list, cmd_ts, "robot_a")
            pause_evs = [e for e in evs if e["type"] == "PAUSE"]
            sd_ev = calc_stop_dist(rows, ts_list, pause_evs[0]["ts"], "robot_a") if pause_evs else None
            resent_evs = [e for e in evs if e["type"] == "STOP_RESENT"]
            unconf_evs = [e for e in evs if e["type"] == "STOP_UNCONFIRMED"]
            passed = t_paused_confirm is not None and sd_cmd is not None and ok_end and st_end["robots"]["robot_a"].get("zone") == "C"

            summary = {
                "scenario": "e", "trial": i, "status": "PASS" if passed else "FAIL",
                "cmd_ts": cmd_ts,
                "dt_to_pause_cause_wall_s": round(t_pause_cause - cmd_ts, 3) if t_pause_cause else None,
                "dt_to_stopping_pause_wall_s": round(t_stopping_pause - cmd_ts, 3) if t_stopping_pause else None,
                "dt_to_paused_confirm_wall_s": round(t_paused_confirm - cmd_ts, 3) if t_paused_confirm else None,
                "dt_to_paused_confirm_sim_s": confirm_sim_dt,
                "stop_dist_from_cmd": sd_cmd,
                "stop_dist_from_db_pause": sd_ev,
                "stop_resent_count": len(resent_evs),
                "stop_unconfirmed_count": len(unconf_evs),
                "cards": [c["kind"] for c in cards],
                **metrics,
            }
            print(
                f"  RESULT E-{i}: {summary['status']} confirm_wall={summary['dt_to_paused_confirm_wall_s']}s "
                f"confirm_sim={confirm_sim_dt}s stop_path={sd_cmd['path_m'] if sd_cmd else None}m "
                f"stop_straight={sd_cmd['straight_m'] if sd_cmd else None}m resent={len(resent_evs)}",
                flush=True,
            )
            results.append(summary)
        except InvalidTrialError as exc:
            print(f"  RESULT E-{i}: INVALID — {exc}", flush=True)
            results.append({"scenario": "e", "trial": i, "status": "INVALID", "error": str(exc)})
            break
    return results


def start_bed_pub(val: bool) -> subprocess.Popen:
    val_str = "true" if val else "false"
    return subprocess.Popen(
        ["ros2", "topic", "pub", "-r", "12", "/patient/bed_exit", "std_msgs/msg/Bool", f"{{data: {val_str}}}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def stop_proc(p: subprocess.Popen | None):
    if p is None:
        return
    p.terminate()
    try:
        p.wait(timeout=3)
    except subprocess.TimeoutExpired:
        p.kill()


def run_scenario_f(trials: int):
    results = []
    bed_pub = start_bed_pub(False)
    try:
        time.sleep(2.5)
        for i in range(1, trials + 1):
            print(f"\n=== Scenario F · Trial {i}/{trials} (/patient/bed_exit True -> A stops only, B finishes, False+Resume) ===", flush=True)
            try:
                reset_for_trial("D", "F")
                csv_path = DATA_DIR / f"f_trial{i}.csv"
                md_path = DATA_DIR / f"f_trial{i}_aligned.md"
                logger = start_logger(csv_path, f"f_{i}")
                t0 = time.time()

                # Start A (D->B) first until inside corridor, then B (F->C, no corridor conflict)
                send_checked("/api/command", {"cmd": "GO_ZONE", "robot": "robot_a", "zone": "B", "operator": "sim"})
                wait_started(["robot_a"])
                tw = time.monotonic()
                while time.monotonic() - tw < 35.0:
                    st = get_state()
                    ra = st["robots"]["robot_a"]
                    if ra["state"] == "MOVING" and ra.get("part4_state") == "MOVING" and ra.get("pose") and ra["pose"]["y"] < -0.5:
                        break
                    time.sleep(0.2)

                send_checked("/api/command", {"cmd": "GO_ZONE", "robot": "robot_b", "zone": "C", "operator": "sim"})
                wait_started(["robot_b"])
                tw = time.monotonic()
                while time.monotonic() - tw < 15.0:
                    st = get_state()
                    rb = st["robots"]["robot_b"]
                    if rb["state"] == "MOVING" and rb.get("part4_state") == "MOVING":
                        break
                    time.sleep(0.15)

                # Flip /patient/bed_exit -> True
                stop_proc(bed_pub)
                t_true = time.time()
                bed_pub = start_bed_pub(True)
                print(f"  Published /patient/bed_exit=True at {t_true:.3f}", flush=True)

                a_paused = False
                b_arrived_while_true = False
                b_ever_paused = False
                tw = time.monotonic()
                while time.monotonic() - tw < 45.0:
                    st = get_state()
                    ra, rb = st["robots"]["robot_a"], st["robots"]["robot_b"]
                    if "BED_EXIT" in (rb.get("pause_causes") or []) or rb["state"] == "SAFE_PAUSE":
                        b_ever_paused = True
                    if ra["state"] == "SAFE_PAUSE" and ra.get("part4_state") == "PAUSED":
                        a_paused = True
                    if rb["state"] == "IDLE" and rb.get("zone") == "C":
                        b_arrived_while_true = True
                    if a_paused and b_arrived_while_true:
                        break
                    time.sleep(0.2)

                # Verify RESUME_ROBOT is rejected while bed_exit == True
                t_rej_try = time.time()
                send_checked("/api/command", {"cmd": "RESUME_ROBOT", "robot": "robot_a", "operator": "sim"})
                time.sleep(1.0)

                # Flip /patient/bed_exit -> False
                stop_proc(bed_pub)
                t_false = time.time()
                bed_pub = start_bed_pub(False)
                print(f"  Published /patient/bed_exit=False at {t_false:.3f}", flush=True)
                time.sleep(2.5)
                no_auto_resume = (get_state()["robots"]["robot_a"]["state"] == "SAFE_PAUSE")

                # Operator resumes robot_a -> arrives at B
                send_checked("/api/command", {"cmd": "RESUME_ROBOT", "robot": "robot_a", "operator": "sim"})
                time.sleep(0.5)
                ok_end, st_end, cards = wait_both_idle(timeout_s=120.0, stop_on_card=True, poll_s=0.5)
                time.sleep(0.4)
                stop_logger(logger)
                t1 = time.time()

                evs = query_events(t0 - 0.2, t1)
                accept_ts = next((e["ts"] for e in evs if e["type"] == "LEG_START"), t0)
                metrics = _extract_metrics(csv_path, md_path, since_ts=accept_ts)

                rows = load_csv(csv_path)
                ts_list = [r["wall_ts"] for r in rows]
                a_pause_evs = [e for e in evs if e["type"] == "PAUSE" and e["robot"] == "robot_a" and e["detail"].get("cause") == "BED_EXIT"]
                sd_bed = calc_stop_dist(rows, ts_list, a_pause_evs[0]["ts"], "robot_a") if a_pause_evs else None
                b_pause_evs = [e for e in evs if e["type"] == "PAUSE" and e["robot"] == "robot_b"]
                rej_evs = [e for e in evs if e["type"] == "RESUME_REJECTED" and e["ts"] >= t_rej_try - 0.5]
                res_evs = [e for e in evs if e["type"] == "RESUME" and e["robot"] == "robot_a"]
                a_arrived = st_end["robots"]["robot_a"].get("zone") == "B"
                passed = (
                    a_paused and b_arrived_while_true and not b_ever_paused and not b_pause_evs
                    and bool(a_pause_evs) and bool(rej_evs) and no_auto_resume and bool(res_evs)
                    and ok_end and a_arrived
                )

                summary = {
                    "scenario": "f", "trial": i, "status": "PASS" if passed else "FAIL",
                    "a_paused": a_paused, "b_arrived_while_true": b_arrived_while_true,
                    "b_ever_paused": b_ever_paused, "resume_rejected_while_true": bool(rej_evs),
                    "no_auto_resume": no_auto_resume, "a_arrived_after_resume": a_arrived,
                    "bed_exit_stop_dist": sd_bed, "cards": [c["kind"] for c in cards],
                    **metrics,
                }
                print(
                    f"  RESULT F-{i}: {summary['status']} a_paused={a_paused} b_done={b_arrived_while_true} "
                    f"b_paused={b_ever_paused} rej={bool(rej_evs)} no_auto={no_auto_resume} "
                    f"a_arr={a_arrived} stop_path={sd_bed['path_m'] if sd_bed else None}m",
                    flush=True,
                )
                results.append(summary)
            except InvalidTrialError as exc:
                print(f"  RESULT F-{i}: INVALID — {exc}", flush=True)
                results.append({"scenario": "f", "trial": i, "status": "INVALID", "error": str(exc)})
                break
    finally:
        stop_proc(bed_pub)
    return results


def run_scenario_g(trials: int):
    """Scenario G: robot3 parked at D1; robot2 runs A->F (3x) and F->A (3x).
    If BLOCKED card opens on robot2, send robot3 D->C while card is open and observe if both stay stopped.
    """
    results = []
    for sub, b_start, b_dest in (("af", "A", "F"), ("fa", "F", "A")):
        for i in range(1, trials + 1):
            label = f"g_{sub}_{i}"
            print(f"\n=== Scenario G-{sub.upper()} · Trial {i}/{trials} (A parked at D1, B: {b_start}->{b_dest}) ===", flush=True)
            try:
                reset_for_trial("D", b_start)
                csv_path = DATA_DIR / f"g_{sub}_trial{i}.csv"
                md_path = DATA_DIR / f"g_{sub}_trial{i}_aligned.md"
                logger = start_logger(csv_path, label)
                t_cmd = time.time()

                send_checked("/api/command", {"cmd": "GO_ZONE", "robot": "robot_b", "zone": b_dest, "operator": "sim"})
                wait_started(["robot_b"])

                ok, st, cards = wait_both_idle(timeout_s=120.0, stop_on_card=True, poll_s=0.5)

                blocked_cards = [c for c in cards if c["kind"] == "BLOCKED"]
                dc_test = None
                if blocked_cards:
                    print(f"  BLOCKED card opened! Sending robot_a D->C while card is open...", flush=True)
                    t_dc = time.time()
                    send_checked("/api/command", {"cmd": "GO_ZONE", "robot": "robot_a", "zone": "C", "operator": "sim"})
                    # Observe for 18s (covers decision window + proximity/follow/deadlock reaction)
                    obs = []
                    tw = time.monotonic()
                    while time.monotonic() - tw < 18.0:
                        st_dc = get_state()
                        ra, rb = st_dc["robots"]["robot_a"], st_dc["robots"]["robot_b"]
                        obs.append({
                            "dt": round(time.monotonic() - tw, 1),
                            "a_state": ra["state"], "a_p4": ra.get("part4_state"),
                            "a_wait": ra.get("wait_reason"), "a_prox": ra.get("proximity"),
                            "b_state": rb["state"], "b_p4": rb.get("part4_state"),
                            "b_wait": rb.get("wait_reason"), "b_prox": rb.get("proximity"),
                            "cards": len(st_dc["open_cards"]),
                        })
                        if ra["state"] == "IDLE" and rb["state"] == "IDLE":
                            break
                        time.sleep(1.0)
                    st_final = get_state()
                    both_stopped = (
                        st_final["robots"]["robot_a"]["state"] in ("HOLD", "SAFE_PAUSE", "FAULT")
                        and st_final["robots"]["robot_b"]["state"] in ("HOLD", "SAFE_PAUSE", "FAULT")
                        and st_final["robots"]["robot_a"].get("part4_state") != "MOVING"
                        and st_final["robots"]["robot_b"].get("part4_state") != "MOVING"
                    )
                    dc_test = {
                        "triggered_at": t_dc,
                        "both_stopped": both_stopped,
                        "final_a": {k: st_final["robots"]["robot_a"].get(k) for k in ("state", "part4_state", "wait_reason", "proximity", "pose")},
                        "final_b": {k: st_final["robots"]["robot_b"].get(k) for k in ("state", "part4_state", "wait_reason", "proximity", "pose")},
                        "open_cards_after": len(st_final["open_cards"]),
                        "trace_last": obs[-1] if obs else None,
                    }
                    print(f"  D->C under BLOCKED: both_stopped={both_stopped} A={dc_test['final_a']['state']}({dc_test['final_a']['wait_reason']}) "
                          f"B={dc_test['final_b']['state']}({dc_test['final_b']['wait_reason']})", flush=True)

                time.sleep(0.4)
                stop_logger(logger)
                t1 = time.time()

                evs = query_events(t_cmd - 0.2, t1)
                accept_ts = next((e["ts"] for e in evs if e["type"] == "LEG_START" and e["robot"] == "robot_b"), t_cmd)
                # Compute metrics BEFORE D->C test (pure passing trial) AND overall
                t_pass_end = dc_test["triggered_at"] if dc_test else t1
                metrics_pass = _extract_metrics(csv_path, md_path, since_ts=accept_ts, until_ts=t_pass_end)
                prox_evs = [e for e in evs if e["ts"] <= t_pass_end and e["type"] == "PROXIMITY_STOP"]
                b_arrived = (not blocked_cards) and ok and st["robots"]["robot_b"].get("zone") == b_dest

                summary = {
                    "scenario": f"g_{sub}", "trial": i,
                    "status": "PASS" if (b_arrived and not prox_evs) else "FAIL",
                    "b_route": f"{b_start}->{b_dest}",
                    "b_arrived": b_arrived,
                    "proximity_stop_occurred": bool(prox_evs),
                    "proximity_stops": [e["detail"] for e in prox_evs],
                    "blocked_card_opened": bool(blocked_cards),
                    "cards": [c["kind"] for c in cards],
                    "dc_deadlock_test": dc_test,
                    **metrics_pass,
                }
                mg = (metrics_pass.get("min_gt_moving") or {}).get("dist_m")
                ma = (metrics_pass.get("min_amcl_moving") or {}).get("dist_m")
                print(
                    f"  RESULT G-{sub.upper()}-{i}: {summary['status']} arrived={b_arrived} "
                    f"prox={bool(prox_evs)} blocked={bool(blocked_cards)} "
                    f"min_GT={mg}m min_AMCL={ma}m wall={metrics_pass.get('wall_duration_s')}s sim={metrics_pass.get('sim_duration_s')}s",
                    flush=True,
                )
                results.append(summary)
            except InvalidTrialError as exc:
                print(f"  RESULT G-{sub.upper()}-{i}: INVALID — {exc}", flush=True)
                results.append({"scenario": f"g_{sub}", "trial": i, "status": "INVALID", "error": str(exc)})
                break
    return results


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--scenario", required=True, choices=list("abcdefg"))
    p.add_argument("--trials", type=int, default=3)
    p.add_argument("--tag", default="", help="Optional suffix for summary JSON (e.g., smoke)")
    args = p.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fn = {
        "a": run_scenario_a, "b": run_scenario_b, "c": run_scenario_c,
        "d": run_scenario_d, "e": run_scenario_e, "f": run_scenario_f,
        "g": run_scenario_g,
    }[args.scenario]
    results = fn(args.trials)
    suffix = f"_{args.tag}" if args.tag else ""
    out_json = DATA_DIR / f"summary_{args.scenario}{suffix}.json"
    out_json.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved {out_json}", flush=True)
    if any(r.get("status") == "INVALID" for r in results):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
