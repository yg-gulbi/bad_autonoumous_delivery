"""Stage 4O: plan the robots together with space-time A* over (robot A position, robot B position, time).

Positions come from ``plan_graph``: a robot is either inside one piece of the route it drives (moving) or
standing at the end of a route. In each plan step (``plan_step_s``) a robot moves on along its route,
waits, or - standing at the end of a route - starts a new route toward another slot (a MOVE). Two
positions that conflict in the graph are never occupied at the same time, and a change of position is
also checked against the other robot's previous position, so robots never pass through each other.

While every moving robot is inside a piece nothing can be decided, so the search jumps to the next time
a robot reaches the end of a piece (or a fixed trajectory changes). A robot that waits, waits until that
next change. Time stays in the state; plans are compared by (position, position) with earlier-and-
cheaper dominance.

Cost = sum of ``level_weights[Level]`` x arrival time at a goal (the last arrival if a robot leaves its
goal again). A robot without a mission has its current spot as goal with ``idle_weight``. Ties prefer
slot 1 over slot 2 and fewer route changes. Nothing here knows about corridors, rooms or zone names.

With more than two robots, or when the joint search runs past ``plan_limit_ms`` (200 ms; the plan runs inside
the 10 Hz tick), robots are planned one at a time in Level order (prioritized planning) against the
trajectories already fixed. That fallback has its own ``plan_limit_ms``.
"""

import heapq
import time
from dataclasses import dataclass, field
from itertools import product
from .clearance import leaving

SLOT2_PENALTY = 1e-3      # tie-break: same cost -> slot 1 (the current rule's preference)
ROUTE_PENALTY = 1e-4      # tie-break: same cost -> fewer route changes (fewer MOVE commands)


@dataclass
class Agent:
    name: str
    weight: float
    starts: list                    # [(route_id, forced_until)] options at t=0; index 0 = keep current
    goals: dict                     # standing piece id -> tie penalty
    targets: tuple = ()             # endpoints a new route may head to when the plan starts (goals + aside)
    goal_targets: tuple = ()        # endpoints of the goals: the only new routes after the plan start
    free_routes: dict = field(default_factory=dict)   # standing route id -> new route ids (free pose)
    start_index: int = 0            # piece index on the first start route
    start_left: int | None = None   # steps left in that piece (None = whole piece)
    static: bool = False            # cannot move (offline, safety stop): fixed obstacle
    blocks: tuple = ()              # extra positions a static robot may still be in (link lost while driving)
    _dists: dict | None = None
    _hcache: dict | None = None


@dataclass
class Plan:
    ok: bool
    events: dict                    # agent -> [(t, (route, index, left))] at every decision time
    cost: float
    expansions: int
    elapsed_ms: float
    method: str                     # JOINT | PRIORITIZED
    reason: str = ""
    end: int = 0                    # last event time
    failed: tuple = ()              # agents without a route in a Level-order plan (they keep still)


