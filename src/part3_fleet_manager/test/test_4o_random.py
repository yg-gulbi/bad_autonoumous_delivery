"""Stage 4O · 7C: Random event stress test in PLAN mode (50 seeds default, PLAN_RANDOM_SEEDS=300 for full run).

Checks 1-5 are the ones of ac58e60. Check 4 used the preemption rule, which PLAN no longer has; it keeps its
meaning as "a pending robot the plan lets go is not kept waiting longer than decision_window_s + 2 s".

Events: missions, cancels (with a higher-Level resubmit), manual pauses and 2 s link drops (1b565ba), and since
the 4O follow-up: quick send (GO_ZONE with a Level), Level change (SET_LEVEL), the same mission sent, cancelled
and sent again three times within 5 s, a second cancel before the first is confirmed, bed exit (the PART 05
feed runs all the time), an obstacle (Nav2 gives up, PART 06 verdict, card), both robots losing the link at
once, a link loss longer than link_timeout_s (of a robot that is not driving). On odd seeds robot_b returns to its dock and starts docked, so
every one of its trips leaves the dock and ends docked.
"""
import os
import random
from dataclasses import replace
from math import inf

import pytest

from kinematic_mock import KinematicSim
from part3_fleet_manager.core.robot_protocol import DONE

A, B = "robot_a", "robot_b"
ZONES = ("A", "B", "C", "D", "E", "F")
DEFAULT_SEEDS = int(os.environ.get("PLAN_RANDOM_SEEDS", "50"))
SIM_DURATION_S = 600.0
DRAIN_LIMIT_S = 480.0
DT = 0.2
DRIVING = ("MOVING", "UNDOCKING", "LOCALIZING", "DOCKING")


def _travel_dir(bot, smap) -> int:
    if bot.state != "MOVING" or not bot.path:
        return 0
    dp = smap.projection(bot.path[0]) - smap.projection(bot.pose)
    if abs(dp) < 1e-4:
        return 0
    return 1 if dp > 0 else -1


def seed_config(cfg, seed: int):
    """Odd seeds: robot_b goes home to its dock (and starts on it)."""
    if seed % 2 and cfg.robots[B].dock is not None:
        return replace(cfg, robots={**cfg.robots, B: replace(cfg.robots[B], return_to="DOCK")}), {B}
    return cfg, set()


