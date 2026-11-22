"""4K screen: stop wording follows the robot STATE, and the occupancy clear button sends its own resource.

The browser code runs in headless Chrome with a throwaway profile (no node on the control PC).
"""
import html
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from part1_ui_dashboard.ui_api import STATES
from part1_ui_dashboard.web import Feed, create_app

STATIC = Path(__file__).resolve().parents[1] / "part1_ui_dashboard" / "static"
CHROME = next((shutil.which(name) for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
               if shutil.which(name)), None)

# Runs after app.js and pages/dashboard.js. Writes one JSON result into <pre id="out">.
PROBE = r"""
(async () => {
  const out = {};
  try {
    cfg = {names: {A: "응급실(A)", B: "102호 병실(B)", C: "수술실(C)", D: "간호 스테이션(D)", E: "101호 병실(E)", F: "조제실(F)"},
           robot_names: {robot_a: "침상형 이송 로봇", robot_b: "약품 운송 로봇"}, ack_timeout_s: 2, states: __STATES__};
    cfg.zones = Object.fromEntries(Object.keys(cfg.names).map(z => [z, {}]));
    const select = document.createElement("select");
    select.innerHTML = zoneOptions("C");
    out.zones = [select.value, select.selectedOptions[0].textContent, locationText({zone:"C", slot:2}), resLabel("ROOM_B")];
    const robot = (label, part4_state, causes) => ({label, part4_state, pause_causes: causes, state: "SAFE_PAUSE", link_ok: true});
    const action = () => [$("alarm-action").textContent, $("alarm-action").className];
    const event = {type: "BED_EXIT", detail: {targets: ["robot_a"]}, ts: 1};
    // 1. stop wording: cause present, STATE not yet PAUSED / PAUSED.
    out.stop = {};
    for (const [key, state] of [["moving", "MOVING"], ["stopping", "STOPPING_PAUSE"], ["paused", "PAUSED"], ["idle", "IDLE"]]) {
      S = {missions: [], robots: {robot_a: robot("침상형 이송 로봇", state, ["BED_EXIT"]), robot_b: robot("약품 운송 로봇", "MOVING", [])}};
      alerts = [event];
      alarm();
      out.stop[key] = action();
      closeModal();
    }
    // Two robots, one confirmed and one not: amber until both reported.
    S = {missions: [], robots: {robot_a: robot("침상형 이송 로봇", "PAUSED", ["BED_EXIT"]),
                                robot_b: robot("약품 운송 로봇", "MOVING", ["BED_EXIT"])}};
    alerts = [event];
    alarm();
    out.stop.mixed = action();
    closeModal();
    // 2. occupancy clear: each UNKNOWN resource gets its own button and request.
    const sent = [];
    window.fetch = async (url, init) => {
      const body = JSON.parse(init.body);
      sent.push({url, body});
      const ref = "ref-" + sent.length;
      remember({ref, ok: true, message: "접수했습니다", mission_id: null});
      return {ok: true, json: async () => ({ref})};
    };
    const resource = state => ({state, capacity: 1, holders: [], queue: [], blocked: false});
    const seg = (kind, label) => ({kind, label, state: "UNKNOWN", direction: 0, holders: [{robot: "robot_a", state: "UNKNOWN", direction: 0}]});
    S = {stamp: 10, robots: {}, missions: [], resources: {CORRIDOR: resource("UNKNOWN"), ROOM_B: resource("UNKNOWN"), ROOM_E: resource("UNKNOWN")},
         traffic: {mode: "PLAN", stop_line_waits: {}, plans: {}, segments: {CROSSROAD: seg("single", "교차로"),
                   DOOR_B: seg("single", "102호 병실(B) 문"), DOOR_E: seg("single", "101호 병실(E) 문")}}};
    const panel = document.createElement("div");
    panel.innerHTML = roadPanel();
    out.buttons = [...panel.querySelectorAll('[data-act="clear"]')].map(b => [b.dataset.id, b.textContent]);
    out.clear = {};
    for (const [id] of out.buttons) {
      actions.clear(id);
      const ok = $("dialog-content").querySelector('[data-act="clear-ok"]');
      const text = $("dialog-content").textContent;
      await actions["clear-ok"](ok.dataset.id, ok);
      out.clear[id] = {text, body: sent.at(-1)?.body, url: sent.at(-1)?.url};
      closeModal();
    }
    // 3. robot block badge and situation line follow the same rule (4K answer 1).
    alerts = [];
    const unit = (part4_state, causes) => ({label: "침상형 이송 로봇", mark: "A", ns: "robot3", state: "SAFE_PAUSE",
      part4_state, pause_causes: causes, link_ok: true, mission_id: null, leg: null, zone: "D", region: "TOP",
      slot: null, battery_pct: 60, stall_s: 0, obstacle: null, obstacle_pending: false, returning: false, via: null});
    const idle = {label: "약품 운송 로봇", mark: "B", ns: "robot2", state: "IDLE", part4_state: "IDLE", pause_causes: [],
      link_ok: true, mission_id: null, leg: null, zone: "F", region: "BOTTOM", slot: null, battery_pct: 80, stall_s: 0,
      obstacle: null, obstacle_pending: false, returning: false, via: null};
    const free = {CORRIDOR: resource("FREE"), ROOM_B: resource("FREE"), ROOM_E: resource("FREE")};
    out.badge = {}; out.line = {};
    for (const [key, state, causes] of [["manual_moving", "MOVING", ["MANUAL"]], ["manual_stopping", "STOPPING_PAUSE", ["MANUAL"]],
        ["manual_paused", "PAUSED", ["MANUAL"]], ["manual_idle", "IDLE", ["MANUAL"]],
        ["exit_moving", "MOVING", ["BED_EXIT"]], ["exit_paused", "PAUSED", ["BED_EXIT"]], ["bump_moving", "MOVING", ["BUMP"]]]) {
      S = {stamp: 10, missions: [], resources: free,
           bed_exit: {value: false, since: null, monitor: {state: "OK", rate_hz: 30}},
           robots: {robot_a: unit(state, causes), robot_b: idle}};
      const block = document.createElement("div");
      block.innerHTML = robotCard("robot_a", S.robots.robot_a);
      const badge = block.querySelector(".badge");
      out.badge[key] = [badge.textContent, badge.className];
      received = performance.now();
      renderTop();
      out.line[key] = [$("situation").querySelector("strong").textContent,
                       $("situation").querySelector("span").textContent, $("situation").className];
    }
  } catch (e) {
    out.error = String(e && e.stack || e);
  }
  document.getElementById("out").textContent = JSON.stringify(out);
})();
"""


