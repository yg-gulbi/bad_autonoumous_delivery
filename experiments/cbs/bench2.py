"""Joint A* vs CBS on the same instances: every ordered slot pair heading into each other's zone x Level pairs
(the 1,080 cases of test_plan_time_for_every_slot_pair_and_level_pair), plus the corridor scenes of test_4o_plan."""

import json
import statistics
import sys
import time
from dataclasses import replace
from pathlib import Path

REPO = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/cbs")
sys.path[:0] = [str(REPO / "src/part3_fleet_manager"), str(REPO / "src/part3_fleet_manager/test"),
                str(Path(__file__).parent)]

from part3_fleet_manager.core.config import load_config      # noqa: E402
from test_4o_plan import Kit                                   # noqa: E402
import importlib
CBS = importlib.import_module(sys.argv[4] if len(sys.argv) > 4 else 'cbs').CBS                                            # noqa: E402

LIMIT_MS = float(sys.argv[2]) if len(sys.argv) > 2 else 200.0
ONLY = int(sys.argv[3]) if len(sys.argv) > 3 else 0

cfg = load_config(REPO / "src/part3_fleet_manager/config/fleet.yaml")
kit = Kit(replace(cfg, traffic=replace(cfg.traffic)))
cbs = CBS(kit.P)


def cases():
    for a in kit.slots:
        for b in kit.slots:
            za, zb = kit.G.endpoints[a].zone, kit.G.endpoints[b].zone
            if a == b or za == zb:
                continue
            for la in (1, 2, 3):
                for lb in (1, 2, 3):
                    yield f"{a}->{zb} L{la} / {b}->{za} L{lb}", lambda a=a, b=b, za=za, zb=zb, la=la, lb=lb: [
                        kit.standing("p", a, zb, la), kit.standing("q", b, za, lb)]


def clean(plan):
    return plan.ok and cbs.first_conflict(plan.events) is None


import cbs as _loose
loose = _loose.CBS(kit.P)


def loose_clean(plan):
    return plan.ok and loose.first_conflict(plan.events) is None


def run(fn, agents_fn):
    kit.P.forget()
    start = time.perf_counter()
    plan = fn(agents_fn())
    return plan, (time.perf_counter() - start) * 1000.0


rows = []
for i, (name, agents_fn) in enumerate(cases()):
    if ONLY and i >= ONLY:
        break
    joint, tj = run(lambda ag: kit.P.plan(ag, limit_ms=LIMIT_MS), agents_fn)
    opt, _ = run(lambda ag: kit.P._search(ag, {}, time.perf_counter() + 5.0), agents_fn)
    c, tc = run(lambda ag: cbs.plan(ag, limit_ms=LIMIT_MS), agents_fn)
    rows.append({
        "case": name,
        "joint_method": joint.method, "joint_ok": loose_clean(joint), "joint_strict": clean(joint), "joint_ms": tj, "joint_cost": joint.cost,
        "opt_ok": opt.ok, "opt_cost": opt.cost,
        "cbs_ok": c.ok, "cbs_clean": clean(c) if c.ok else False, "cbs_loose": loose_clean(c) if c.ok else False, "cbs_ms": tc, "cbs_cost": c.cost,
        "cbs_nodes": getattr(c, "ct_nodes", 0), "cbs_reason": c.reason,
    })


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else float("nan")


n = len(rows)
summary = {"cases": n, "limit_ms": LIMIT_MS}
for key in ("joint", "cbs"):
    ms = [r[f"{key}_ms"] for r in rows]
    summary[key] = {"mean_ms": round(statistics.mean(ms), 1), "p95_ms": round(pct(ms, 0.95), 1),
                    "max_ms": round(max(ms), 1)}
summary["joint"]["fallback_prioritized"] = sum(r["joint_method"] != "JOINT" for r in rows)
summary["joint"]["valid"] = sum(r["joint_ok"] for r in rows)
summary["cbs"]["solved"] = sum(r["cbs_ok"] for r in rows)
summary["cbs"]["valid_strict"] = sum(r["cbs_clean"] for r in rows)
summary["cbs"]["valid_test_rule"] = sum(r["cbs_loose"] for r in rows)
summary["joint"]["valid_strict_checker"] = sum(r["joint_strict"] for r in rows)
reasons = {}
for r in rows:
    if not r["cbs_ok"]:
        reasons[r["cbs_reason"]] = reasons.get(r["cbs_reason"], 0) + 1
summary["cbs"]["fail_reasons"] = reasons
summary["cbs"]["ct_nodes_mean"] = round(statistics.mean(r["cbs_nodes"] for r in rows), 1)
summary["cbs"]["ct_nodes_max"] = max(r["cbs_nodes"] for r in rows)
both = [r for r in rows if r["cbs_ok"] and r["opt_ok"]]
gap = [r["cbs_cost"] - r["opt_cost"] for r in both]
summary["cost_vs_optimal"] = {
    "compared": len(both),
    "cbs_equal": sum(abs(g) < 1e-6 for g in gap),
    "cbs_worse": sum(g > 1e-6 for g in gap),
    "cbs_better": sum(g < -1e-6 for g in gap),
    "joint_equal_opt": sum(abs(r["joint_cost"] - r["opt_cost"]) < 1e-6 for r in rows if r["opt_ok"]),
}
print(json.dumps(summary, ensure_ascii=False, indent=1))
out = Path(__file__).parent / f"bench_{sys.argv[4] if len(sys.argv) > 4 else 'cbs'}_{int(LIMIT_MS)}_{ONLY}.json"
out.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1))
