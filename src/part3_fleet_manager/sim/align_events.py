#!/usr/bin/env python3
"""Align 10 Hz Gazebo ground-truth distance CSV with part3_fleet_manager SQLite events (bad_sim.db).

Computes:
  - Wall-clock and Gazebo sim-clock duration & average RTF
  - Minimum GT & AMCL center-to-center distance AFTER command acceptance while >= 1 robot is moving
    (compared against the 0.45 m reference threshold), plus overall CSV min
  - Chronological table of fleet DB events joined to nearest GT sample (showing both wall & sim time)
  - Stopping distance for PAUSE events
"""
import argparse
import bisect
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import sqlite3


def load_csv(path: Path):
    rows = []
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append({
                "wall_ts": float(r["wall_ts"]),
                "wall_iso": r["wall_iso"],
                "sim_ts": float(r["sim_ts"]) if r["sim_ts"] else None,
                "rtf": float(r["rtf"]) if r["rtf"] else None,
                "r3_x": float(r["r3_x"]), "r3_y": float(r["r3_y"]),
                "r2_x": float(r["r2_x"]), "r2_y": float(r["r2_y"]),
                "dist_m": float(r["dist_m"]),
                "r3_v": float(r["r3_speed_mps"]) if r["r3_speed_mps"] else 0.0,
                "r2_v": float(r["r2_speed_mps"]) if r["r2_speed_mps"] else 0.0,
                "amcl_dist_m": float(r["amcl_dist_m"]) if r["amcl_dist_m"] else None,
                "label": r.get("label", ""),
            })
    return rows


def nearest_row(rows, ts_list, target_ts: float, max_diff: float = 0.5):
    if not rows:
        return None
    i = bisect.bisect_left(ts_list, target_ts)
    cand = []
    if i < len(rows):
        cand.append(rows[i])
    if i > 0:
        cand.append(rows[i - 1])
    best = min(cand, key=lambda r: abs(r["wall_ts"] - target_ts))
    return best if abs(best["wall_ts"] - target_ts) <= max_diff else None


def stop_distance(rows, ts_list, start_ts: float, robot: str, speed_thresh: float = 0.015, hold_s: float = 0.4):
    """Measure GT path/straight distance traveled by robot from start_ts until speed <= speed_thresh for hold_s."""
    key_x, key_y, key_v = ("r3_x", "r3_y", "r3_v") if robot in ("robot_a", "robot3") else ("r2_x", "r2_y", "r2_v")
    i = bisect.bisect_left(ts_list, start_ts)
    if i >= len(rows):
        return None
    p0 = (rows[i][key_x], rows[i][key_y])
    path_m = 0.0
    still_since = None
    prev = p0
    stop_row = rows[i]
    for r in rows[i + 1:]:
        cur = (r[key_x], r[key_y])
        step = math.hypot(cur[0] - prev[0], cur[1] - prev[1])
        path_m += step
        prev = cur
        if r[key_v] <= speed_thresh:
            if still_since is None:
                still_since = r["wall_ts"]
                stop_row = r
            if r["wall_ts"] - still_since >= hold_s:
                straight_m = math.hypot(stop_row[key_x] - p0[0], stop_row[key_y] - p0[1])
                return {
                    "start_ts": rows[i]["wall_ts"],
                    "stop_ts": stop_row["wall_ts"],
                    "elapsed_wall_s": round(stop_row["wall_ts"] - rows[i]["wall_ts"], 3),
                    "elapsed_sim_s": round(stop_row["sim_ts"] - rows[i]["sim_ts"], 3)
                    if (stop_row["sim_ts"] is not None and rows[i]["sim_ts"] is not None) else None,
                    "straight_m": round(straight_m, 4),
                    "path_m": round(path_m, 4),
                    "start_xy": (round(p0[0], 3), round(p0[1], 3)),
                    "stop_xy": (round(stop_row[key_x], 3), round(stop_row[key_y], 3)),
                }
        else:
            still_since = None
    return None


