"""pytest 실행 중 각 테스트의 시작·기대 결과·종료 상태를 즉시 출력한다."""

from __future__ import annotations

import re
import time


_items = {}
_started = {}
_finished = set()
_outcomes = {}


def _expected(item):
    doc = getattr(item.obj, "__doc__", None)
    if doc and doc.strip():
        return doc.strip().splitlines()[0]
    name = re.sub(r"\[[^]]*]$", "", item.name)
    return name.removeprefix("test_").replace("_", " ")


def pytest_collection_modifyitems(items):
    total = len(items)
    for index, item in enumerate(items, 1):
        _items[item.nodeid] = (index, total, _expected(item))
    print(f"[PLAN] collected_tests={total}", flush=True)


def pytest_runtest_logstart(nodeid, location):
    index, total, expected = _items[nodeid]
    _started[nodeid] = time.perf_counter()
    print(f"[TEST {index:02d}/{total:02d} START] {nodeid}", flush=True)
    print(f"[EXPECTED] {expected}", flush=True)


def pytest_runtest_logreport(report):
    if report.nodeid in _finished:
        return
    if report.when not in ("setup", "call", "teardown"):
        return

    if report.failed:
        _outcomes[report.nodeid] = ("FAIL", report.longreprtext.splitlines()[-1])
    elif report.skipped:
        _outcomes.setdefault(report.nodeid, ("SKIP", "skipped"))
    elif report.when == "call":
        _outcomes.setdefault(report.nodeid, ("PASS", ""))
    if report.when != "teardown":
        return

    index, total, _ = _items[report.nodeid]
    elapsed = time.perf_counter() - _started.get(report.nodeid, time.perf_counter())
    status, reason = _outcomes.get(report.nodeid, ("FAIL", "test produced no call result"))
    print(
        f"[TEST {index:02d}/{total:02d} END] {status} duration_s={elapsed:.3f}",
        flush=True,
    )
    if reason:
        print(f"[REASON] {reason}", flush=True)
    _finished.add(report.nodeid)


def pytest_sessionfinish(session, exitstatus):
    terminal = session.config.pluginmanager.get_plugin("terminalreporter")
    stats = terminal.stats if terminal is not None else {}
    passed = len(stats.get("passed", ()))
    failed = len(stats.get("failed", ())) + len(stats.get("error", ()))
    skipped = len(stats.get("skipped", ()))
    print("[QUANTITATIVE RESULTS]", flush=True)
    print(
        f"collected={len(session.items)} passed={passed} failed={failed} "
        f"skipped={skipped}",
        flush=True,
    )