class Planner:
    def __init__(self, graph, *, horizon_steps=1200):
        self.graph = graph
        self.horizon = horizon_steps
        self._goal_dist: dict = {}

    def forget(self):
        """Drop cached goal distances (after the graph forgot old pieces)."""
        self._goal_dist.clear()

    def route(self, route_id):
        return self.graph.routes[route_id]

    def piece_of(self, state):
        route = self.graph.routes[state[0]]
        return route.pieces[state[1]] if state[1] < len(route.pieces) else route.park

    def standing(self, state):
        return state[1] >= len(self.graph.routes[state[0]].pieces)

    @staticmethod
    def at(events, t):
        """State of one agent at plan step t from its event list."""
        state = events[0][1]
        for et, st in events:
            if et > t:
                break
            state = st
        return state

    # --- heuristic ------------------------------------------------------------------------------

    def _endpoint_dist(self, goals):
        """Steps from every endpoint to the nearest goal standing piece (Dijkstra over whole routes)."""
        key = tuple(sorted(goals))
        found = self._goal_dist.get(key)
        if found is not None:
            return found
        graph = self.graph
        best, heap = {}, []
        for pid in goals:
            name = graph.pieces[pid].point
            if graph.park.get(name) == pid:
                best[name] = 0
                heap.append((0, name))
        heapq.heapify(heap)
        while heap:
            d, name = heapq.heappop(heap)
            if d > best.get(name, float("inf")):
                continue
            for other in graph.endpoints:
                rid = graph.from_endpoint.get(other, {}).get(name)
                if rid is None:
                    continue
                nd = d + graph.route_steps(rid)
                if nd < best.get(other, float("inf")):
                    best[other] = nd
                    heapq.heappush(heap, (nd, other))
        self._goal_dist[key] = best
        return best

    def _h(self, agent, state, done):
        if done:
            return 0
        if agent._dists is None:
            agent._dists = self._endpoint_dist(agent.goals)
            agent._hcache = {}
        found = agent._hcache.get(state)
        if found is None:
            found = agent._hcache[state] = self._h_raw(agent, state, agent._dists)
        return found

    def _h_raw(self, agent, state, dists):
        rid, idx, left = state
        route = self.graph.routes[rid]
        if idx < len(route.pieces):
            return self.graph.route_steps(rid, idx, left) + dists.get(route.target, 10 ** 6)
        options = agent.free_routes.get(rid)
        if options is None:
            return dists.get(route.target, 10 ** 6)
        return min((self.graph.route_steps(r) + dists.get(self.graph.routes[r].target, 10 ** 6) for r in options),
                   default=10 ** 6)

    # --- moves ----------------------------------------------------------------------------------

    def _moves(self, agent, state, forced, t):
        """One agent's choices: [(state after choosing, moving, new route)].

        A moving choice has ``left`` >= 1 steps to the end of its piece; the joint step then advances
        every moving agent by the smallest ``left`` among them. Standing at the end of a route, a robot
        may start a route to any allowed slot (a move aside) when the plan starts or while it stands in
        one of its goals; otherwise it only starts toward its goals. Every replan is a new start.
        """
        if agent.static:
            return ((state, False, False),)
        rid, idx, left = state
        route = self.graph.routes[rid]
        n = len(route.pieces)
        if idx < n:
            if left > 0:
                return ((state, True, False),)
            nxt = ((rid, idx + 1, self.graph.pieces[route.pieces[idx + 1]].steps), True, False)
            if forced is not None and rid == forced[0] and idx < forced[1]:
                return (nxt,)
            return (nxt, (state, False, False))
        moves = [(state, False, False)]
        # Aside routes when the plan starts, or from a goal the robot stands in (to let the other out).
        at_goal = route.park in agent.goals
        targets = agent.targets if t == 0 or at_goal else agent.goal_targets
        options = agent.free_routes.get(rid)
        if options is None:
            table = self.graph.from_endpoint.get(route.target, {})
            options = [table[n] for n in targets if n in table]
        elif t > 0 and not at_goal:
            options = [r for r in options if self.graph.routes[r].target in agent.goal_targets]
        start = self.graph.start_steps          # a new MOVE does not move the robot at once
        for new in options:
            first = self.graph.routes[new].pieces
            if first:
                moves.append(((new, 0, self.graph.pieces[first[0]].steps + start), True, True))
        return moves

    # --- entry points ---------------------------------------------------------------------------

    def plan(self, agents, *, limit_ms=200.0):
        """Joint plan for up to two moving agents; more agents or a timeout fall back to Level order."""
        start = time.perf_counter()
        movers = [a for a in agents if not a.static]
        if len(movers) <= 2:
            result = self._search(agents, {}, start + limit_ms / 1000.0)
            result.elapsed_ms = (time.perf_counter() - start) * 1000.0
            if result.ok:
                return result
            reason = result.reason
        else:
            reason = "로봇 3대 이상"
        fallback = self.prioritized(agents, limit_ms=limit_ms)
        fallback.elapsed_ms = (time.perf_counter() - start) * 1000.0
        fallback.reason = reason
        return fallback

    def reachable(self, agent, walls, *, limit_ms=50.0):
        """Can the agent reach a goal with only the fixed obstacles ``walls`` (no other robot)? True, False when
        no way exists, None when the search ran out of time (not known)."""
        result = self._search([agent, *walls], {}, time.perf_counter() + limit_ms / 1000.0)
        if result.ok:
            return True
        return None if result.reason == "계획 시간 초과" else False

    def prioritized(self, agents, *, limit_ms=200.0):
        """Plan one agent at a time in descending weight against the trajectories already fixed.

        An agent left without a route keeps still. The higher agents were planned as if it could move, so
        the round is planned again with it as a fixed obstacle until no new agent fails. If the Level
        order leaves an agent without a route, the reverse order is tried and the order with fewer
        agents left standing (then lower cost) is used.
        """
        deadline = time.perf_counter() + limit_ms / 1000.0
        movers = sorted((a for a in agents if not a.static), key=lambda a: -a.weight)
        best = None
        for order in (movers, movers[::-1]) if len(movers) > 1 else (movers,):
            result = self._prioritized_order(agents, order, deadline)
            if best is None or (len(result.failed), result.cost) < (len(best.failed), best.cost):
                best = result
            if not best.failed or time.perf_counter() > deadline:
                break
        return best

    def _prioritized_order(self, agents, ranking, deadline):
        failed, expansions = [], 0
        while True:
            still = [a for a in agents if a.static or a.name in failed]
            order = [a for a in ranking if a.name not in failed]
            frozen = [Agent(a.name, a.weight, a.starts[:1], a.goals, start_index=a.start_index,
                            start_left=a.start_left, static=True, blocks=a.blocks) for a in still]
            events, cost, new_failure = {}, 0.0, None
            for agent in order:
                budget = max(0.05, (deadline - time.perf_counter()) / max(1, len(order)))
                res = self._search([agent, *frozen], dict(events), time.perf_counter() + budget)
                expansions += res.expansions
                if not res.ok:
                    new_failure = agent.name
                    break
                events[agent.name] = res.events[agent.name]
                cost += res.cost
            if new_failure is None:
                break
            failed.append(new_failure)
        for agent in agents:
            if agent.name in failed:
                events[agent.name] = [(0, self.initial_state(agent))]
        end = max((line[-1][0] for line in events.values()), default=0)
        return Plan(not failed, events, cost, expansions, 0.0, "PRIORITIZED", end=end, failed=tuple(failed))

    def initial_state(self, agent, option=0):
        rid = agent.starts[option][0]
        route = self.graph.routes[rid]
        idx, left = (agent.start_index, agent.start_left) if option == 0 else (0, None)
        if idx < len(route.pieces):
            steps = self.graph.pieces[route.pieces[idx]].steps
            if option > 0:
                steps += self.graph.start_steps     # CANCEL, then the new MOVE
            return rid, idx, steps if left is None else left
        return rid, len(route.pieces), 0

    # --- search ---------------------------------------------------------------------------------

    def _search(self, agents, scripted, deadline):
        graph = self.graph
        conflict, distance, piece_of, close = graph.conflict, graph.distance, self.piece_of, graph.close
        free = [a for a in agents if not a.static and a.name not in scripted]
        statics = [piece_of(self.initial_state(a)) for a in agents if a.static]
        statics += [p for a in agents if a.static for p in a.blocks]
        # A robot that may still drive on (link lost while driving, CANCEL not confirmed) is not standing: being
        # close to it is no start the grace applies to (random seed 205 of 4O 후속 6).
        coasting = {piece_of(self.initial_state(a)) for a in agents if a.static and a.blocks} | {
            p for a in agents if a.static for p in a.blocks}

        def close_to(p, o):
            return conflict(p, o) if o in coasting else close(p, o)
        script_lines = list(scripted.values())
        changes = sorted({t for line in script_lines for t, _ in line[1:]})
        script_end = changes[-1] if changes else 0
        k = len(free)
        if k == 0:
            return Plan(True, {}, 0.0, 0, 0.0, "JOINT")
        weights = [a.weight for a in free]

        def obstacles(t):
            return statics + [piece_of(self.at(line, t)) for line in script_lines]

        def next_change(t):
            for c in changes:
                if c > t:
                    return c
            return None

        def done(i, state):
            return self.standing(state) and piece_of(state) in free[i].goals

        def heuristic(states, arrivals):
            return sum(weights[i] * self._h(free[i], states[i], arrivals[i] is not None) for i in range(k))

        def blocked(new_pieces, old_pieces, t_from, t_to, grace):
            """(conflict?, still in the initial overlap?) for a change of positions."""
            still = False
            obs_from, obs_to = obstacles(t_from), obstacles(t_to)
            for i in range(k):
                pi, oi = new_pieces[i], old_pieces[i]
                for j in range(i + 1, k):
                    pj, oj = new_pieces[j], old_pieces[j]
                    if pi == oi and pj == oj:
                        if grace and close(pi, pj):
                            still = True
                        continue
                    pairs = ((pi, pj), (oi, pj), (pi, oj)) if pi != oi and pj != oj else ((pi, pj),)
                    for a, b in pairs:
                        if conflict(a, b):
                            if grace and leaving(distance(oi, oj), distance(a, b)):
                                still = True
                                continue
                            return True, False
                    if grace and close(pi, pj):
                        still = True        # not in conflict now (both standing), but not apart yet
                for o_new, o_old in zip(obs_to, obs_from):
                    if pi == oi and o_new == o_old:
                        if grace and close_to(pi, o_new):
                            still = True
                        continue
                    for a, b in ((pi, o_new), (oi, o_new), (pi, o_old)):
                        if conflict(a, b):
                            if grace and leaving(distance(oi, o_old), distance(a, b)):
                                still = True
                                continue
                            return True, False
                    if grace and close_to(pi, o_new):
                        still = True
            return False, still

        heap, visited, nodes = [], {}, []
        counter = expansions = 0
        options = []
        for agent in free:
            opts = []
            for n, (rid, until) in enumerate(agent.starts):
                opts.append((self.initial_state(agent, n), (rid, until) if until else None, n > 0))
            options.append(opts)
        # Grace (the robots already overlap) comes from where they are now (each one's current route), not from
        # the first piece of a new route: that piece may run through the other robot and is a real conflict.
        now_pieces = [piece_of(opts[0][0]) for opts in options]
        overlap = (any(close(now_pieces[i], now_pieces[j]) for i in range(k) for j in range(i + 1, k))
                   or any(close_to(p, o) for p in now_pieces for o in obstacles(0)))
        for combo in product(*options):
            states = tuple(c[0] for c in combo)
            force = tuple(c[1] for c in combo)
            pieces = [piece_of(s) for s in states]
            grace = overlap and (any(close(pieces[i], pieces[j]) for i in range(k) for j in range(i + 1, k))
                                 or any(close_to(p, o) for p in pieces for o in obstacles(0)))
            arrivals = tuple(0.0 if done(i, states[i]) else None for i in range(k))
            extra = sum(free[i].goals.get(pieces[i], 0.0) for i in range(k) if arrivals[i] is not None)
            extra += ROUTE_PENALTY * sum(1 for c in combo if c[2])
            nodes.append((states, -1, 0, None))
            counter += 1
            h0 = heuristic(states, arrivals)
            heapq.heappush(heap, (extra + h0, h0, counter, extra, 0, len(nodes) - 1,
                                  states, arrivals, extra, force, grace))

        while heap:
            _, _, _, g, t, node_id, states, arrivals, extra, force, grace = heapq.heappop(heap)
            if all(a is not None for a in arrivals) and t >= script_end:
                return self._result(free, nodes, node_id, g, expansions)
            key = (states, force, grace) if t >= script_end else (states, force, grace, t)
            entries = visited.get(key)
            if entries is None:
                entries = visited[key] = []
            elif any(et <= t and ex <= extra + 1e-12
                     and all(b is None or (a is not None and a <= b) for a, b in zip(ea, arrivals))
                     for et, ea, ex in entries):
                continue
            entries.append((t, arrivals, extra))
            expansions += 1
            # Every expansion: one can take milliseconds while new pieces get their first conflict checks,
            # and the plan runs inside the 10 Hz tick.
            if time.perf_counter() > deadline:
                return Plan(False, {}, 0.0, expansions, 0.0, "JOINT", "계획 시간 초과")
            if t >= self.horizon:
                continue
            old_pieces = [piece_of(s) for s in states]
            event = next_change(t)
            for combo in product(*(self._moves(free[i], states[i], force[i], t) for i in range(k))):
                lefts = [c[0][2] for c in combo if c[1]]
                if lefts:
                    step = min(lefts) if event is None else min(min(lefts), event - t)
                elif event is not None:
                    step = event - t
                elif any(c[0] != states[i] for i, c in enumerate(combo)):
                    step = 0           # arrival at the end of a route: becomes a standing position
                else:
                    continue           # nobody moves and nothing around changes
                new_pieces = [piece_of(c[0]) for c in combo]
                hit, still = blocked(new_pieces, old_pieces, t, t + step, grace)
                if hit:
                    continue
                nt = t + step
                new_states, new_arrivals, new_extra = [], [], extra
                for i, c in enumerate(combo):
                    rid, idx, left = c[0]
                    if c[1]:
                        left -= step
                        route = graph.routes[rid]
                        if left == 0 and idx + 1 >= len(route.pieces):
                            # Standing at the end of a route is one state whichever route led there.
                            rid, idx, left = graph.stay_route(route.target), 0, 0
                    if c[2]:
                        new_extra += ROUTE_PENALTY
                    st = (rid, idx, left)
                    new_states.append(st)
                    if done(i, st):
                        if arrivals[i] is not None and self.standing(states[i]) and piece_of(states[i]) == piece_of(st):
                            new_arrivals.append(arrivals[i])
                        else:
                            new_arrivals.append(float(nt))
                            new_extra += free[i].goals.get(piece_of(st), 0.0)
                    else:
                        new_arrivals.append(None)
                new_states, new_arrivals = tuple(new_states), tuple(new_arrivals)
                if new_states == states and step == 0:
                    continue
                final_pieces = [piece_of(s) for s in new_states]
                if final_pieces != new_pieces:
                    # Arriving at the end of a route turns the last piece into a standing position.
                    hit, _ = blocked(final_pieces, new_pieces, nt, nt, grace)
                    if hit:
                        continue
                ng = new_extra + sum(weights[i] * (new_arrivals[i] if new_arrivals[i] is not None else nt)
                                     for i in range(k))
                new_force = tuple(force[i] if force[i] is not None and new_states[i][0] == force[i][0]
                                  and new_states[i][1] < force[i][1] else None for i in range(k))
                nodes.append((new_states, node_id, nt, (t, tuple(c[0] for c in combo))))
                counter += 1
                nh = heuristic(new_states, new_arrivals)
                heapq.heappush(heap, (ng + nh, nh, counter, ng, nt, len(nodes) - 1,
                                      new_states, new_arrivals, new_extra, new_force, grace and still))
        return Plan(False, {}, 0.0, expansions, 0.0, "JOINT", "경로 없음")

    def _result(self, free, nodes, node_id, cost, expansions):
        """Events per agent: the state chosen at each decision (a piece is entered then) and the state the
        jump ends in (end of a piece, or standing at the end of a route)."""
        chain = []
        while node_id != -1:
            states, parent, t, chosen = nodes[node_id]
            chain.append((t, states))
            if chosen is not None:
                chain.append(chosen)
            node_id = parent
        chain.reverse()
        events = {}
        for i, agent in enumerate(free):
            line = []
            for t, states in chain:
                if line and line[-1][0] == t:
                    line[-1] = (t, states[i])
                elif not line or line[-1][1] != states[i]:
                    line.append((t, states[i]))
            events[agent.name] = line
        return Plan(True, events, cost, expansions, 0.0, "JOINT", end=chain[-1][0])