def analyze_csv(
    csv_path: Path,
    db_path: Path,
    since_ts: float | None = None,
    until_ts: float | None = None,
    speed_thresh: float = 0.015,
    pad: float = 1.0,
) -> dict:
    rows = load_csv(csv_path)
    if not rows:
        return {"error": "CSV has no rows"}
    ts_list = [r["wall_ts"] for r in rows]
    t_start, t_end = ts_list[0] - pad, ts_list[-1] + pad

    evs = []
    if db_path.exists():
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        evs = list(con.execute(
            "SELECT id, ts, robot, mission_id, type, detail_json FROM events "
            "WHERE ts BETWEEN ? AND ? ORDER BY ts, id",
            (t_start, t_end),
        ))
        con.close()

    # Determine command acceptance timestamp if not explicitly provided:
    # first LEG_START or RESOURCE_REQUEST inside CSV range
    if since_ts is None:
        for ev in evs:
            if ev["ts"] >= ts_list[0] - 0.5 and ev["type"] in ("LEG_START", "RESOURCE_REQUEST", "MISSION_ASSIGNED"):
                since_ts = ev["ts"]
                break
        if since_ts is None:
            since_ts = ts_list[0]

    window_rows = [
        r for r in rows
        if r["wall_ts"] >= since_ts and (until_ts is None or r["wall_ts"] <= until_ts)
    ]
    moving_rows = [
        r for r in window_rows
        if r["r3_v"] >= speed_thresh or r["r2_v"] >= speed_thresh
    ]

    def _min_info(subset, key="dist_m"):
        valid = [r for r in subset if r.get(key) is not None]
        if not valid:
            return None
        m = min(valid, key=lambda r: r[key])
        return {
            "dist_m": round(m[key], 4),
            "wall_ts": m["wall_ts"],
            "wall_iso": m["wall_iso"],
            "sim_ts": round(m["sim_ts"], 3) if m["sim_ts"] is not None else None,
            "r3_xy": [round(m["r3_x"], 3), round(m["r3_y"], 3)],
            "r2_xy": [round(m["r2_x"], 3), round(m["r2_y"], 3)],
            "r3_v": round(m["r3_v"], 3),
            "r2_v": round(m["r2_v"], 3),
        }

    sim_rows = [r for r in rows if r["sim_ts"] is not None]
    wall_dur = rows[-1]["wall_ts"] - rows[0]["wall_ts"]
    sim_dur = (sim_rows[-1]["sim_ts"] - sim_rows[0]["sim_ts"]) if len(sim_rows) >= 2 else None
    avg_rtf = (sim_dur / wall_dur) if (sim_dur is not None and wall_dur > 0) else None

    min_gt_moving = _min_info(moving_rows, "dist_m")
    min_amcl_moving = _min_info(moving_rows, "amcl_dist_m")
    min_gt_all = _min_info(rows, "dist_m")

    return {
        "samples": len(rows),
        "moving_samples": len(moving_rows),
        "since_ts": since_ts,
        "wall_duration_s": round(wall_dur, 2),
        "sim_duration_s": round(sim_dur, 2) if sim_dur is not None else None,
        "avg_rtf": round(avg_rtf, 3) if avg_rtf is not None else None,
        "min_gt_moving": min_gt_moving,
        "min_amcl_moving": min_amcl_moving,
        "min_gt_all": min_gt_all,
        "gt_ge_045": (min_gt_moving["dist_m"] >= 0.45) if min_gt_moving else None,
        "rows": rows,
        "ts_list": ts_list,
        "events": evs,
    }


