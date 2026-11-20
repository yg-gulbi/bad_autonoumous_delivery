"""Exercise the real EventSource listeners, dialog and acknowledgement buttons on a fake clock."""
import json
from pathlib import Path

import pytest

from part3_fleet_manager.core.config import load_config
from part3_fleet_manager.core.decisions import Decisions
from part3_fleet_manager.core.safety import Safety
from test_4k_screen import run_browser


def flicker_events():
    cfg = load_config(Path(__file__).resolve().parents[2] / "part3_fleet_manager/config/fleet.yaml")
    events = []

    def record(kind, stamp, reason, *, robot=None, **detail):
        events.append(dict(type=kind, ts=stamp, title=reason, robot=robot, detail=dict(reason=reason, **detail)))

    safety = Safety(cfg, record, Decisions(record))
    pattern = [True, False, True, True, False, False, False, False, False, True]
    for i in range(1800):
        safety.observe(pattern[i % 10], safety.targets, (i + 1) / 10)
    return events


# Keep page rendering minimal, but deliver every notice via start.js's real SSE subscription.
HARNESS = r"""
(async () => {
  const out = {}, errors = [];
  window.addEventListener("error", e => errors.push(e.message));
  try {
    const a = "robot_a", b = "robot_b";
    const bus = new EventTarget();
    window.EventSource = class { constructor() { return bus; } };
    window.fetch = async () => ({json: async () => ({bed_targets:[a], robot_names:{[a]:"A",[b]:"B"}, event_limit:100})});
    window.loadMap = async () => {};
    window.historyChanged = () => {};
    window.historySlots = new Map();
    loadPage = () => {};
    document.body.appendChild(Object.assign(document.createElement("button"), {id:"gear"}));
    await new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = new URL("start.js", document.querySelector('script[src$="app.js"]').src);
        script.onload = resolve;
        script.onerror = reject;
        document.body.appendChild(script);
    });
    const deliver = (kind, data) => bus.dispatchEvent(new MessageEvent(kind, {data:JSON.stringify(data)}));
    const state = {stamp:0, missions:[], resources:{CORRIDOR:{}}, robots:{},
                   bed_exit:{value:true, monitor:{state:"OK"}}};
    deliver("state", state);
    let clock = 0, beeps = 0;
    Object.defineProperty(performance, "now", {value: () => clock});
    beep = () => { beeps++; };
    const event = (type, ts, robot=null, detail={}) =>
        deliver("event", {type, ts, robot, title:type, detail:{reason:type, ...detail}});
    const dismiss = () => document.querySelector('[data-act="dismiss"]').click();
    __TEST__
    out.errors = errors;
  } catch (err) { out.error = String(err.stack || err); }
  $("out").textContent = JSON.stringify(out);
})();
"""


def browser_case(tmp_path, script):
    result = run_browser(tmp_path, HARNESS.replace("__TEST__", script))
    assert not result["errors"]
    return result


def test_real_three_minute_exit_trace_and_later_new_stop(tmp_path):
    trace = flicker_events()
    result = browser_case(tmp_path, """
        for (const e of __TRACE__) deliver("event", e);
        out.initial = [alerts.length, beeps, modal, alerts[0].type,
                       E.filter(e => e.type === "PAUSE").length];
        dismiss();
        event("BED_EXIT", 181);
        event("PAUSE", 181, b, {cause:"BED_EXIT"});
        event("PAUSE", 181, a, {cause:"MANUAL"});
        out.acknowledged = [alerts.length, beeps, modal, $("situation").querySelector("strong").textContent];
        event("PAUSE", 182, a, {cause:"BED_EXIT"});
        out.reopened = [alerts.length, beeps, modal, alerts[0].ts];
        dismiss();
        event("PAUSE", 182, a, {cause:"BED_EXIT"});
        out.replay = [alerts.length, beeps];
    """.replace("__TRACE__", json.dumps(trace)))
    assert result["initial"] == [1, 1, "alarm", "BED_EXIT", 1]
    assert result["acknowledged"] == [0, 1, None, "침상 이탈 감지"]
    assert result["reopened"] == [1, 2, "alarm", 182]
    assert result["replay"] == [0, 2]