def run_random_seed(cfg, seed: int, *, duration_s: float = SIM_DURATION_S) -> dict:
    rng = random.Random(seed)
    cfg, docked = seed_config(cfg, seed)
    sim = KinematicSim(cfg, mode="PLAN", speed_mps=0.2, docked=docked, bed_feed=True)
    log: list[str] = []
    submitted_refs: list[str] = []
    stats = {"missions": 0, "cancels": 0, "requeues": 0, "pauses": 0, "drops": 0, "safety_cards": 0,
             "prox_stops": 0, "prox_stops_planned": 0, "replans": 0, "plan_ms_max": 0.0, "fallbacks": 0,
             "quick_sends": 0, "level_changes": 0, "bursts": 0, "double_cancels": 0, "bed_exits": 0,
             "obstacles": 0, "double_drops": 0, "long_drops": 0, "undocks": 0, "docks": 0,
             "rejected": 0}
    injected_until = {"t": -1.0}   # proximity stops right after a manual pause or link drop are not plan failures
    scheduled: list[tuple[float, object]] = []   # (time, action) for events made of several steps
    last_resume: dict[str, float] = {}
    verdict_for: dict[str, float] = {}
    sent_home: dict[str, float] = {}

    def schedule(at, action):
        scheduled.append((at, action))
        scheduled.sort(key=lambda item: item[0])

    def injected(until):
        injected_until["t"] = max(injected_until["t"], until)

    # Pre-schedule random events across the 10-minute window.
    next_event_t = rng.uniform(0.5, 4.0)
    paused_until: dict[str, float] = {}
    both_wait_s = 0.0
    max_both_wait_s = 0.0
    start_wait_since: dict[str, float] = {}
    max_start_wait_s = 0.0
    confirm_at: dict[str, float] = {}
    card_answered: set[str] = set()

    def fail(msg: str):
        tail = "\n  ".join(log[-40:])
        raise AssertionError(f"[seed={seed} t={sim.now:.1f}s] {msg}\nRecent events ({len(log)} total):\n  {tail}")

    def check_invariants(phase: str):
        nonlocal both_wait_s, max_both_wait_s, max_start_wait_s
        sm = sim.core.resources
        smap = sm.map
        now = sim.now

        # Invariant 1 & 2: narrow segment exclusivity, direction consistency, and held-before-entry.
        for name in smap.narrow_names:
            seg = sm.segments[name]
            # 1a. Holder-level invariants
            if seg.kind == "single" and len(seg.holders) > 1:
                fail(f"Inv1(holder): single segment {name} has multiple holders {list(seg.holders)}")
            if seg.kind == "one_way":
                dirs = {h.direction for h in seg.holders.values() if h.direction}
                if len(dirs) > 1:
                    fail(f"Inv1(holder): one_way segment {name} has opposite holder directions {seg.holders}")
            # 1b & 2. Physical pose invariants
            inside_robots = []
            for r, bot in sim.robots.items():
                if seg.spec.contains(bot.pose):
                    inside_robots.append(r)
                    if r not in seg.holders:
                        fail(f"Inv2: {r} physically inside unheld narrow segment {name} at {bot.pose} (state={bot.state}, holders={list(seg.holders)})")
            if seg.kind == "single" and len(inside_robots) > 1:
                fail(f"Inv1(phys): single segment {name} contains {inside_robots}")
            if seg.kind == "one_way" and len(inside_robots) > 1:
                phys_dirs = set()
                for r in inside_robots:
                    h = seg.holders.get(r)
                    d = (h.direction if h and h.direction else 0) or _travel_dir(sim.robots[r], smap)
                    if d:
                        phys_dirs.add(d)
                if len(phys_dirs) > 1:
                    fail(f"Inv1(phys): one_way segment {name} has opposite robots {inside_robots}")

        # Also check single slots never have > 1 holder.
        for (_, _), sname in smap.slot_names.items():
            seg = sm.segments[sname]
            if len(seg.holders) > 1:
                fail(f"Inv1(slot): single slot {sname} has multiple holders {list(seg.holders)}")

        # Invariant 3: both robots not simultaneously stuck in traffic wait > 30 s (excluding safety pause & link drop).
        any_safety = any(bool(sim.core.safety.causes[r]) for r in sim.robots)
        any_offline = any((not sim.core.link_ok[r]) or (now < sim.robots[r].drop_until) for r in sim.robots)
        if not any_safety and not any_offline:
            waiting = []
            for r, bot in sim.robots.items():
                unit = sim.core.mission.robots[r]
                vis = sim.core.visible_state(r, unit)
                is_wait = (
                    (vis == "HOLD" or sm.is_stop_line_paused(r) or r in sm.pending)
                    and bot.state in ("IDLE", "COMPLETE", "CANCELED", "PAUSED")
                    and (unit.job is not None or r in sm.pending)
                )
                if is_wait:
                    waiting.append(r)
            if len(waiting) == len(sim.robots):
                both_wait_s += DT
                max_both_wait_s = max(max_both_wait_s, both_wait_s)
                if both_wait_s > 30.0:
                    reasons = {r: sim.core.wait_reason(r) for r in sim.robots}
                    poses = {r: (round(sim.robots[r].pose[0], 2), round(sim.robots[r].pose[1], 2)) for r in sim.robots}
                    holders = {k: list(v.holders) for k, v in sm.segments.items() if v.holders}
                    fail(f"Inv3({phase}): both robots stuck in traffic wait for {both_wait_s:.1f}s > 30s; reasons={reasons} poses={poses} holders={holders}")
            else:
                both_wait_s = 0.0

        # Invariant 4: a pending robot the plan lets go now gets its MOVE within decision_window_s + 2.0 s.
        limit_s = cfg.resources.decision_window_s + 2.0
        for r in sim.robots:
            leg = sm.pending.get(r)
            plan = sm.plans.get(r)
            if (leg is not None and plan is not None and plan.legs and sm.eligible(r) and not any_safety
                    and not any_offline and r not in sm.held and not sim.core.mission.robots[r].replacing
                    and r not in sim.core.collision.access.waits and r not in sim.core.collision.contacts):
                start = start_wait_since.setdefault(r, now)
                waited = now - start
                max_start_wait_s = max(max_start_wait_s, waited)
                if waited > limit_s + 1e-6:
                    fail(f"Inv4({phase}): {r} waited {waited:.2f}s > {limit_s:.2f}s although the plan lets it go")
                continue
            start_wait_since.pop(r, None)

    def handle_operator_routine():
        now = sim.now
        # Auto-confirm WAIT_CONFIRM after a brief random 0.4~1.6 s pause during active window, or immediately in drain.
        for r, unit in sim.core.mission.robots.items():
            if unit.state == "WAIT_CONFIRM" and unit.job:
                due = confirm_at.setdefault(r, now + (rng.uniform(0.4, 1.6) if now < duration_s else 0.0))
                if now >= due:
                    confirm_at.pop(r, None)
                    sim.confirm(r, unit.job.id)
                    log.append(f"t={now:.1f} CONFIRM {r} {unit.job.id}")
            else:
                confirm_at.pop(r, None)
        # Resume after a manual pause has run out or the bed is back to normal (once a second while denied).
        for r in list(paused_until):
            if now >= paused_until[r]:
                paused_until.pop(r)
        safety = sim.core.safety
        for r in sim.robots:
            if not safety.causes[r] or r in paused_until or now - last_resume.get(r, -inf) < 1.0:
                continue
            if safety.denial([r], now, safety.bed_exit):
                continue
            last_resume[r] = now
            sim.resume(r)
            log.append(f"t={now:.1f} RESUME {r} {sorted(safety.causes[r])}")
        # PART 06 verdict on the photo of a Nav2 abort in front of an obstacle (0.6 s later).
        for r, bot in sim.robots.items():
            if bot.gave_up_at is not None and verdict_for.get(r) != bot.gave_up_at and now >= bot.gave_up_at + 0.6:
                verdict_for[r] = bot.gave_up_at
                label = rng.choice(("medical_box", "person", None))
                sim.obstacle_verdict(r, label)
                log.append(f"t={now:.1f} OBSTACLE_VERDICT {r} {label}")
        # Drain: a robot left elsewhere by a quick send is sent home.
        if now >= duration_s:
            for r, unit in sim.core.mission.robots.items():
                home = cfg.robots[r].start_zone
                if (unit.state == "IDLE" and unit.job is None and not unit.queue and not unit.canceling
                        and unit.zone != home and sim.robots[r].state in DONE and not safety.causes[r]
                        and now - sent_home.get(r, -inf) > 10.0):
                    sent_home[r] = now
                    sim.cmd("GO_ZONE", robot=r, zone=home)
                    log.append(f"t={now:.1f} GO_HOME {r} {home}")
        # If a boundary drop ever caused UNKNOWN while robot is back online, operator confirms clear.
        sm = sim.core.resources
        for name, seg in sm.segments.items():
            if seg.state == "UNKNOWN" and all(sim.core.link_ok[r] for r in seg.uncertain):
                sim.cmd("RESOURCE_CLEAR_CONFIRM", resource=name)
                log.append(f"t={now:.1f} CLEAR_UNKNOWN {name}")
        # Operator retries any robot waiting in FAULT after a head-on retreat back to previous zone.
        for r, unit in sim.core.mission.robots.items():
            if unit.state == "FAULT" and unit.job and not unit.canceling and not unit.replacing and not sim.core.safety.causes[r]:
                sim.cmd("RETRY", robot=r)
                log.append(f"t={now:.1f} RETRY {r} {unit.job.id}")
        # Operator resolves open safety-net cards (PROXIMITY / HEAD_ON / DEADLOCK) after 1.0 s reaction time.
        for card_id, card in list(sim.core.decisions.cards.items()):
            if card_id in card_answered or now - card["created_at"] < 1.0:
                continue
            if any(sim.core.safety.causes.get(r) for r in card.get("robots", ())):
                continue
            opts = [o["id"] for o in card["options"]]
            choice = None
            if card.get("recommended") in opts:
                choice = card["recommended"]
            elif card.get("context") == "PROXIMITY":
                r = card["robots"][0]
                wait = sim.core.collision.near.get(r)
                peer = wait.peer if wait else None
                for cand in ("self_first", "peer_first", "return", "retry"):
                    if cand not in opts:
                        continue
                    if cand == "self_first" and (not peer or not sim.core.collision.can_go_first(r, peer, sim.core.mission, now)):
                        continue
                    if cand == "peer_first" and (not peer or not sim.core.collision.can_go_first(peer, r, sim.core.mission, now)):
                        continue
                    if cand == "return" and not sim.core.mission.robots[r].job:
                        continue
                    choice = cand
                    break
            if choice is None and opts:
                choice = opts[0]
            if choice:
                card_answered.add(card_id)
                stats["safety_cards"] += 1
                sim.choose(card_id, choice)
                log.append(f"t={now:.1f} CARD_CHOOSE {card.get('context') or card['kind']} {card['robots']} -> {choice}")

    def inject_random_event():
        now = sim.now
        # Choose event type weighted toward rich traffic mixing.
        active_missions = [
            (r, u.job) for r, u in sim.core.mission.robots.items()
            if u.job is not None and u.job.kind != "RETURN_HOME"
        ]
        queued_missions = [
            (r, j) for r, u in sim.core.mission.robots.items() for j in u.queue
        ] + [(None, j) for j in sim.core.mission.unassigned]

        roll = rng.random()
        if roll < 0.40 and len(active_missions) + len(queued_missions) < 6:
            # New random mission (kind, level, zones)
            kind = rng.choice(("PATIENT", "MED", "CALL"))
            lvl = rng.choice((1, 2, 3))
            if kind == "PATIENT":
                z1, z2 = rng.sample(ZONES, 2)
                ref = sim.submit_patient(z1, z2, level=lvl)
                log.append(f"t={now:.1f} NEW PATIENT L{lvl} {z1}->{z2} ({ref})")
            elif kind == "MED":
                stops = rng.sample(ZONES, rng.choice((2, 3)))
                ref = sim.submit_med(stops, level=lvl)
                log.append(f"t={now:.1f} NEW MED L{lvl} {'->'.join(stops)} ({ref})")
            else:
                z = rng.choice(ZONES)
                ref = sim.submit_call(z, level=lvl)
                log.append(f"t={now:.1f} NEW CALL L{lvl} {z} ({ref})")
            submitted_refs.append(ref)
            stats["missions"] += 1
        elif roll < 0.52 and active_missions:
            # Cancel an active mission at any time (including mid-corridor!), sometimes re-submitting with higher Level!
            r, job = rng.choice(active_missions)
            sim.cancel(job.id)
            stats["cancels"] += 1
            sm = sim.core.resources
            in_narrow = any(r in sm.segments[n].holders and sm.segments[n].holders[r].state == "OCCUPIED"
                            for n in sm.map.narrow_names)
            log.append(f"t={now:.1f} CANCEL {job.id} ({r}, in_narrow={in_narrow})")
            if rng.random() < 0.50:
                new_lvl = min(3, job.level + 1)
                if job.kind == "PATIENT_TRANSPORT":
                    z1, z2 = rng.sample(ZONES, 2)
                    ref = sim.submit_patient(z1, z2, level=new_lvl)
                    log.append(f"t={now:.1f} REQUEUE PATIENT L{new_lvl} {z1}->{z2} ({ref})")
                elif job.kind == "MED_ROUND":
                    stops = rng.sample(ZONES, 2)
                    ref = sim.submit_med(stops, level=new_lvl)
                    log.append(f"t={now:.1f} REQUEUE MED L{new_lvl} {'->'.join(stops)} ({ref})")
                else:
                    z = rng.choice(ZONES)
                    ref = sim.submit_call(z, level=new_lvl)
                    log.append(f"t={now:.1f} REQUEUE CALL L{new_lvl} {z} ({ref})")
                submitted_refs.append(ref)
                stats["requeues"] += 1
        elif roll < 0.58:
            # Pause a robot for 2~6 s, then resume.
            r = rng.choice((A, B))
            if r not in paused_until and not sim.core.safety.causes[r]:
                dur = rng.uniform(2.0, 6.0)
                sim.pause(r)
                paused_until[r] = now + dur
                injected_until["t"] = max(injected_until["t"], now + dur + 10.0)
                stats["pauses"] += 1
                log.append(f"t={now:.1f} PAUSE {r} for {dur:.1f}s")
        elif roll < 0.62:
            # 2 s link drop.
            r = rng.choice((A, B))
            if now >= sim.robots[r].drop_until:
                sim.drop_link(r, 2.0)
                injected_until["t"] = max(injected_until["t"], now + 12.0)
                stats["drops"] += 1
                log.append(f"t={now:.1f} DROP_LINK {r} 2.0s")
        elif roll < 0.68:
            # Quick send (GO_ZONE) with a Level to a robot without a mission.
            idle = [r for r, u in sim.core.mission.robots.items() if not u.job and not u.queue and not u.canceling]
            if idle:
                r, zone, level = rng.choice(idle), rng.choice(ZONES), rng.choice((1, 2, 3))
                sim.cmd("GO_ZONE", robot=r, zone=zone, level=level)
                stats["quick_sends"] += 1
                log.append(f"t={now:.1f} GO_ZONE {r} {zone} L{level}")
        elif roll < 0.74:
            # Level change of a waiting or running mission.
            jobs = [j for _, j in active_missions + queued_missions]
            if jobs:
                job = rng.choice(jobs)
                level = rng.choice([lvl for lvl in (1, 2, 3) if lvl != job.level])
                sim.cmd("SET_LEVEL", mission_id=job.id, level=level)
                stats["level_changes"] += 1
                log.append(f"t={now:.1f} SET_LEVEL {job.id} L{job.level}->L{level}")
        elif roll < 0.78:
            # The same mission sent, cancelled and sent again three times within 5 s (the last one stays).
            kind, level = rng.choice(("PATIENT", "MED", "CALL")), rng.choice((1, 2, 3))
            zones = rng.sample(ZONES, 2)
            refs: list[str] = []

            def send():
                if kind == "PATIENT":
                    refs.append(sim.submit_patient(zones[0], zones[1], level=level))
                elif kind == "MED":
                    refs.append(sim.submit_med(zones, level=level))
                else:
                    refs.append(sim.submit_call(zones[0], level=level))
                stats["missions"] += 1
                submitted_refs.append(refs[-1])
                log.append(f"t={sim.now:.1f} BURST SEND {kind} L{level} {zones} ({refs[-1]})")

            def cancel_last():
                mid = sim.ids.get(refs[-1]) if refs else None
                if mid:
                    sim.cancel(mid)
                    stats["cancels"] += 1
                    log.append(f"t={sim.now:.1f} BURST CANCEL {mid}")

            for i, action in enumerate((send, cancel_last, send, cancel_last, send, cancel_last, send)):
                schedule(now + 0.6 * i, action)
            stats["bursts"] += 1
        elif roll < 0.82:
            # Cancel a running mission twice (same tick and again before the robot confirms the CANCEL),
            # and its robot's next waiting mission right after.
            if active_missions:
                r, job = rng.choice(active_missions)
                sim.cancel(job.id)
                sim.cancel(job.id)
                schedule(now + DT, lambda mid=job.id: sim.cancel(mid))
                queue = list(sim.core.mission.robots[r].queue)
                if queue:
                    schedule(now + DT, lambda mid=queue[0].id: sim.cancel(mid))
                stats["cancels"] += 1
                stats["double_cancels"] += 1
                log.append(f"t={now:.1f} DOUBLE_CANCEL {job.id} ({r}, robot {sim.robots[r].state})")
        elif roll < 0.86:
            # Bed exit for 1~4 s (the feed keeps running); robot_a is held until the operator resumes.
            dur = rng.uniform(1.0, 4.0)
            sim.bed_value = True

            def bed_back():
                sim.bed_value = False
            schedule(now + dur, bed_back)
            injected(now + dur + 10.0)
            stats["bed_exits"] += 1
            log.append(f"t={now:.1f} BED_EXIT {dur:.1f}s")
        elif roll < 0.90:
            # Obstacle in front of a driving robot: it stops, Nav2 gives up, PART 06 answers, a card opens.
            moving = [r for r, bot in sim.robots.items() if bot.state == "MOVING" and bot.blocked_since is None]
            if moving:
                r = rng.choice(moving)
                sim.obstacle(r)
                injected(now + sim.cfg.mock.abort_after_s + 10.0)
                stats["obstacles"] += 1
                log.append(f"t={now:.1f} OBSTACLE {r} at {sim.robots[r].pose}")
        elif roll < 0.94:
            # Both robots lose the link for 2 s at the same time.
            for r in (A, B):
                sim.drop_link(r, 2.0)
            injected(now + 12.0)
            stats["double_drops"] += 1
            log.append(f"t={now:.1f} DROP_LINK both 2.0s")
        else:
            # A robot that is not driving loses the link longer than link_timeout_s. (A robot driving toward a
            # closed stop line cannot be stopped once a PAUSE is lost for longer than the stop line lasts; the
            # report measures that case separately.)
            standing = [r for r, bot in sim.robots.items() if bot.state not in DRIVING and now >= bot.drop_until]
            if standing:
                r = rng.choice(standing)
                dur = rng.uniform(cfg.robot_link.link_timeout_s + 1.0, cfg.robot_link.link_timeout_s + 5.0)
                sim.drop_link(r, dur)
                injected(now + dur + 10.0)
                stats["long_drops"] += 1
                log.append(f"t={now:.1f} DROP_LINK {r} {dur:.1f}s (long, {sim.robots[r].state})")

    def count_events(out):
        stats["rejected"] += sum(1 for ack in out.acks if not ack.get("ok"))
        for e in out.events:
            if e["type"] == "PROXIMITY_STOP":
                stats["prox_stops"] += 1
                if e["ts"] > injected_until["t"] and not any(sim.core.safety.causes[r] for r in sim.robots):
                    stats["prox_stops_planned"] += 1
                    log.append(f"t={e['ts']:.1f} PROXIMITY_STOP {e['robot']} {e['detail'].get('distance_m')}")
            elif e["type"] == "PLAN_UPDATE":
                stats["replans"] += 1
                stats["plan_ms_max"] = max(stats["plan_ms_max"], e["detail"]["plan_ms"])
                stats["fallbacks"] += int(e["detail"]["fallback"])

    try:
        # Phase 1: 10 simulated minutes of mixed random events.
        while sim.now < duration_s - 1e-9:
            if sim.now >= next_event_t:
                inject_random_event()
                next_event_t = sim.now + rng.uniform(4.0, 16.0)
            while scheduled and scheduled[0][0] <= sim.now + 1e-9:
                scheduled.pop(0)[1]()
            handle_operator_routine()
            out = sim.step(DT)
            count_events(out)
            check_invariants("active")

        # Phase 2: Resume any active manual pause and drain all missions back to home.
        for r in list(paused_until):
            paused_until.pop(r)
            if sim.core.safety.causes[r]:
                sim.resume(r)
                log.append(f"t={sim.now:.1f} DRAIN_RESUME {r}")

        drain_end = sim.now + DRAIN_LIMIT_S
        while sim.now < drain_end - 1e-9:
            while scheduled and scheduled[0][0] <= sim.now + 1e-9:
                scheduled.pop(0)[1]()
            handle_operator_routine()
            out = sim.step(DT)
            count_events(out)
            check_invariants("drain")
            # Stop draining early once everything is completely settled at home.
            all_idle_home = all(
                sim.core.mission.robots[r].state == "IDLE"
                and sim.core.mission.robots[r].job is None
                and not sim.core.mission.robots[r].queue
                and sim.core.mission.robots[r].zone == cfg.robots[r].start_zone
                and sim.robots[r].state in DONE
                for r in sim.robots
            )
            if all_idle_home and not sim.core.mission.unassigned and not sim.core.decisions.cards:
                break

        # Invariant 5: all missions DONE/CANCELED, both robots IDLE at home, no open cards!
        snap = sim.core.snapshot(sim.now)
        for m in snap["missions"]:
            if m["status"] not in ("DONE", "CANCELED"):
                fail(f"Inv5: mission {m['mission_id']} not terminal (status={m['status']})")
        if sim.core.mission.unassigned:
            fail(f"Inv5: unassigned missions left: {[j.id for j in sim.core.mission.unassigned]}")
        for r in sim.robots:
            unit = sim.core.mission.robots[r]
            home = cfg.robots[r].start_zone
            if unit.state != "IDLE" or unit.zone != home or unit.job is not None:
                fail(f"Inv5: {r} not IDLE at home {home} (state={unit.state}, zone={unit.zone}, pose={sim.robots[r].pose}, wait={sim.core.wait_reason(r)})")
        if sim.core.decisions.cards:
            fail(f"Inv5: open decision cards remain: {list(sim.core.decisions.cards.values())}")
        for name in sim.core.resources.map.narrow_names:
            if sim.core.resources.segments[name].holders:
                fail(f"Inv5: narrow segment {name} still held by {sim.core.resources.segments[name].holders}")

        stats["max_both_wait_s"] = round(max_both_wait_s, 2)
        stats["max_start_wait_s"] = round(max_start_wait_s, 2)
        stats["undocks"] = sum(bot.undocks for bot in sim.robots.values())
        stats["docks"] = sum(bot.docks for bot in sim.robots.values())
        return stats
    finally:
        sim.close()


@pytest.mark.parametrize("seed", range(DEFAULT_SEEDS))
def test_random_segments_invariants(cfg, seed):
    run_random_seed(cfg, seed)


def test_seed_39_e_room_no_mutual_proximity_stop(cfg):
    """ac58e60 failed seed 39: both robots stopped 0.38 m apart inside room E for over 30 s."""
    stats = run_random_seed(cfg, 39)
    assert stats["max_both_wait_s"] <= 30.0
    assert stats["prox_stops_planned"] == 0