def run_browser(tmp_path, probe=PROBE):
    if CHROME is None:
        pytest.skip("headless Chrome 없음")
    page = tmp_path / "probe.html"
    page.write_text(f"""<!doctype html><meta charset="utf-8">
<input id="operator" value="시험자"><div id="toast" popover="manual"></div>
<dialog id="dialog"><div id="dialog-content"></div></dialog><pre id="out"></pre>
<span class="mode"></span><time id="clock"></time><span id="connection"></span><button id="safety"></button>
<span id="bed-monitor"></span><span id="online"></span><span id="active-count"></span><span id="queued-count"></span>
<div id="situation"></div>
<script src="{(STATIC / 'app.js').as_uri()}"></script>
<script src="{(STATIC / 'pages' / 'dashboard.js').as_uri()}"></script>
<script>{probe.replace("__STATES__", json.dumps(STATES, ensure_ascii=False))}</script>""", encoding="utf-8")
    profile = tmp_path / "chrome-profile"
    result = subprocess.run(
        [CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--no-first-run", "--disable-extensions",
         f"--user-data-dir={profile}", "--virtual-time-budget=3000", "--dump-dom", page.as_uri()],
        capture_output=True, text=True, timeout=60)
    found = re.search(r'<pre id="out">(.*?)</pre>', result.stdout, re.S)
    assert found and found.group(1), result.stderr[-2000:]
    data = json.loads(html.unescape(found.group(1)))
    assert "error" not in data, data["error"]
    return data


@pytest.fixture(scope="module")
def browser(tmp_path_factory):
    return run_browser(tmp_path_factory.mktemp("browser"))


