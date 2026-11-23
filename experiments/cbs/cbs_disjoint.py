"""Disjoint-splitting CBS on the same PlanGraph, low-level search (Planner._search) and strict conflict
criterion as cbs_strict.py.

Why cbs_strict.py loses optimality (up to +38.6% on 316/1,052 cases at 200 ms):
1. Conflict-set negative constraints on both branches ("a avoids everything conflicting with pb" vs "b avoids
   everything conflicting with pa") are non-exhaustive: valid non-conflicting alternatives (pa', pb') such as
   (B1 standing, B2 moving) or (C1 moving, F2 standing) conflict with pb and pa respectively, so BOTH branches
   prune them.
2. Full stay-range constraints [t, stay] on both branches cut off shifted timings where one agent leaves or
   arrives slightly earlier/later.

Disjoint splitting (Li et al., IJCAI 2019) fixes this while keeping the exact same Planner._search low level
and cbs_strict.CBS.first_conflict rule:
- Pick a conflict under the exact same strict rule (longest moving-moving overlap first so corridor head-ons
  do not micro-step by 2 steps on doors) and pick one split agent `a` (moving before standing, higher Level
  weight first, then longer stay).
- Positive branch (on `a`): commit `a` to the point/segment sequence it traversed up to `t_commit` (keeping
  same-corridor goal alternatives such as B1 vs B2 open when `a` later replans from `t_commit` via
  Planner._search) and add the implied conflict-set negative constraint ("avoid", pa, t, t_c1) to `b`
  (standard phantom at `pa`).
- Negative branch (on `a`): add a negative constraint ("forbid", pa, t_star, t_star) to `a` at the overlap end
  `t_star`, implemented via a synthetic far phantom:
  - On narrow capacity segments (`graph.capacity`: corridor cells, crossroad, doors), it blocks all pieces
    sharing `(point, standing, capacity_segs)` so equivalent route polylines through the same 1-lane bottleneck
    in the same direction are pruned in one split while standing pieces and other slots remain open.
  - On slots, halls, and rooms (no `graph.capacity` segment), it blocks only `pa` itself so unrelated exit
    directions across open regions or slots remain open.
"""

import heapq
import time
from dataclasses import replace
from itertools import count

from part3_fleet_manager.core.planner import Plan, ROUTE_PENALTY
import cbs_strict


def _merge_constraints(cons):
    """Merge overlapping or adjacent constraints of the same (kind, piece) to keep scripted obstacles compact."""
    by_kp = {}
    for kind, piece, t0, t1 in cons:
        by_kp.setdefault((kind, piece), []).append((t0, t1))
    out = []
    for (kind, piece), spans in sorted(by_kp.items()):
        spans.sort()
        cur0, cur1 = spans[0]
        for a, b in spans[1:]:
            if a <= cur1 + 1:
                cur1 = max(cur1, b)
            else:
                out.append((kind, piece, cur0, cur1))
                cur0, cur1 = a, b
        out.append((kind, piece, cur0, cur1))
    return tuple(out)