def format_report(csv_path: Path, analysis: dict) -> str:
    rows = analysis["rows"]
    ts_list = analysis["ts_list"]
    evs = analysis["events"]
    lines = []
    lines.append(
        f"CSV: {csv_path} ({analysis['samples']} samples, moving={analysis['moving_samples']}, "
        f"wall={analysis['wall_duration_s']}s, sim={analysis['sim_duration_s']}s, RTF={analysis['avg_rtf']})"
    )
    mg = analysis["min_gt_moving"]
    if mg:
        flag = ">= 0.45 m" if mg["dist_m"] >= 0.45 else "< 0.45 m"
        lines.append(
            f"MIN GT dist (moving after accept): {mg['dist_m']:.4f} m ({flag}) "
            f"at {mg['wall_iso']} (sim={mg['sim_ts']}s) A={tuple(mg['r3_xy'])} B={tuple(mg['r2_xy'])}"
        )
    else:
        lines.append("MIN GT dist (moving after accept): NONE (0 moving samples!)")
    ma = analysis["min_amcl_moving"]
    if ma:
        lines.append(f"MIN AMCL dist (moving after accept): {ma['dist_m']:.4f} m at {ma['wall_iso']} (sim={ma['sim_ts']}s)")
    if analysis["min_gt_all"]:
        ga = analysis["min_gt_all"]
        lines.append(f"MIN GT dist (all rows): {ga['dist_m']:.4f} m at {ga['wall_iso']} (sim={ga['sim_ts']}s)")

    lines.append(f"\nDB events in window ({len(evs)}):")
    lines.append("| wall_time | sim_s | robot | event | GT dist(m) | A(x,y) v | B(x,y) v | detail |")
    lines.append("|---|---:|---|---|---:|---|---|---|")
    for ev in evs:
        nr = nearest_row(rows, ts_list, ev["ts"])
        iso = datetime.fromtimestamp(ev["ts"]).strftime("%H:%M:%S.%f")[:-3]
        d = json.loads(ev["detail_json"] or "{}")
        short = d.get("reason") or d.get("destination") or d.get("rule") or ""
        if "distance_m" in d:
            short = f"{short} (db_d={d['distance_m']:.3f}m)".strip()
        if "gap_m" in d:
            short = f"{short} (gap={d['gap_m']:.3f}m)".strip()
        if nr:
            sim_s = f"{nr['sim_ts']:.2f}" if nr["sim_ts"] is not None else "-"
            lines.append(
                f"| {iso} | {sim_s} | {ev['robot'] or '-'} | `{ev['type']}` | "
                f"{nr['dist_m']:.3f} | ({nr['r3_x']:.2f},{nr['r3_y']:.2f}) {nr['r3_v']:.2f} | "
                f"({nr['r2_x']:.2f},{nr['r2_y']:.2f}) {nr['r2_v']:.2f} | {short} |"
            )
        else:
            lines.append(f"| {iso} | - | {ev['robot'] or '-'} | `{ev['type']}` | - | - | - | {short} |")
        if ev["type"] == "PAUSE" and ev["robot"]:
            sd = stop_distance(rows, ts_list, ev["ts"], ev["robot"])
            if sd:
                lines.append(
                    f"  -> STOP_DIST({ev['robot']}): path={sd['path_m']:.4f}m, straight={sd['straight_m']:.4f}m, "
                    f"dt_wall={sd['elapsed_wall_s']}s, dt_sim={sd['elapsed_sim_s']}s "
                    f"{sd['start_xy']} -> {sd['stop_xy']}"
                )
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Align GT distance CSV with bad_sim.db events")
    parser.add_argument("--csv", required=True, help="Input CSV from gt_distance_logger.py")
    parser.add_argument("--db", default="~/.bad/bad_sim.db", help="Fleet manager SQLite DB path")
    parser.add_argument("--since", type=float, default=None, help="Command acceptance wall_ts")
    parser.add_argument("--until", type=float, default=None, help="Optional end wall_ts")
    parser.add_argument("--speed-thresh", type=float, default=0.015, help="Moving speed threshold m/s")
    parser.add_argument("--pad", type=float, default=1.0, help="Seconds padding before/after CSV range")
    parser.add_argument("--out", default="", help="Optional markdown/text summary output path")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    analysis = analyze_csv(
        csv_path, Path(args.db).expanduser(),
        since_ts=args.since, until_ts=args.until,
        speed_thresh=args.speed_thresh, pad=args.pad,
    )
    if "error" in analysis:
        raise SystemExit(analysis["error"])
    text = format_report(csv_path, analysis)
    print(text)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
