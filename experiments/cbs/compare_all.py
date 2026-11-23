"""Unified benchmark comparing Joint A*, cbs, cbs_strict, and cbs_disjoint across all 1,080 instances
(ordered slot pairs across different zones x 9 Level pairs) at 200 ms and 1000 ms limits.

Usage:
    python3 experiments/cbs/compare_all.py [repo_path] [--limits 200,1000] [--raw-out /tmp/cbs_raw.json]
"""

import argparse
import json
import statistics
import sys
import time
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent


def setup_imports(repo: Path):
    sys.path[:0] = [
        str(repo / "src/part3_fleet_manager"),
        str(repo / "src/part3_fleet_manager/test"),
        str(HERE),
    ]


def pct(xs, q):
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def build_cases(kit):
    out = []
    for a in kit.slots:
        for b in kit.slots:
            za, zb = kit.G.endpoints[a].zone, kit.G.endpoints[b].zone
            if a == b or za == zb:
                continue
            for la in (1, 2, 3):
                for lb in (1, 2, 3):
                    out.append({
                        "case": f"{a}->{zb} L{la} / {b}->{za} L{lb}",
                        "a": a, "b": b, "za": za, "zb": zb,
                        "la": la, "lb": lb,
                        "has_l3": (la == 3 or lb == 3),
                        "l3_asym": (la == 3) != (lb == 3),
                        "l3_both": (la == 3 and lb == 3),
                    })
    return out


def make_agents(kit, spec):
    return [
        kit.standing("p", spec["a"], spec["zb"], spec["la"]),
        kit.standing("q", spec["b"], spec["za"], spec["lb"]),
    ]