class CBS(cbs_strict.CBS):
    """Disjoint-splitting CBS on the exact PlanGraph, Planner._search low level, and cbs_strict conflict rule."""

    def __init__(self, planner):
        super().__init__(planner)
        self._neg_phantoms = {}
        self._neg_synced = {}

    def _piece_key(self, piece_id):
        """Equivalence key for negative phantoms: on narrow capacity segments (``graph.capacity``: corridor
        cells, crossroad, doors), group by ``(point, standing, capacity_segs)`` so all route polylines through
        the same 1-lane bottleneck in the same direction are pruned together. On slots, halls and rooms (where
        different exit directions have different geometric conflicts, e.g. A2->D1 vs A2->D2 or A1->D1 vs A1->A2),
        keep the exact ``piece_id``."""
        p = self.G.pieces[piece_id]
        cap_segs = tuple(sorted((k, v) for k, v in self.G.segs(piece_id).items() if k in self.G.capacity))
        if cap_segs:
            return ("cap", p.point, p.standing, cap_segs)
        return ("exact", piece_id)

    def _neg_piece(self, target_piece):
        """Synthetic far-away piece conflicting ONLY with pieces matching ``_piece_key(target_piece)``."""
        ekey = self._piece_key(target_piece)
        pid = self._neg_phantoms.get(ekey)
        if pid is None:
            idx = len(self._neg_phantoms)
            pid = self.G._piece(
                f"CBS_NEG_{idx}",
                ((100.0 + idx * 1e-3, 100.0),),
                standing=True,
            )
            self._neg_phantoms[ekey] = pid
            self._neg_synced[ekey] = 0
        if ekey[0] == "exact":
            key = (target_piece, pid) if target_piece < pid else (pid, target_piece)
            self.G._conflict[key] = True
            return pid
        synced = self._neg_synced[ekey]
        n_pieces = len(self.G.pieces)
        if synced < n_pieces:
            _, point, standing, cap_segs = ekey
            for qid, qp in list(self.G.pieces.items()):
                if qid == pid or qp.point.startswith("CBS_"):
                    continue
                if (qp.point == point and qp.standing == standing
                        and tuple(sorted((k, v) for k, v in self.G.segs(qid).items() if k in self.G.capacity)) == cap_segs):
                    key = (qid, pid) if qid < pid else (pid, qid)
                    self.G._conflict[key] = True
            self._neg_synced[ekey] = n_pieces
        return pid

    def _phantom_constraint(self, kind, piece, t0, t1):
        p = self._neg_piece(piece) if kind == "forbid" else piece
        return self._phantom(p, t0, t1)

    def _pick_conflict(self, events):
        """Scan conflicts under the exact same strict rule as ``cbs_strict.CBS.first_conflict``.

        Returns None iff ``first_conflict(events)`` is None. Prefers moving-moving conflicts with the longest
        contiguous overlap (e.g. CROSSROAD/corridor cells before 2-step doors), breaking ties by earliest step.
        """
        a, b = list(events)
        end = max(line[-1][0] for line in events.values()) + 1
        sa, sb = self._steps(events[a], end), self._steps(events[b], end)
        conflict = self.G.conflict

        def stay(seq, t):
            e = t
            while e + 1 <= end and seq[e + 1] == seq[t]:
                e += 1
            return e

        best = None
        best_score = None
        t = 1
        while t <= end:
            pa, pb, qa, qb = sa[t], sb[t], sa[t - 1], sb[t - 1]
            if conflict(pa, pb):
                ea, eb = stay(sa, t), stay(sb, t)
                ov = min(ea, eb)
                pa_s, pb_s = self.G.pieces[pa].standing, self.G.pieces[pb].standing
                ov_len = (ov - t + 1) if (not pa_s or not pb_s) else 1
                both_moving = 1 if (not pa_s and not pb_s) else 0
                score = (both_moving, ov_len, -t)
                if best_score is None or score > best_score:
                    best_score = score
                    best = ("vertex", t, a, pa, ea, b, pb, eb)
                t = ov + 1
                continue
            if self._moved(qa, pa) and self._moved(qb, pb):
                det = None
                if conflict(qa, pb):
                    det = ("edge", t, a, qa, t, b, pb, stay(sb, t))
                elif conflict(pa, qb):
                    det = ("edge", t, b, qb, t, a, pa, stay(sa, t))
                if det is not None:
                    score = (0, 2, -t)
                    if best_score is None or score > best_score:
                        best_score = score
                        best = det
            t += 1
        return best

    def _matching_prefixes(self, agent, prefix_events, t_cut):
        """Return candidate (prefix_events_alt, rid, idx, rem) at ``t_cut`` matching the committed route's
        point-and-step prefix up to ``t_cut`` (so e.g. E2->B1 and E2->B2, which share E2..CROSSROAD..ROOM_B
        with identical step counts, are both kept open when replanning past ``t_cut``)."""
        st = self.P.at(prefix_events, t_cut)
        rid, idx, left = st
        st_t = 0
        for et, _ in prefix_events:
            if et > t_cut:
                break
            st_t = et
        route = self.G.routes[rid]
        if idx >= len(route.pieces):
            return [(prefix_events, rid, 0, None)]
        rem = max(0, left - (t_cut - st_t))
        if rem == 0 and idx + 1 >= len(route.pieces):
            return [(prefix_events, self.G.stay_route(route.target), 0, 0)]

        out = [(prefix_events, rid, idx, rem)]
        # Look for sibling routes from the same start endpoint that follow the exact same points & durations
        # up through piece index `idx` (e.g. slot 1 vs slot 2 of the same goal zone).
        if route.start is not None and route.start in self.G.from_endpoint:
            base_sig = [(self.G.pieces[p].point, self.G.pieces[p].steps) for p in route.pieces[:idx + 1]]
            for tgt in agent.goal_targets:
                alt_rid = self.G.from_endpoint[route.start].get(tgt)
                if alt_rid is None or alt_rid == rid:
                    continue
                alt_route = self.G.routes[alt_rid]
                if len(alt_route.pieces) <= idx:
                    continue
                alt_sig = [(self.G.pieces[p].point, self.G.pieces[p].steps) for p in alt_route.pieces[:idx + 1]]
                if alt_sig == base_sig:
                    alt_prefix = tuple(
                        (et, (alt_rid, s[1], s[2]) if s[0] == rid else s)
                        for et, s in prefix_events
                    )
                    out.append((alt_prefix, alt_rid, idx, rem))
        return out

    def _low_with_pos(self, agent, statics, constraints, pos_prefix, deadline, cache):
        key = (agent.name, constraints, pos_prefix[0] if pos_prefix else -1, pos_prefix[1] if pos_prefix else None)
        found = cache.get(key)
        if found is not None:
            return found
        res = self._low_uncached(agent, statics, constraints, pos_prefix, deadline)
        if res.ok or res.reason != "계획 시간 초과":
            cache[key] = res
        return res

    def _low_uncached(self, agent, statics, constraints, pos_prefix, deadline):
        if not pos_prefix:
            scripted = {f"cbs{i}": self._phantom_constraint(*c) for i, c in enumerate(constraints)}
            return self.P._search([agent, *statics], scripted, deadline)

        t_cut, prefix_events = pos_prefix
        shifted = {}
        for i, (kind, piece, t0, t1) in enumerate(constraints):
            if t1 < t_cut:
                continue
            shifted[f"cbs{i}"] = self._phantom_constraint(kind, piece, max(0, t0 - t_cut), t1 - t_cut)

        # Rank candidates by their O(1) lower bound and only try sibling routes (e.g. slot 2) when the primary
        # route failed or a constraint touches its diverging suffix (pieces after the shared prefix).
        matches = self._matching_prefixes(agent, prefix_events, t_cut)
        primary = matches[0]
        if len(matches) > 1:
            prim_route = self.G.routes[primary[1]]
            prim_idx = primary[2]
            suffix_pieces = list(prim_route.pieces[prim_idx + 1:])
            if prim_route.park is not None:
                suffix_pieces.append(prim_route.park)
            # Check if any constraint at t >= t_cut conflicts with the primary route's diverging suffix or park.
            suffix_hit = False
            for kind, piece, t0, t1 in constraints:
                if t1 < t_cut:
                    continue
                target = self._neg_piece(piece) if kind == "forbid" else piece
                if any(self.G.conflict(sp, target) for sp in suffix_pieces):
                    suffix_hit = True
                    break
            if not suffix_hit:
                matches = [primary]

        cands = []
        for cand_prefix, rid, idx, rem in matches:
            route = self.G.routes[rid]
            rem_steps = self.G.route_steps(rid, idx, rem) if idx < len(route.pieces) else 0
            goal_tie = agent.goals.get(route.park, 0.0) if route.park in agent.goals else 0.0
            lb = agent.weight * (t_cut + rem_steps) + goal_tie
            cands.append((lb, cand_prefix, rid, idx, rem))
        cands.sort(key=lambda x: x[0])

        best_plan = None
        total_exp = 0
        for lb, cand_prefix, rid, idx, rem in cands:
            if best_plan is not None and lb >= best_plan.cost - 1e-9:
                break
            seq = self._steps(cand_prefix, t_cut)
            violated = False
            for kind, piece, t0, t1 in constraints:
                if t0 <= t_cut:
                    target = self._neg_piece(piece) if kind == "forbid" else piece
                    lo, hi = max(0, t0), min(t_cut, t1)
                    for tau in range(lo, hi + 1):
                        if self.G.conflict(seq[tau], target):
                            violated = True
                            break
                    if violated:
                        break
            if violated:
                continue

            sub_agent = replace(agent, starts=[(rid, None)], start_index=idx, start_left=rem)
            res = self.P._search([sub_agent, *statics], shifted, deadline)
            total_exp += res.expansions
            if not res.ok:
                if res.reason == "계획 시간 초과":
                    return Plan(False, {}, 0.0, total_exp, res.elapsed_ms, "JOINT", "계획 시간 초과")
                continue

            stitched = [(et, s) for et, s in cand_prefix if et < t_cut]
            for et, s in res.events[agent.name]:
                t_abs = t_cut + et
                if stitched and stitched[-1][0] == t_abs:
                    stitched[-1] = (t_abs, s)
                elif not stitched or stitched[-1][1] != s:
                    stitched.append((t_abs, s))

            last_t = stitched[-1][0]
            last_piece = self.P.piece_of(stitched[-1][1])
            arr_t = last_t
            for et, s in reversed(stitched):
                if self.P.standing(s) and self.P.piece_of(s) == last_piece:
                    arr_t = et
                else:
                    break
            # Count new-route transitions exactly as Planner._search does (including a move started at t=0
            # from a standing initial start route, whose (0, stay) entry was overwritten by _result).
            start_rid = agent.starts[0][0]
            first_rid = stitched[0][1][0]
            routes_taken = 1 if (first_rid != start_rid and self.G.routes[first_rid].pieces) else 0
            prev_rid = first_rid
            for _, (r_id, _, _) in stitched[1:]:
                if r_id != prev_rid and self.G.routes[r_id].pieces:
                    routes_taken += 1
                prev_rid = r_id
            total_cost = agent.weight * arr_t + agent.goals.get(last_piece, 0.0) + ROUTE_PENALTY * routes_taken
            cand_plan = Plan(True, {agent.name: stitched}, total_cost, total_exp, res.elapsed_ms, "JOINT", end=last_t)
            if best_plan is None or cand_plan.cost < best_plan.cost:
                best_plan = cand_plan

        if best_plan is not None:
            best_plan.expansions = total_exp
            return best_plan
        return Plan(False, {}, 0.0, total_exp, 0.0, "JOINT", "경로 없음")

    def plan(self, agents, *, limit_ms=200.0, max_nodes=10000):
        start = time.perf_counter()
        deadline = start + limit_ms / 1000.0
        movers = [a for a in agents if not a.static]
        statics = [a for a in agents if a.static]
        if len(movers) != 2:
            return Plan(False, {}, 0.0, 0, 0.0, "CBS-D", "로봇 2대만")

        expansions = 0
        tie = count()
        cache = {}
        root_cons = {a.name: () for a in movers}
        root_pos = {a.name: None for a in movers}
        root_paths, root_costs = {}, {}
        for agent in movers:
            res = self._low_with_pos(agent, statics, (), None, deadline, cache)
            expansions += res.expansions
            if not res.ok:
                return self._fail(res.reason, expansions, start)
            root_paths[agent.name] = tuple(res.events[agent.name])
            root_costs[agent.name] = res.cost

        heap = [(sum(root_costs.values()), next(tie), root_cons, root_pos, root_paths, root_costs)]
        nodes = 0
        while heap:
            if time.perf_counter() > deadline:
                return self._fail("계획 시간 초과", expansions, start, nodes)
            cost, _, cons, pos, paths, costs = heapq.heappop(heap)
            nodes += 1
            if nodes > max_nodes:
                return self._fail("노드 한도", expansions, start, nodes)
            det = self._pick_conflict(paths)
            if det is None:
                end = max(line[-1][0] for line in paths.values())
                out_paths = {k: list(v) for k, v in paths.items()}
                plan = Plan(True, out_paths, cost, expansions, (time.perf_counter() - start) * 1000.0, "CBS-D", end=end)
                plan.ct_nodes = nodes
                return plan

            kind, t, a, pa, ea, b, pb, eb = det
            wa = next(m.weight for m in movers if m.name == a)
            wb = next(m.weight for m in movers if m.name == b)
            pa_s, pb_s = self.G.pieces[pa].standing, self.G.pieces[pb].standing
            # Split agent choice: moving before standing (never freeze a standing agent across the whole horizon),
            # higher Level weight first, then narrow capacity segment over open exact piece (prunes all equivalent
            # bottleneck routes in one negative split), then longer stay at this conflict.
            ca = 1 if self._piece_key(pa)[0] == "cap" else 0
            cb = 1 if self._piece_key(pb)[0] == "cap" else 0
            if (0 if pb_s else 1, wb, cb, eb - t) > (0 if pa_s else 1, wa, ca, ea - t):
                a, pa, ea, b, pb, eb = b, pb, eb, a, pa, ea
                pa_s, pb_s = pb_s, pa_s

            ov = min(ea, eb)
            max_end = max(line[-1][0] for line in paths.values())
            if kind == "edge":
                t_c0, t_c1 = t - 1, t
                t_neg0, t_neg1 = t - 1, t
                t_commit = t
            else:
                t_c0, t_c1 = t, (ov if pa_s else ea)
                t_star = ov if ov < max_end else t
                t_neg0, t_neg1 = t_star, t_star
                t_commit = min(t_c1, max_end)

            # Branch 1 (Positive on `a` + implied conflict-set negative on `b`).
            new_cons1 = dict(cons)
            new_cons1[b] = _merge_constraints(cons[b] + (("avoid", pa, t_c0, t_c1),))
            new_pos1 = dict(pos)
            if pos[a] is None or t_commit > pos[a][0]:
                new_pos1[a] = (t_commit, paths[a])
            ag_b = next(m for m in movers if m.name == b)
            res_b = self._low_with_pos(ag_b, statics, new_cons1[b], new_pos1[b], deadline, cache)
            expansions += res_b.expansions
            if res_b.ok:
                np1 = dict(paths)
                np1[b] = tuple(res_b.events[b])
                nc1 = dict(costs)
                nc1[b] = res_b.cost
                heapq.heappush(heap, (sum(nc1.values()), next(tie), new_cons1, new_pos1, np1, nc1))

            # Branch 2 (Negative on `a` at `t_neg0..t_neg1`).
            new_cons2 = dict(cons)
            new_cons2[a] = _merge_constraints(cons[a] + (("forbid", pa, t_neg0, t_neg1),))
            ag_a = next(m for m in movers if m.name == a)
            res_a = self._low_with_pos(ag_a, statics, new_cons2[a], pos[a], deadline, cache)
            expansions += res_a.expansions
            if res_a.ok:
                np2 = dict(paths)
                np2[a] = tuple(res_a.events[a])
                nc2 = dict(costs)
                nc2[a] = res_a.cost
                heapq.heappush(heap, (sum(nc2.values()), next(tie), new_cons2, pos, np2, nc2))

        return self._fail("경로 없음", expansions, start, nodes)

    def _fail(self, reason, expansions, start, nodes=0):
        plan = Plan(False, {}, 0.0, expansions, (time.perf_counter() - start) * 1000.0, "CBS-D", reason)
        plan.ct_nodes = nodes
        return plan
