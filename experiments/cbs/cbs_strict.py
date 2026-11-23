"""Experimental CBS (Conflict-Based Search) on the same PlanGraph and conflict rule as the Joint A* planner.

Low level: the existing single-agent space-time search (Planner._search with one free agent). A CBS constraint
"agent X must not be where it conflicts with piece P during steps [t0, t1]" is given to that search as a scripted
phantom obstacle that stands at P during [t0, t1] and far away otherwise, so the conflict rule is exactly the
one the joint planner uses (graph.conflict, swap check against the previous step).

High level: best-first over constraint trees, cost = sum of agent costs (each = Level weight x arrival + ties).
"""

import heapq
import time
from itertools import count

from part3_fleet_manager.core.planner import Plan


class CBS:
    def __init__(self, planner):
        self.P = planner
        self.G = planner.graph
        far = self.G._piece("CBS_FAR", ((100.0, 100.0),), standing=True)
        self.far_state = (self.G.stay_route(piece=far), 0, 0)
        self._stay = {}

    def _state_at(self, piece):
        rid = self._stay.get(piece)
        if rid is None:
            rid = self._stay[piece] = self.G.stay_route(piece=piece)
        return (rid, 0, 0)

    def _phantom(self, piece, t0, t1):
        line = [] if t0 == 0 else [(0, self.far_state)]
        line.append((t0, self._state_at(piece)))
        line.append((t1 + 1, self.far_state))
        return line

    def _low(self, agent, statics, constraints, deadline):
        scripted = {f"cbs{i}": self._phantom(*c) for i, c in enumerate(constraints)}
        return self.P._search([agent, *statics], scripted, deadline)

    def _steps(self, events, end):
        return [self.P.piece_of(self.P.at(events, t)) for t in range(end + 1)]

    def _moved(self, before, after):
        """A change of position as the joint search sees it: arriving (last piece -> standing at the same point)
        is not one."""
        if before == after:
            return False
        pa, pb = self.G.pieces[before], self.G.pieces[after]
        return not (pb.standing and pa.point == pb.point)

    def _moved(self, before, after):
        """A change of position as the joint search sees it: arriving (last piece -> standing at the same point)
        is not one."""
        if before == after:
            return False
        pa, pb = self.G.pieces[before], self.G.pieces[after]
        return not (pb.standing and pa.point == pb.point)

    def first_conflict(self, events):
        """First conflict by the joint search's own rule: positions in conflict at the same step, and when both
        robots change position also each new position against the other's previous one (no passing through).
        Range constraints: each agent is kept off the other's position for as long as the other stays there."""
        a, b = list(events)
        end = max(line[-1][0] for line in events.values()) + 1
        sa, sb = self._steps(events[a], end), self._steps(events[b], end)
        conflict = self.G.conflict

        def stay(seq, t):
            e = t
            while e + 1 <= end and seq[e + 1] == seq[t]:
                e += 1
            return e
        for t in range(1, end + 1):
            pa, pb, qa, qb = sa[t], sb[t], sa[t - 1], sb[t - 1]
            if conflict(pa, pb):
                return t, ((a, pb, t, stay(sb, t)), (b, pa, t, stay(sa, t)))
            if self._moved(qa, pa) and self._moved(qb, pb):
                if conflict(qa, pb):
                    return t, ((a, pb, t - 1, stay(sb, t)), (b, qa, t - 1, t))
                if conflict(pa, qb):
                    return t, ((a, qb, t - 1, t), (b, pa, t - 1, stay(sa, t)))
        return None

    def plan(self, agents, *, limit_ms=200.0, max_nodes=10000):
        start = time.perf_counter()
        deadline = start + limit_ms / 1000.0
        movers = [a for a in agents if not a.static]
        statics = [a for a in agents if a.static]
        if len(movers) != 2:
            return Plan(False, {}, 0.0, 0, 0.0, "CBS-S", "로봇 2대만")
        expansions = 0
        tie = count()
        root_cons = {a.name: () for a in movers}
        root_paths, root_costs = {}, {}
        for agent in movers:
            res = self._low(agent, statics, (), deadline)
            expansions += res.expansions
            if not res.ok:
                return self._fail(res.reason, expansions, start)
            root_paths[agent.name] = res.events[agent.name]
            root_costs[agent.name] = res.cost
        heap = [(sum(root_costs.values()), next(tie), root_cons, root_paths, root_costs)]
        nodes = 0
        while heap:
            if time.perf_counter() > deadline:
                return self._fail("계획 시간 초과", expansions, start, nodes)
            cost, _, cons, paths, costs = heapq.heappop(heap)
            nodes += 1
            if nodes > max_nodes:
                return self._fail("노드 한도", expansions, start, nodes)
            found = self.first_conflict(paths)
            if found is None:
                end = max(line[-1][0] for line in paths.values())
                plan = Plan(True, paths, cost, expansions, (time.perf_counter() - start) * 1000.0, "CBS-S", end=end)
                plan.ct_nodes = nodes
                return plan
            _, branches = found
            for name, piece, t0, t1 in branches:
                agent = next(a for a in movers if a.name == name)
                new_cons = dict(cons)
                new_cons[name] = cons[name] + ((piece, t0, t1),)
                res = self._low(agent, statics, new_cons[name], deadline)
                expansions += res.expansions
                if not res.ok:
                    continue
                new_paths = dict(paths)
                new_paths[name] = res.events[name]
                new_costs = dict(costs)
                new_costs[name] = res.cost
                heapq.heappush(heap, (sum(new_costs.values()), next(tie), new_cons, new_paths, new_costs))
        return self._fail("경로 없음", expansions, start, nodes)

    def _fail(self, reason, expansions, start, nodes=0):
        plan = Plan(False, {}, 0.0, expansions, (time.perf_counter() - start) * 1000.0, "CBS-S", reason)
        plan.ct_nodes = nodes
        return plan
