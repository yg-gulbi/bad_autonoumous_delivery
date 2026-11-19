"""Part 5 검증 산출물을 집계해 콘솔과 summary.json에 기록한다."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def _junit_totals(run_root):
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "time_s": 0.0}
    for path in run_root.rglob("*.xml"):
        root = ET.parse(path).getroot()
        suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
        for suite in suites:
            for key in ("tests", "failures", "errors", "skipped"):
                totals[key] += int(suite.get(key, 0))
            totals["time_s"] += float(suite.get("time", 0.0))
    totals["passed"] = (
        totals["tests"] - totals["failures"] - totals["errors"] - totals["skipped"]
    )
    totals["time_s"] = round(totals["time_s"], 3)
    return totals


def _benchmark_rows(run_root):
    path = run_root / "integration_benchmark" / "all_modules" / "comparison.csv"
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--status", choices=("PASS", "FAIL"), required=True)
    parser.add_argument("--planned", type=int, required=True)
    parser.add_argument("--started", type=int, required=True)
    parser.add_argument("--finished", type=int, required=True)
    parser.add_argument("--passed", type=int, required=True)
    parser.add_argument("--failed", type=int, required=True)
    args = parser.parse_args()

    run_root = Path(args.run_root).resolve()
    totals = _junit_totals(run_root)
    integration = _read_json(run_root / "integration_tests" / "mp4_metrics.json")
    benchmark = _benchmark_rows(run_root)
    payload = {
        "status": args.status,
        "profile": args.profile,
        "run_dir": str(run_root),
        "finished_at": datetime.now().astimezone().isoformat(),
        "test_totals": totals,
        "suite_totals": {
            "planned": args.planned,
            "started": args.started,
            "finished": args.finished,
            "passed": args.passed,
            "failed": args.failed,
        },
        "integration_metrics": integration,
        "benchmark_metrics": benchmark,
    }
    (run_root / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print("[FINAL QUANTITATIVE RESULTS]", flush=True)
    print(
        f"planned_suites={args.planned} started={args.started} "
        f"finished={args.finished} passed={args.passed} failed={args.failed}",
        flush=True,
    )
    print(
        "tests={tests} passed={passed} failures={failures} errors={errors} "
        "skipped={skipped} junit_time_s={time_s}".format(**totals),
        flush=True,
    )
    if integration:
        print(
            "mp4_frames={total_frames} published={published} published_true={published_true} "
            "published_false={published_false} unavailable={unavailable_frames} "
            "processing_fps={processing_fps}".format(**integration),
            flush=True,
        )
    if benchmark:
        print(
            "method recall safe_fpr precision f1 coverage event judge_p95_ms processing_fps",
            flush=True,
        )
        for row in benchmark:
            print(
                f"{row['method']} {row['recall']} {row['safe_fpr']} {row['precision']} "
                f"{row['f1']} {row['coverage']} "
                f"{row['event_detected']}/{row['event_total']} "
                f"{row['judge_p95_ms']} {row['processing_fps']}",
                flush=True,
            )
    print(f"[RUN END] {args.status}", flush=True)
    print(f"[ARTIFACTS] {run_root}", flush=True)


if __name__ == "__main__":
    main()