def test_64_stop_wording_waits_for_the_robot_state(browser):
    """Cause set but PART 04 not PAUSED: amber "requested"; PAUSED: red "confirmed"."""
    assert browser["stop"]["moving"] == ["침상형 이송 로봇 정지 요청 중(확인 전)", "amber-text"]
    assert browser["stop"]["stopping"] == ["침상형 이송 로봇 정지 요청 중(확인 전)", "amber-text"]
    assert browser["stop"]["paused"] == ["침상형 이송 로봇 정지 확인", "red-text"]
    # D-32: a robot whose STATE says it is not driving (IDLE, COMPLETE, …) has also reported standing still.
    assert browser["stop"]["idle"] == ["침상형 이송 로봇 정지 확인", "red-text"]
    assert browser["stop"]["mixed"] == ["침상형 이송 로봇 정지 확인 · 약품 운송 로봇 정지 요청 중(확인 전)", "amber-text"]


def test_65_each_unknown_segment_has_its_own_clear_button(browser):
    assert browser["buttons"] == [["CROSSROAD", "현장 확인 후 교차로 비움"],
                                  ["DOOR_B", "현장 확인 후 102호 병실(B) 문 비움"],
                                  ["DOOR_E", "현장 확인 후 101호 병실(E) 문 비움"]]
    for resource, label in (("CROSSROAD", "교차로"), ("DOOR_B", "102호 병실(B) 문"), ("DOOR_E", "101호 병실(E) 문")):
        sent = browser["clear"][resource]
        assert sent["url"] == "/api/command"
        assert sent["body"] == {"cmd": "RESOURCE_CLEAR_CONFIRM", "resource": resource, "operator": "시험자"}
        assert f"현장에서 {label}의 점유 상태를 확인했습니까?" in sent["text"]


@pytest.mark.parametrize("resource", ["CORRIDOR", "ROOM_B", "ROOM_E"])
def test_65_command_route_publishes_the_resource_unchanged(resource):
    messages = []
    client = create_app(Feed(), {}, lambda *m: messages.append(m), None).test_client()
    reply = client.post("/api/command", json={"cmd": "RESOURCE_CLEAR_CONFIRM", "resource": resource, "operator": "당직"})
    assert reply.status_code == 202
    (topic, text), = messages
    assert topic == "/ui/operator_cmd"
    assert json.loads(text) == {"v": 1, "cmd": "RESOURCE_CLEAR_CONFIRM", "cmd_id": reply.json["ref"],
                                "resource": resource, "by": "당직"}


@pytest.mark.parametrize("key,expected", [
    ("manual_moving", ["정지 요청 중(확인 전)", "badge amber"]),
    ("manual_stopping", ["정지 요청 중(확인 전)", "badge amber"]),
    ("manual_paused", ["정지 확인", "badge red"]),
    ("manual_idle", ["정지 확인", "badge red"]),
    ("exit_moving", ["정지 요청 중(확인 전)", "badge amber"]),
])
def test_64_robot_block_badge_follows_the_robot_state(browser, key, expected):
    """Answer 1: the robot block badge uses the alarm rule instead of the control's "안전 정지"."""
    assert browser["badge"][key] == expected


@pytest.mark.parametrize("key,expected", [
    ("manual_moving", ["운영자 안전 정지", "침상형 이송 로봇 정지 요청 중(확인 전)", "attention"]),
    ("manual_stopping", ["운영자 안전 정지", "침상형 이송 로봇 정지 요청 중(확인 전)", "attention"]),
    ("manual_paused", ["운영자 안전 정지", "침상형 이송 로봇 정지 확인 · 재개 대기", "risk"]),
    ("manual_idle", ["운영자 안전 정지", "침상형 이송 로봇 정지 확인 · 재개 대기", "risk"]),
    ("exit_moving", ["안전 정지", "침상형 이송 로봇 정지 요청 중(확인 전)", "attention"]),
    ("exit_paused", ["안전 정지", "침상형 이송 로봇 정지 확인 · 재개 대기", "risk"]),
    # A bumper hit stays a red alarm; only its stop sentence follows the robot STATE.
    ("bump_moving", ["범퍼 충돌", "침상형 이송 로봇 정지 요청 중(확인 전)", "risk"]),
])
def test_64_situation_line_follows_the_robot_state(browser, key, expected):
    """Answer 1/2: the operator stop has no alarm window; the badge and this line show the unconfirmed stop."""
    assert browser["line"][key] == expected


def test_named_zone_labels_keep_command_value(browser):
    assert browser["zones"] == ["C", "수술실(C)", "수술실(C) 2번 자리", "102호 병실(B) 출입"]