@pytest.fixture(scope="module")
def alarm_kinds(tmp_path_factory):
    return browser_case(tmp_path_factory.mktemp("alarm-kinds"), """
        out.kinds = {};
        for (const kind of Object.keys(alarmKinds)) {
            const sounds = beeps, start = E.length, result = {};
            event(kind, 1, a, {targets:[a,b]});
            const original = document.querySelector('[data-act="dismiss"]');
            event(kind, 2, a, {targets:[b,a]});
            result.open = [alerts.length, beeps - sounds, original === document.querySelector('[data-act="dismiss"]'),
                           $("alarm-at").textContent === tm(2), $("elapsed").dataset.since, E[start].ts];
            event(kind, 3, b);
            event(kind, 4, b);
            result.queue = [alerts.length, beeps - sounds, alerts[1].ts];
            dismiss();
            result.next = [alerts.length, modal, $("alarm-at").textContent === tm(4)];
            event(kind, 5, a, {targets:[a,b]});
            result.repeat = [alerts.length, beeps - sounds];
            dismiss();
            out.kinds[kind] = result;
        }
    """)["kinds"]


@pytest.mark.parametrize("kind", ["BED_EXIT", "BED_MONITOR_LOST", "BUMP", "BLOCKED", "LINK_LOST", "FAULT"])
def test_alarm_deduplicates_queued_and_open_by_kind_and_target(alarm_kinds, kind):
    result = alarm_kinds[kind]
    assert result["open"] == [1, 1, True, True, "2", 1]
    assert result["queue"] == [2, 2, 4]
    assert result["next"] == [1, "alarm", True]
    assert result["repeat"] == [1, 2]


def test_monitor_back_discards_only_pending_or_visible_lost(tmp_path):
    result = browser_case(tmp_path, """
        const lost = t => event("BED_MONITOR_LOST", t, null, {targets:[a]});
        event("FAULT", 1, b);
        lost(2); lost(3);
        event("BED_MONITOR_BACK", 4);
        out.queued = [modal, alerts.map(e => e.type)];
        dismiss();
        let maximum = 0;
        for (let t = 5; t < 25; t += 4) {
            lost(t); lost(t+1);
            maximum = Math.max(maximum, alerts.length);
            event("BED_MONITOR_BACK", t+2);
        }
        out.cleared = [maximum, alerts.length, modal];
        lost(25); dismiss();
        event("BED_MONITOR_BACK", 26); lost(27);
        out.acknowledged = [alerts.length, modal];
        event("PAUSE", 27, a, {cause:"BED_MONITOR_LOST"});
        event("BUMP", 28, b);
        event("BED_MONITOR_BACK", 29);
        out.next = [modal, alerts.map(e => e.type), $("dialog-content").querySelector("h2").textContent];
        dismiss(); lost(32);
        const snapshot = (stamp, monitor, events) => deliver("snapshot", {
            state:{...state, stamp, bed_exit:{value:false, monitor:{state:monitor}}}, events, cards:[], acks:[], age:0
        });
        snapshot(31, "OK", [{type:"BED_MONITOR_BACK", ts:29}]);
        out.stale = [modal, alerts.length];
        snapshot(33, "LOST", [{type:"BED_MONITOR_BACK", ts:34}]);
        out.recovered = [modal, alerts.length];
    """)
    assert result["queued"] == ["alarm", ["FAULT"]]
    assert result["cleared"] == [1, 0, None]
    assert result["acknowledged"] == [0, None]
    assert result["next"] == ["alarm", ["BUMP"], "BUMP"]
    assert result["stale"] == ["alarm", 1]
    assert result["recovered"] == [None, 0]


def test_monitor_warn_toasts_are_limited_per_message_on_receipt(tmp_path):
    result = browser_case(tmp_path, """
        const warn = reason => event("BED_MONITOR_WARN", 1000, null, {reason});
        warn("불안정"); warn("불안정"); warn("지연");
        clock = 29999; warn("불안정"); warn("지연");
        out.before = [...$("toast").children].map(n => n.textContent);
        clock = 30000; warn("불안정"); warn("불안정"); warn("지연");
        out.after = [...$("toast").children].map(n => n.textContent);
        out.journal = E.length;
    """)
    assert result["before"] == ["불안정", "지연"]
    assert result["after"] == ["불안정", "지연", "불안정", "지연"]
    assert result["journal"] == 8