def summarize_slice(rows, method):
    n = len(rows)
    ms = [r[method]["ms"] for r in rows]
    solved_rows = [r for r in rows if r[method]["ok"]]
    unsolved_rows = [r for r in rows if not r[method]["ok"]]
    both = [r for r in solved_rows if r["opt"]["ok"]]

    gaps_abs = [r[method]["cost"] - r["opt"]["cost"] for r in both]
    gaps_pct = [((r[method]["cost"] - r["opt"]["cost"]) / r["opt"]["cost"]) * 100.0 for r in both]
    worse_pct = [g for g, ga in zip(gaps_pct, gaps_abs) if ga > 1e-6]

    strict_both = [r for r in both if r["opt"]["valid_strict"]]
    strict_gaps_pct = [((r[method]["cost"] - r["opt"]["cost"]) / r["opt"]["cost"]) * 100.0 for r in strict_both]

    max_gap_case = None
    if both:
        worst = max(both, key=lambda r: (r[method]["cost"] - r["opt"]["cost"]) / r["opt"]["cost"])
        wg = ((worst[method]["cost"] - worst["opt"]["cost"]) / worst["opt"]["cost"]) * 100.0
        max_gap_case = {
            "case": worst["case"],
            "opt_cost": round(worst["opt"]["cost"], 4),
            "cost": round(worst[method]["cost"], 4),
            "gap_pct": round(wg, 2),
            "opt_valid_strict": worst["opt"]["valid_strict"],
        }

    reasons = {}
    for r in unsolved_rows:
        reason = r[method]["reason"] or "unknown"
        reasons[reason] = reasons.get(reason, 0) + 1

    ct_nodes = [r[method]["ct_nodes"] for r in rows]
    eq_cnt = sum(abs(g) < 1e-6 for g in gaps_abs)
    worse_cnt = sum(g > 1e-6 for g in gaps_abs)
    better_cnt = sum(g < -1e-6 for g in gaps_abs)

    return {
        "total": n,
        "mean_ms": round(statistics.mean(ms), 2) if ms else 0.0,
        "median_ms": round(statistics.median(ms), 2) if ms else 0.0,
        "p95_ms": round(pct(ms, 0.95), 2) if ms else 0.0,
        "max_ms": round(max(ms), 2) if ms else 0.0,
        "solved": len(solved_rows),
        "unsolved": len(unsolved_rows),
        "fallback_prioritized": sum(1 for r in rows if r[method]["method"] != "JOINT") if method == "joint" else 0,
        "valid_loose": sum(1 for r in rows if r[method]["valid_loose"]),
        "valid_strict": sum(1 for r in rows if r[method]["valid_strict"]),
        "fail_reasons": reasons,
        "ct_nodes_mean": round(statistics.mean(ct_nodes), 2) if ct_nodes else 0.0,
        "ct_nodes_max": max(ct_nodes) if ct_nodes else 0,
        "compared": len(both),
        "equal_opt_count": eq_cnt,
        "equal_opt_pct_solved": round(eq_cnt / len(both) * 100.0, 2) if both else 0.0,
        "equal_opt_pct_total": round(eq_cnt / n * 100.0, 2) if n else 0.0,
        "worse_count": worse_cnt,
        "better_count": better_cnt,
        "cost_diff_mean_pct": round(statistics.mean(gaps_pct), 2) if gaps_pct else 0.0,
        "cost_diff_worse_mean_pct": round(statistics.mean(worse_pct), 2) if worse_pct else 0.0,
        "cost_diff_max_pct": round(max(gaps_pct), 2) if gaps_pct else 0.0,
        "cost_diff_min_pct": round(min(gaps_pct), 2) if gaps_pct else 0.0,
        "cost_diff_mean_abs": round(statistics.mean(gaps_abs), 3) if gaps_abs else 0.0,
        "cost_diff_max_abs": round(max(gaps_abs), 3) if gaps_abs else 0.0,
        "strict_opt_compared": len(strict_both),
        "strict_opt_equal_pct": round(
            sum(abs(r[method]["cost"] - r["opt"]["cost"]) < 1e-6 for r in strict_both) / len(strict_both) * 100.0, 2
        ) if strict_both else 0.0,
        "strict_opt_mean_pct": round(statistics.mean(strict_gaps_pct), 2) if strict_gaps_pct else 0.0,
        "strict_opt_max_pct": round(max(strict_gaps_pct), 2) if strict_gaps_pct else 0.0,
        "max_gap_case": max_gap_case,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("repo", nargs="?", default=str(HERE.parent.parent))
    parser.add_argument("--limits", default="200,1000", help="Comma-separated limit_ms values")
    parser.add_argument("--only", type=int, default=0, help="Limit number of cases (0 = all 1,080)")
    parser.add_argument("--opt-limit-s", type=float, default=10.0)
    parser.add_argument("--raw-out", default="/tmp/cbs_compare_raw.json")
    parser.add_argument("--summary-out", default=str(HERE / "results/summary.json"))
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    setup_imports(repo)

    from part3_fleet_manager.core.config import load_config
    from test_4o_plan import Kit
    import cbs as cbs_mod
    import cbs_strict as cbs_strict_mod
    import cbs_disjoint as cbs_disjoint_mod

    cfg = load_config(repo / "src/part3_fleet_manager/config/fleet.yaml")

    def new_kit():
        return Kit(replace(cfg, traffic=replace(cfg.traffic)))

    kit_opt = new_kit()
    loose_checker = cbs_mod.CBS(kit_opt.P)
    strict_checker = cbs_strict_mod.CBS(kit_opt.P)

    specs = build_cases(kit_opt)
    if args.only > 0:
        specs = specs[:args.only]
    print(f"Benchmarking {len(specs)} cases across limits={args.limits}...", flush=True)

    # 1. Compute exact Joint A* optimal baseline once per instance.
    t0 = time.perf_counter()
    opt_results = []
    for spec in specs:
        kit_opt.P.forget()
        s = time.perf_counter()
        plan = kit_opt.P._search(make_agents(kit_opt, spec), {}, s + args.opt_limit_s)
        dt = (time.perf_counter() - s) * 1000.0
        opt_results.append({
            "ok": plan.ok,
            "cost": plan.cost,
            "ms": dt,
            "valid_loose": plan.ok and loose_checker.first_conflict(plan.events) is None,
            "valid_strict": plan.ok and strict_checker.first_conflict(plan.events) is None,
            "reason": plan.reason,
        })
    opt_ok = sum(o["ok"] for o in opt_results)
    opt_strict = sum(o["valid_strict"] for o in opt_results)
    print(
        f"[opt] {opt_ok}/{len(specs)} ok, strict_clean={opt_strict}/{len(specs)} "
        f"(flagged={opt_ok - opt_strict}), elapsed={time.perf_counter() - t0:.1f}s",
        flush=True,
    )

    methods = ("joint", "cbs", "cbs_strict", "cbs_disjoint")
    limits = [int(x) for x in args.limits.split(",") if x.strip()]

    raw_by_limit = {}
    summary = {
        "cases": len(specs),
        "level3_cases": sum(s["has_l3"] for s in specs),
        "level3_asym_cases": sum(s["l3_asym"] for s in specs),
        "level3_both_cases": sum(s["l3_both"] for s in specs),
        "opt_baseline": {
            "limit_s": args.opt_limit_s,
            "solved": opt_ok,
            "valid_loose": sum(o["valid_loose"] for o in opt_results),
            "valid_strict": opt_strict,
            "strict_flagged": opt_ok - opt_strict,
            "mean_ms": round(statistics.mean(o["ms"] for o in opt_results), 2),
            "max_ms": round(max(o["ms"] for o in opt_results), 2),
        },
        "limits": {},
    }

    for limit_ms in limits:
        print(f"\n=== Limit: {limit_ms} ms ===", flush=True)
        rows = [
            {
                "case": spec["case"],
                "la": spec["la"],
                "lb": spec["lb"],
                "has_l3": spec["has_l3"],
                "l3_asym": spec["l3_asym"],
                "l3_both": spec["l3_both"],
                "opt": opt_results[i],
            }
            for i, spec in enumerate(specs)
        ]

        # Run each method on an isolated Kit so PlanGraph/conflict caches never leak across methods.
        for method in methods:
            kit_m = new_kit()
            loose_m = cbs_mod.CBS(kit_m.P)
            strict_m = cbs_strict_mod.CBS(kit_m.P)
            if method == "joint":
                solver = lambda ag, k=kit_m, lim=float(limit_ms): k.P.plan(ag, limit_ms=lim)
            elif method == "cbs":
                inst = cbs_mod.CBS(kit_m.P)
                solver = lambda ag, s=inst, lim=float(limit_ms): s.plan(ag, limit_ms=lim)
            elif method == "cbs_strict":
                inst = cbs_strict_mod.CBS(kit_m.P)
                solver = lambda ag, s=inst, lim=float(limit_ms): s.plan(ag, limit_ms=lim)
            elif method == "cbs_disjoint":
                inst = cbs_disjoint_mod.CBS(kit_m.P)
                solver = lambda ag, s=inst, lim=float(limit_ms): s.plan(ag, limit_ms=lim)
            else:
                raise ValueError(method)

            mt0 = time.perf_counter()
            for i, spec in enumerate(specs):
                kit_m.P.forget()
                ag = make_agents(kit_m, spec)
                st = time.perf_counter()
                plan = solver(ag)
                dt = (time.perf_counter() - st) * 1000.0
                rows[i][method] = {
                    "ok": plan.ok,
                    "ms": round(dt, 3),
                    "cost": round(plan.cost, 6) if plan.ok else 0.0,
                    "method": plan.method,
                    "reason": plan.reason,
                    "ct_nodes": getattr(plan, "ct_nodes", 0),
                    "valid_loose": bool(plan.ok and loose_m.first_conflict(plan.events) is None),
                    "valid_strict": bool(plan.ok and strict_m.first_conflict(plan.events) is None),
                }
            print(f"  [{method:12s}] done in {time.perf_counter() - mt0:.1f}s", flush=True)

        raw_by_limit[str(limit_ms)] = rows
        l3_rows = [r for r in rows if r["has_l3"]]
        l3_asym_rows = [r for r in rows if r["l3_asym"]]
        l3_both_rows = [r for r in rows if r["l3_both"]]
        non_l3_rows = [r for r in rows if not r["has_l3"]]

        lim_summary = {
            "all": {m: summarize_slice(rows, m) for m in methods},
            "level3": {m: summarize_slice(l3_rows, m) for m in methods},
            "level3_asym": {m: summarize_slice(l3_asym_rows, m) for m in methods},
            "level3_both": {m: summarize_slice(l3_both_rows, m) for m in methods},
            "non_level3": {m: summarize_slice(non_l3_rows, m) for m in methods},
        }
        summary["limits"][str(limit_ms)] = lim_summary

        for m in methods:
            s_all = lim_summary["all"][m]
            s_l3 = lim_summary["level3"][m]
            print(
                f"  {m:12s} | mean={s_all['mean_ms']:5.1f} p95={s_all['p95_ms']:6.1f} max={s_all['max_ms']:6.1f} ms | "
                f"unsolved={s_all['unsolved']:3d} fb={s_all['fallback_prioritized']:2d} strict={s_all['valid_strict']:4d} | "
                f"eq_opt={s_all['equal_opt_pct_solved']:5.1f}% gap_mean={s_all['cost_diff_mean_pct']:+5.2f}% "
                f"gap_max={s_all['cost_diff_max_pct']:+6.2f}% | "
                f"L3: unsolved={s_l3['unsolved']:2d} eq={s_l3['equal_opt_pct_solved']:5.1f}% "
                f"gap={s_l3['cost_diff_mean_pct']:+5.2f}%/{s_l3['cost_diff_max_pct']:+6.2f}%",
                flush=True,
            )

    summary_path = Path(args.summary_out)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(f"\nSaved summary -> {summary_path}", flush=True)

    raw_path = Path(args.raw_out)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps({"limits": raw_by_limit}, ensure_ascii=False))
    print(f"Saved raw data -> {raw_path}", flush=True)


if __name__ == "__main__":
    main()
