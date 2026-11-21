"""Making the plan again: when (``replan_due``), from what (``make_job``: one planner agent per robot, from the
robot state layer), the search itself (``run_job``, pure: it can run outside the tick, plan_runner) and turning
its result into the robots' plans and the ADG dependencies (``apply_job``).

The plan is made again on a new leg request, a finished or cancelled leg, arrival at an aside slot, a link
recovery or loss, a safety stop release, a Level change, an operator's UNKNOWN check, and when a robot is
``replan_delay_s`` behind the plan.
"""

from dataclasses import dataclass, field, replace
from math import hypot

from .plan_exec import PlanLeg, RobotPlan, Entry, Dep, conflict_kind
from .planner import SLOT2_PENALTY, Agent

PAUSED_CUT_M = 0.05      # a PAUSED robot's route is cut this far ahead: it may wait right where it stopped


@dataclass
class PlanJob:
    """One planning run: what it starts from (made in the tick) and what it found (filled by run_job)."""
    reason: str
    at: float                      # tick time the robots' states were taken
    seq: int                       # PlanManager._marks when it was made: a later mark makes it stale
    agents: list
    walls: tuple
    coast: dict                    # robot that may still drive its MOVE -> remaining pieces
    driving: dict                  # robot -> endpoint of the MOVE it drives when the job was made, or None
    fenced: set = field(default_factory=set)
    result: object = None


class PlanReplan:
    """Mixed into PlanManager."""

    # --- when ----------------------------------------------------------------------------------------------

    def _mark(self, reason):
        self._marks += 1
        if self.dirty is None:
            self.dirty = reason

    def _late(self, now):
        step_s = self.gcfg.plan_step_s
        start = self.plan_info.get("at", now)
        for robot, plan in self.plans.items():
            if plan.failed and robot not in self.fenced and now - start > self.gcfg.replan_delay_s:
                return robot        # a fenced robot waits for the operator's check (that plans again)
            nxt = plan.entry + 1
            if nxt < len(plan.entries) and plan.entries[nxt].leg == 0:
                due = start + plan.entries[nxt].t0 * step_s
                if now > due + self.gcfg.replan_delay_s:
                    return robot
        return None

    def replan_due(self, now):
        """The reason to plan again now, or None."""
        if self.dirty is None and now - self._late_replan >= self.gcfg.replan_delay_s:
            late = self._late(now)
            if late is not None:
                self._late_replan = now
                self._mark(f"{self.robot_label(late)} 계획보다 늦음")
        return self.dirty

    # --- from what -----------------------------------------------------------------------------------------

    def _goal_endpoints(self, robot, leg):
        if leg.dock_path:
            dock = self.config.robots[robot].dock
            return [n for n, p in self.graph.endpoints.items() if p.kind == "dock" and dock and p.anchor == (dock.x, dock.y)]
        count = self.config.slot_count(leg.destination) if self.slot_support.get(robot, True) else 1
        return [self.map.slot_names[(leg.destination, s)] for s in range(1, count + 1)]

    def _aside_targets(self, robot, start_xy, exclude_zone, here, *, every=False):
        """The ``aside_candidates`` slots with the shortest route (``every``: all of them, nearest first)."""
        if not self.gcfg.aside_candidates:
            return []
        slots = [n for n, p in self.graph.endpoints.items() if p.kind == "slot" and p.zone != exclude_zone
                 and n != here and (self.slot_support.get(robot, True) or p.slot == 1)]
        if here in self.graph.from_endpoint:
            table = self.graph.from_endpoint[here]
            steps = {n: self.graph.route_steps(table[n]) for n in slots if n in table}
        else:
            steps = {n: self.graph.route_steps(self.graph.route_from(start_xy, n)) for n in slots}
        ranked = sorted(steps, key=lambda n: (steps[n], n))
        return ranked if every else ranked[:self.gcfg.aside_candidates]

    def _agent(self, robot, coast):
        """Agent for the planner, or None when the robot cannot be placed on the map. ``coast`` gets the rest of
        the MOVE of a robot that may still drive it."""
        view = self.robots.view(robot)
        pose = view.pose
        if pose is None:
            return None
        leg = self.active.get(robot) or self.pending.get(robot)
        stop_m = self.config.traffic.stop_margin_m
        static = (not view.connected or view.safe_pause or view.stalled or view.cancelling
                  or self.confirming.get(robot, False))     # WAIT_CONFIRM: a patient may be getting on (D-56)
        # The proximity rule (clearance.stop_distance) gives slot_stop_m to a robot standing still at a slot with
        # no granted leg, online or not (4O 정리).
        slot_rule = robot not in self.active
        here = self.robots.parked_at.get(robot)
        if here is not None and not self.graph.points[here].region.contains(pose):
            here = None
        if here is None and any(p.kind == "dock" and p.region.contains(pose) for p in self.graph.endpoints.values()):
            here = next(n for n, p in self.graph.endpoints.items() if p.kind == "dock" and p.region.contains(pose))
        moving = view.driving and not static
        target = self.robots.move_endpoint(robot) if moving else None
        if leg is not None:
            goals = self._goal_endpoints(robot, leg)
            weight = self.gcfg.level_weights.get(self.level_of(leg), 1.0)
            goal_zone = leg.destination
        else:
            weight = self.gcfg.idle_weight
            home = self.aside_home.get(robot)
            goals = [home] if isinstance(home, str) else []
            goal_zone = self.graph.endpoints[home].zone if isinstance(home, str) else (
                self.graph.endpoints[here].zone if here in self.graph.endpoints else None)
        free_routes = {}
        starts = []
        if moving and target is not None:
            via_done = view.via_done
            origin = self.robots.origin(robot, pose)
            extra = self._still_held(robot)
            # A robot driving cannot stop before its stop line (stop_margin_m ahead), so the points up to there
            # are fixed; the route is cut at the stop line, so it can still wait right there. A robot that has
            # already stopped (PAUSED) can stay where it is: its route is cut PAUSED_CUT_M ahead of it, so it can
            # wait there (a robot inside a piece cannot wait; before, a PAUSED robot had to drive through the whole
            # piece it stood in, and two robots paused in E1 and E2 had no plan, random seed 112 of 4O follow-up 4).
            driving = view.rolling
            cut = stop_m if driving else (PAUSED_CUT_M if view.paused else None)
            rid = self.graph.route_from(pose, target, start=origin, via_done=via_done, extra=extra, cut=cut)
            route = self.graph.routes[rid]
            until = max((j for j, s0 in enumerate(route.starts) if j > 0 and s0 < stop_m - 1e-6), default=None) \
                if driving else None
            starts.append((rid, until))
            first = route.line[1] if len(route.line) > 1 else route.line[0]
            heading = (first[0] - pose[0], first[1] - pose[1])
            norm = hypot(*heading) or 1.0
            committed = [self.graph.pieces[p].point for p in route.pieces[:(until or 0) + 1]]
            # Aside slots it can still turn to: the nearest aside_candidates among the slots ahead (a slot
            # behind it would need a turn inside the stop line, so it does not take one of the places). A robot
            # that has stopped (PAUSED) can turn: CANCEL, then a new MOVE (move_start_s, as for any new route).
            asides = 0
            for other in [*goals, *self._aside_targets(robot, pose, goal_zone, None, every=True)]:
                if other == target:
                    continue
                if other not in goals and asides >= self.gcfg.aside_candidates:
                    break
                alt = self.graph.route_from(pose, other, start=origin, via_done=via_done, extra=extra, cut=cut)
                alt_route = self.graph.routes[alt]
                if len(alt_route.line) < 2 or not alt_route.pieces:
                    continue
                step = (alt_route.line[1][0] - pose[0], alt_route.line[1][1] - pose[1])
                cos = (step[0] * heading[0] + step[1] * heading[1]) / (norm * (hypot(*step) or 1.0))
                alt_points = [self.graph.pieces[p].point for p in alt_route.pieces[:len(committed)]]
                if not driving or (cos > 0.7 and alt_points == committed):
                    starts.append((alt, until))
                    asides += other not in goals
            stand_goal = None
        else:
            extra = self._still_held(robot)
            if here is not None:
                stand_goal = self.graph.parked(here, extra, slot_rule=slot_rule)
                rid = self.graph.stay_route(here, piece=stand_goal)
            else:
                stand_goal = self.graph.stand(pose, extra, slot_rule=slot_rule)
                rid = self.graph.stay_route(None, piece=stand_goal)
            starts.append((rid, None))
        if leg is None and not goals:
            if stand_goal is not None and here is None:
                # Stopped on the way without a mission: staying is free, and so is parking in a nearby slot
                # (it cannot drive back to a free position once it has moved aside).
                near = self._aside_targets(robot, pose, None, None)[:2]
                goals_pieces = {stand_goal: 0.0, **{self.graph.park[n]: 0.0 for n in near}}
                goals = near
            elif stand_goal is not None:
                goals_pieces = {stand_goal: 0.0, self.graph.park[here]: 0.0}
                goals = [here] if here is not None else []
            elif target is not None:
                goals_pieces = {self.graph.park[target]: 0.0}
                goals = [target]
            else:
                goals_pieces = {}
        else:
            goals_pieces = {self.graph.park[n]: (SLOT2_PENALTY if self.graph.endpoints[n].slot == 2 else 0.0)
                            for n in goals}
            if here in goals and stand_goal is not None:
                goals_pieces[stand_goal] = goals_pieces[self.graph.park[here]]
        if not goals_pieces:
            static = True
        aside = self._aside_targets(robot, pose, goal_zone, here) if not static else []
        targets = tuple(dict.fromkeys([*aside, *goals]))
        if here is None and not moving and not static:
            free_routes[rid] = [self.graph.route_from(pose, n, extra=extra) for n in targets]
        blocks = ()
        coast_target = self.robots.move_endpoint(robot) if static and view.coasting else None
        if coast_target is not None:
            # CANCEL not confirmed yet, or link lost while driving: the robot may still go on to its MOVE
            # target, so block that way.
            rid = self.graph.route_from(pose, coast_target, start=self.robots.origin(robot, pose),
                                        via_done=view.via_done, extra=self._still_held(robot))
            blocks = tuple(self.graph.routes[rid].pieces)
            coast[robot] = blocks
        return Agent(robot, weight, starts, goals_pieces, targets, tuple(goals), free_routes, static=static,
                     blocks=blocks)

    def make_job(self, now, reason):
        """Everything the search needs, taken from the robots now. The graph's routes for the robots' positions
        are made here, in the tick, so the search only reads the graph."""
        coast = {}
        agents = [a for a in (self._agent(r, coast) for r in self.config.robots) if a is not None]
        driving = {r: self.robots.move_endpoint(r) if self.robots.view(r).driving else None for r in self.config.robots}
        job = PlanJob(reason, now, self._marks, agents, self._fences(), coast, driving)
        self._last_replan = now
        self.dirty = None
        return job

    # --- the search ----------------------------------------------------------------------------------------

    def run_job(self, job):
        """The search alone (no plan state is touched): it may run in the planning thread."""
        agents = list(job.agents)
        if job.walls:
            # A robot that cannot reach its goal even alone, only because of UNKNOWN segments, waits where it is
            # for the operator's check ("현장 확인 필요"); no other robot is moved aside for it (D-54).
            budget = max(5.0, self.gcfg.plan_limit_ms / 4)
            for i, agent in enumerate(agents):
                if not agent.static and agent.goal_targets and \
                        self.planner.reachable(agent, job.walls, limit_ms=budget) is False:
                    job.fenced.add(agent.name)
                    agents[i] = replace(agent, static=True)
        job.agents = agents
        job.result = self.planner.plan([*agents, *job.walls], limit_ms=self.gcfg.plan_limit_ms)
        return job

    # --- into plans ----------------------------------------------------------------------------------------

    def apply_job(self, job, now):
        result, agents = job.result, job.agents
        self.coast_route = dict(job.coast)
        self.fenced = {a.name: self._fenced_names(a) for a in agents if a.name in job.fenced}
        old = self.plans
        self.plans = {}
        goal_names = {a.name: set(a.goal_targets) for a in agents}
        for agent in agents:
            events = result.events.get(agent.name)
            if agent.name in self.fenced:
                events = [(0, self.planner.initial_state(agent))]
            elif not events or agent.static:
                continue
            self.plans[agent.name] = self._robot_plan(agent.name, events, goal_names[agent.name], old.get(agent.name),
                                                      job.driving.get(agent.name))
            if agent.name in result.failed or agent.name in self.fenced:
                self.plans[agent.name].failed = True
                self.plans[agent.name].legs = []
        self._dependencies()
        keep_routes = {leg.route for plan in self.plans.values() for leg in plan.legs}
        keep_pieces = {entry.piece for plan in self.plans.values() for entry in plan.entries}
        keep_pieces.update(piece for pieces in self.coast_route.values() for piece in pieces)
        for wall in job.walls:
            keep_routes.add(wall.starts[0][0])
            keep_pieces.update(wall.blocks)
        if self.graph.prune(keep_routes, keep_pieces):
            self.planner.forget()
        for robot, plan in self.plans.items():
            leg = self.active.get(robot) or self.pending.get(robot)
            goal = plan.legs[-1].target if plan.legs else (plan.entries[0].point if plan.entries else None)
            if leg is not None and goal is not None and (not plan.legs or not plan.legs[-1].aside) \
                    and goal in self.graph.points and self.graph.points[goal].zone == leg.destination:
                point = self.graph.points[goal]
                if point.kind == "slot":
                    if point.slot == 1:
                        self.slot_choice.pop(leg, None)
                    else:
                        self.slot_choice[leg] = point.slot
        reason = job.reason
        self.plan_info = {"at": job.at, "ms": round(result.elapsed_ms, 1), "cost": round(result.cost, 2),
                          "method": result.method, "expansions": result.expansions, "reason": reason,
                          "fallback": result.method != "JOINT", "ok": result.ok}
        summary = {r: [self.endpoint_label(leg.target) + (" (비켜서기)" if leg.aside else "") for leg in p.legs]
                   for r, p in self.plans.items()}
        text = f"다시 계획 · {reason} · {result.elapsed_ms:.0f} ms"
        if result.method != "JOINT":
            text += f" · Level 순 계획으로 대신 ({result.reason})"
        if self.fenced:
            text += " · 현장 확인 필요 칸 " + "·".join(dict.fromkeys(
                self.resource_label(n) for names in self.fenced.values() for n in names))
        self.record("PLAN_UPDATE", now, text, reason_code=reason, plan_ms=round(result.elapsed_ms, 1),
                    cost=round(result.cost, 2), method=result.method, fallback=result.method != "JOINT",
                    expansions=result.expansions, plans=summary,
                    unknown={r: list(names) for r, names in self.fenced.items()})

    def replan(self, now, reason):
        """Plan again within this call (the planning thread does the same in three steps, plan_runner)."""
        self.apply_job(self.run_job(self.make_job(now, reason)), now)

    def _robot_plan(self, robot, events, goal_targets, old, driving_to):
        """``driving_to``: endpoint of the MOVE the robot drove when the job was made (None: not driving). The plan
        starts from that moment; a robot that has arrived since is seen by the executor in the next tick."""
        graph, planner = self.graph, self.planner
        legs, entries = [], []
        last_key = None
        for t, state in events:
            rid, idx, _ = state
            route = graph.routes[rid]
            standing = idx >= len(route.pieces)
            if route.pieces and (not legs or legs[-1].route != rid):
                legs.append(PlanLeg(rid, route.target, route.target not in goal_targets, route.line, route.length))
            leg_no = len(legs) - 1 if route.pieces else (len(legs) - 1 if legs else -1)
            piece = planner.piece_of(state)
            key = (rid, idx if not standing else "stand")
            if key == last_key:
                continue
            last_key = key
            if standing:
                s0 = s1 = route.length if route.pieces else 0.0
            else:
                s0 = route.starts[idx]
                s1 = route.starts[idx + 1] if idx + 1 < len(route.pieces) else route.length
            if entries:
                entries[-1].t1 = t
            entries.append(Entry(piece, graph.pieces[piece].point, leg_no, s0, s1, t, 10 ** 9, standing))
        plan = RobotPlan(legs, entries)
        driving = driving_to is not None
        if old is not None and old.started and legs and old.legs and old.legs[0].target == legs[0].target \
                and driving:
            plan.started = True
        if driving and legs and driving_to == legs[0].target:
            plan.started = True
        return plan

    def _dependencies(self):
        """For every conflicting pair of positions, the one planned second waits for the one planned first; the
        dependency carries the kind of the conflict (plan_exec.ORDER_KEPT)."""
        conflict = self.graph.conflict
        robots = list(self.plans)
        for robot in robots:
            self.plans[robot].deps = {}
        for a in robots:
            for b in robots:
                if a == b:
                    continue
                pa, pb = self.plans[a], self.plans[b]
                for j, eb in enumerate(pb.entries):
                    best = None
                    for i, ea in enumerate(pa.entries):
                        if ea.t1 <= eb.t0 and conflict(ea.piece, eb.piece):
                            best = i
                    if best is not None:
                        pb.deps.setdefault(j, []).append(
                            Dep(a, best, conflict_kind(pa.entries[best:], pb.entries[j:])))
