"use strict";
const $ = id => document.getElementById(id);
const h = v => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nav = { dashboard: ["종합 관제", "로봇 운행과 이송 작업을 실시간으로 확인하세요."], missions: ["이송 작업", "요청부터 완료까지 이송 작업의 진행 상황을 확인하세요."], patient: ["환자 등록", "환자 정보와 이동 경로를 등록하세요."], supply: ["약품 운송 요청", "수령지와 전달지를 지정하고 인계 과정을 확인하세요."], log: ["운영 기록", "로봇 운행과 관제 판단을 시간순으로 확인하세요."] };
const robotName = r => S?.robots[r]?.label || cfg.robot_names?.[r] || "관제";
const zn = z => cfg.names[z] || "구역 미확인";
const zs = (z, slot) => zn(z) + (slot === 2 ? " 2번 자리" : "");
const resNames = { FREE: "비어 있음", GRANTED: "허가", OCCUPIED: "점유 중", UNKNOWN: "점유 불명" };
const resLabel = key => key === "CORRIDOR" ? "복도" : zn(key.slice(-1)) + " 출입";
const routeText = m => m.stops.map(zn).join(" → ");
function tm(t) {
    if (t == null) return "미수신";
    const date = new Date(t * 1000);
    return [date.getHours(), date.getMinutes(), date.getSeconds()].map(v => String(v).padStart(2,"0")).join(":");
}
const duration = n => [Math.floor(Math.max(0, n) / 60), Math.floor(Math.max(0, n) % 60)].map(v => String(v).padStart(2, "0")).join(":");
const pill = (text, color = "") => `<span class="badge ${color}">${h(text)}</span>`;
const btn = (text, act, id = "", cls = "", disabled = false) => `<button type="button" class="${cls}" data-act="${act}" data-id="${h(id)}" ${disabled ? "disabled" : ""}>${h(text)}</button>`;
const zoneOptions = initial => Object.keys(cfg.zones).map(z => `<option value="${z}" ${z === initial ? "selected" : ""}>${h(zn(z))}</option>`).join("");
const waitStates = new Set(["HOLD", "GIVE_WAY", "YIELDING", "WAIT_CONFIRM"]);
const stateColor = state => ["SAFE_PAUSE", "FAULT", "OFFLINE"].includes(state) ? "red" : waitStates.has(state) ? "amber" : "";
function displayState(id, robot) {
    const state = robot.state, road = S?.resources.CORRIDOR;
    // 4K (D-32): a stop cause is a request until the robot's STATE reports standing still.
    if (state === "SAFE_PAUSE") return stopConfirmed(robot) ? ["정지 확인", "red"] : ["정지 요청 중(확인 전)", "amber"];
    if (state === "HOLD" && robot.proximity) return [robot.proximity.head_on ? "마주 봄" : "가까움 정지", "amber"];
    if (state === "HOLD" && robot.zone_wait) {
        const access = {NEAR_START:"출발 대기", INBOUND:"도착 로봇 대기", OUTBOUND:"출구 양보"};
        return [access[robot.zone_wait.rule] || (robot.zone_wait.exit ? "출구 막힘 대기" : "목적지 대기"), "amber"];
    }
    if (state === "HOLD" && road?.follow_stop?.rear === id) return ["후행 대기", "amber"];
    if (["MOVING","SENDING","RETURNING"].includes(state)) {
        if (["UNDOCKING","LOCALIZING"].includes(robot.part4_state)) return ["도크에서 출발 준비", ""];
        if (robot.part4_state === "DOCKING") return ["도킹 중", ""];
    }
    if (state === "MOVING" && robot.stall_s > 0) {
        const blocked = Object.values(S.resources).some(res => res.blocked_robots?.includes(id));
        return [blocked ? "경로 막힘" : "일시 정체", "amber"];
    }
    return [cfg.states[state] || state, stateColor(state)];
}
const mById = id => S?.missions.find(m => m.mission_id === id);
const color = r => r === "robot_b" ? "violet" : "blue";
const empty = t => `<p class="empty">${h(t)}</p>`;
let cfg = {}, S = null, E = [], cards = new Map(), alerts = [], page = "", received = -Infinity, modal = null, logFilter = {}, missionFilter = { tab: "ACTIVE" }, selected = null;
let sound = true, audioContext = null, modalCard = null;
const pending = new Map(), early = new Map(), busy = new Set(), actions = {}, initializers = {}, renderers = {};

function put(id, html) {
    const node = $(id);
    if (node && node._markup !== html) {
        node.innerHTML = html;
        node._markup = html;
    }
}

function toast(msg, ok = true) {
    const n = document.createElement("p");
    n.className = ok ? "ok" : "err";
    n.textContent = msg;
    $("toast").append(n);
    $("toast").showPopover?.();
    setTimeout(() => { n.remove(); if (!$("toast").children.length)
        $("toast").hidePopover?.(); }, 5000);
}

function openModal(kind, markup) {
    modal = kind;
    $("dialog").dataset.kind = kind;
    modalCard = null;
    $("dialog-content").innerHTML = markup;
    if (!$("dialog").open)
        $("dialog").showModal();
}

function closeModal() { modal = null; modalCard = null; if ($("dialog").open)
    $("dialog").close(); }

function dialogHead(t) { return `<div class="row"><h2>${h(t)}</h2>${btn("닫기", "close", "", "quiet")}</div>`; }

function navigate(target) { if (location.hash === "#/" + target)
    loadPage();
else
    location.hash = "/" + target; }

function inspect(id) {
    selected = id;
    if (page === "missions") { render(); return; }
    missionFilter = {tab: mById(id)?.status || "ACTIVE"};
    navigate("missions");
}

function remember(ack) {
    early.set(ack.ref, ack);
    if (early.size > 150)
        early.delete(early.keys().next().value);
    pending.get(ack.ref)?.(ack);
}

async function send(path, data = {}, button = null) {
    const op = $("operator").value.trim();
    if (!op) {
        toast("조작자 이름을 입력하세요.", false);
        return;
    }
    const key = path + JSON.stringify(data);
    if (busy.has(key))
        return;
    busy.add(key);
    if (button)
        button.disabled = true;
    const timeout = 1000 * cfg.ack_timeout_s, deadline = performance.now() + timeout;
    let ref, timer;
    try {
        const response = await fetch("/api/" + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...data, operator: op }), signal: AbortSignal.timeout(timeout) });
        const reply = await response.json();
        if (!response.ok)
            throw Error(reply.message || "관제 응답 없음");
        if (!reply.ref) {
            toast(reply.message, reply.success !== false);
            return reply;
        }
        ref = reply.ref;
        const ack = early.get(ref) || await new Promise((resolve, reject) => { pending.set(ref, resolve); timer = setTimeout(() => reject(Error("관제 응답 없음")), Math.max(0, deadline - performance.now())); });
        early.delete(ref);
        toast(ack.message, ack.ok);
        return ack;
    }
    catch (e) {
        const message = e.name === "TimeoutError" ? "관제 응답 없음" : e.message;
        toast(message, false);
        return { ok: false, message };
    }
    finally {
        clearTimeout(timer);
        pending.delete(ref);
        busy.delete(key);
        if (button?.isConnected)
            button.disabled = false;
    }
}

function command(cmd, fields, b) { return send("command", { cmd, ...fields }, b); }

function render() { renderers[page]?.(); if (modal === "quick")
    formResults(); renderTop(); }

function monitorLocked(m) {
    return m && !m.bypass && (m.state === "LOST" || (m.state === "NEVER" && m.require_at_start));
}

function resumeReason(id) {
    const m = S?.bed_exit.monitor;
    if (monitorLocked(m) && (id == null || (m.targets || cfg.bed_targets || []).includes(id)))
        return "침상 감시가 끊겨 재개할 수 없습니다. 웹캠을 확인하세요";
    return S?.bed_exit.value ? "침상 이탈 해제 대기" : "";
}

function monitorBadge() {
    const m = S?.bed_exit.monitor;
    if (S?.bed_exit.value) return ["침상 감시 이탈 감지", "red-text"];
    if (m?.bypass) return ["감시 없이 운행 중", "red-text"];
    if (m?.state === "OK") return ["침상 감시 " + Math.round(m.rate_hz) + " Hz", ""];
    if (m?.state === "WARN") return ["침상 감시 불안정", "amber-text"];
    if (m?.state === "LOST") return ["침상 감시 끊김", "red-text"];
    return ["침상 감시 연결 안 됨", monitorLocked(m) ? "red-text" : ""];
}

function situation(fresh) {
    const issues = [];
    const add = (severity, title, detail, basis = false, tone = null) => issues.push({severity, title, detail, basis, tone});
    // 4K (D-32): stop lines say whether the robot STATE confirmed the stop. Unconfirmed is amber.
    const stopLine = (severity, title, robots) => {
        const [text, tone] = stopAction(robots), done = tone === "red-text";
        add(severity, title, done ? text + " · 재개 대기" : text, false, done ? "risk" : "attention");
    };
    if (!fresh) add(4, "관제 연결 끊김", "관제 상태 수신을 기다립니다.");
    if (!S) return issues;
    const mon = S.bed_exit.monitor, corridor = S.resources.CORRIDOR;
    if (S.bed_exit.value) add(4, "침상 이탈 감지", "침상 카메라로 환자 상태를 확인하세요.");
    if (monitorLocked(mon) || mon?.bypass) add(4, monitorBadge()[0], mon?.bypass ? "담당자가 감시 없이 운행을 선택했습니다." : "웹캠 신호와 대상 로봇을 확인하세요.");
    const units = Object.entries(S.robots);
    const bumped = units.filter(([,r]) => (r.pause_causes || [r.paused_by]).includes("BUMP"));
    if (bumped.length) add(4, "범퍼 충돌", stopAction(bumped.map(([,r]) => r))[0]);
    for (const [,r] of units) {
        if (!r.link_ok) add(4, "로봇 연결 끊김", r.label + " 상태 수신을 기다립니다.");
    }
    const manual = units.filter(([,r]) => (r.pause_causes || [r.paused_by]).includes("MANUAL"));
    if (manual.length) stopLine(3, manual.length === units.length ? "운영자 전체 정지" : "운영자 안전 정지", manual.map(([,r]) => r));
    const follow = corridor.follow_stop;
    if (follow) add(1, follow.rule === "GAP" ? "앞 로봇과 간격 유지 → " + robotName(follow.rear) + " 대기" : "앞 로봇 정지 → " + robotName(follow.rear) + " 대기",
        follow.rule === "GAP" ? "재개 간격 확보 또는 선행 로봇 이탈 대기" : robotName(follow.front) + " 주행 재개 또는 복도 이탈 대기", true);
    if ([...cards.values()].some(c => c.kind === "DEADLOCK")) add(1, "교착 해소 대기", "결정 카드에서 양보할 로봇을 선택하세요.", true);
    for (const [key,res] of Object.entries(S.resources)) {
        if (res.state === "UNKNOWN") add(1, resLabel(key) + " 점유 불명", "현장에서 점유 상태를 확인하세요.", true);
        if (res.blocked) add(1, "이동 경로 막힘", res.blocked_robots.map(robotName).join(" · "), true);
    }
    for (const [id,r] of units) {
        if (r.proximity) add(2, r.proximity.reason, r.label + " 정지 중",
            {type:"PROXIMITY_STOP", ts:r.proximity.since, robot:id, detail:r.proximity});
        if (r.zone_wait) {
            const peer = S.robots[r.zone_wait.peer];
            const accessHint = {NEAR_START:"재개 간격이 확보되면 출발합니다.", INBOUND:"상대가 자리에 서면 출구를 다시 확인합니다.", OUTBOUND:"상대가 구역을 벗어나면 재개합니다."}[r.zone_wait.rule];
            add(1, r.zone_wait.reason, accessHint || (r.zone_wait.exit ? "2번 자리가 비면 출발합니다." : peer?.state === "IDLE"
                ? peer.label + "을 다른 구역으로 보내면 출발합니다" : "목적지가 비면 자동으로 출발합니다."),
                {type:"ZONE_OCCUPIED_HOLD", ts:r.zone_wait.since, robot:id, detail:{...r.zone_wait, destination:r.zone_wait.zone}});
        }
        if (!r.proximity && !r.zone_wait && id !== follow?.rear && ["HOLD", "GIVE_WAY", "YIELDING"].includes(r.state))
            add(1, cfg.states[r.state], r.wait_reason || r.label, true);
        if (r.state === "MOVING" && r.stall_s > 0 && !Object.values(S.resources).some(res => res.blocked_robots?.includes(id)))
            add(1, "일시 정체", r.label + " · " + Math.floor(r.stall_s) + "초 동안 이동 없음", true);
        if (r.state === "FAULT") add(1, "주행 장애", r.label);
        if (r.state === "SAFE_PAUSE" && !manual.some(([key]) => key === id) && !bumped.some(([key]) => key === id) && !S.bed_exit.value && !monitorLocked(mon))
            stopLine(3, "안전 정지", [r]);
    }
    return issues.sort((a,b) => b.severity - a.severity);
}

function renderTop() {
    const fresh = S && performance.now() - received <= 2000;
    if (!fresh && S) {
        const stamp = S.stamp + (performance.now() - received) / 1000;
        for (const el of document.querySelectorAll("[data-yolo-until]")) {
            if (stamp >= Number(el.dataset.yoloUntil)) el.remove();
        }
    }
    $("connection").textContent = fresh ? "관제 연결 정상" : "관제 연결 끊김";
    $("connection").className = fresh ? "" : "red-text";
    document.querySelector(".mode").textContent = (cfg.mode === "demo" || (cfg.mode === "auto" && (cfg.demo || S?.demo))) ? "시연 모드" : "실기 모드";
    $("clock").textContent = tm(Date.now() / 1000);
    const paused = Object.values(S?.robots || {}).some(r => r.state === "SAFE_PAUSE");
    $("safety").textContent = paused ? "전체 재개" : "안전 정지 요청";
    $("safety").title = paused ? resumeReason() : "";
    $("safety").disabled = !!$("safety").title;
    const [monitor, tone] = monitorBadge();
    $("bed-monitor").textContent = monitor;
    $("bed-monitor").className = tone;
    $("online").textContent = "로봇 " + (S?.summary?.online ?? 0) + "대 연결";
    $("active-count").textContent = "진행 작업 " + (S?.summary?.active ?? 0) + "건";
    $("queued-count").textContent = "배정 대기 " + (S?.missions.filter(m => m.status === "QUEUED").length ?? 0) + "건";
    const issues = situation(fresh), top = issues[0];
    $("situation").className = top ? top.tone || (top.severity < 3 ? "attention" : "risk") : "";
    const noticeButtons = [...cards.values()].map(c => btn("결정 · " + (cardNames[c.kind] || c.title), "card", c.card_id));
    if (alerts.length) noticeButtons.push(btn("경보 " + alerts.length, "alarm"));
    if (top?.basis) noticeButtons.unshift(btn("판단 근거", "basis", "", "", !basisEvent()));
    if (top?.severity === 4) noticeButtons.push(btn(sound ? "경보음 끄기" : "경보음 켜기", "sound"));
    const message = '<strong>' + h(top?.title || "이상 없음") + '</strong><span>' +
        h(top?.detail || "로봇 " + (S?.summary?.online ?? 0) + "대가 정상 운행 중입니다.") + '</span>';
    const lane = top?.basis?.type === "ZONE_OCCUPIED_HOLD" ? '<div class="zone-notice">' + message + '</div>' : message;
    put("situation", lane + (issues.length > 1 ? '<b>외 ' + (issues.length - 1) + '건</b>' : '') +
        '<div class="buttons">' + noticeButtons.join("") + '</div>');
    if (modal === "alarm" && alerts[0]) {
        const [action, tone] = alarmAction(alerts[0]);
        $("alarm-at").textContent = tm(alerts[0].ts);
        $("elapsed").dataset.since = alerts[0].ts;
        $("alarm-action").textContent = action;
        $("alarm-action").className = tone;
        $("mute").textContent = sound ? "경보음 끄기" : "경보음 켜기";
    }
    const elapsed = $("elapsed");
    if (elapsed) {
        const seconds = Math.max(0, Date.now()/1000 - Number(elapsed.dataset.since));
        elapsed.textContent = elapsed.dataset.stopped === "true" ? Math.floor(seconds) + "초째 정지" : duration(seconds) + " 경과";
    }
    if (modal === "card" && modalCard) {
        const c = cards.get(modalCard);
        if (!c) {
            closeModal();
            nextNotice();
            return;
        }
        if (c.context === "PROXIMITY") {
            const near = S?.robots[c.robots[0]]?.proximity;
            for (const choice of ["peer_first", "self_first"]) {
                const button = document.querySelector(`[data-act="choose"][data-id="${choice}"]`);
                if (button) button.disabled = !near?.[choice];
            }
        }
        if (c.timeout_s != null) {
            const rest = Math.max(0, c.created_at + c.timeout_s - (S?.stamp || Date.now() / 1000) - (performance.now() - received) / 1000);
            if ($("card-clock"))
                $("card-clock").textContent = Math.ceil(rest) + "초 남음";
            if ($("card-bar"))
                $("card-bar").value = rest;
        }
    }
}
const cardNames = { BED_MONITOR_LOST: "침상 감시 끊김", BLOCKED: "경로 막힘", DEADLOCK: "교착 해소", NAV_FAILED: "주행 실패", MOVE_REJECTED: "이동 거절", STOP_MISMATCH: "확인 불일치", STOP_UNCONFIRMED: "정지 미확인" };

const choiceDescriptions = {
    BLOCKED: {retry:"앞이 비었는지 확인한 뒤 원래 경로로 다시 갑니다.", return:"이전 구역으로 돌아갑니다.", hold:"현재 위치에서 멈춘 채 운영자의 재시도를 기다립니다."},
    ZONE: {retry:"목적지 점유를 다시 확인하고 비었을 때 출발합니다.", return:"이전 구역을 목적지로 바꾸고 출발 조건을 확인합니다.", hold:"현재 작업을 보류하고 운영자의 재시도를 기다립니다."},
    EXIT: {retry:"2번 자리가 비었는지 다시 확인합니다.", depart:"이 출발에 한해 출구 대기를 풉니다. 접근하면 근접 규칙으로 정지합니다.", hold:"현재 작업을 보류합니다."},
    PROXIMITY: {self_first:"상대가 서 있고 거리가 줄지 않는 동안 이 로봇이 먼저 갑니다.", retry:"두 로봇의 거리를 다시 확인합니다. 재개 간격이 확보되면 출발합니다.", return:"현재 구간을 취소하고 이전 구역으로 돌아갈 조건을 확인합니다.", hold:"이 로봇의 임무를 보류합니다.", peer_first:"이 로봇이 멈춘 동안 이동할 수 있는 상대 로봇을 먼저 보냅니다."},
    DEADLOCK: {yield_a:"침상형 이송 로봇이 대피 구역으로 이동합니다.", yield_b:"약품 운송 로봇이 대피 구역으로 이동합니다."},
    BED_MONITOR_LOST: {hold:"감시 신호를 확인할 때까지 정지를 유지합니다.", bypass:"운영자 확인으로 침상 감시 없이 운행을 허용합니다."},
    NAV_FAILED: {retry:"같은 목적지로 다시 출발합니다.", return:"이전 구역으로 돌아갑니다.", cancel:"현재 작업을 취소합니다."},
    MOVE_REJECTED: {retry:"같은 이동 명령을 다시 보냅니다.", cancel:"현재 작업을 취소합니다."},
    STOP_MISMATCH: {recheck:"현재 정류장에서 다시 확인합니다.", skip:"이 정류장을 건너뛰고 진행합니다.", cancel:"현재 작업을 취소합니다."},
    STOP_UNCONFIRMED: {resend:"정지 명령을 다시 보내고 멈췄다는 보고를 다시 기다립니다.", confirmed:"현장에서 로봇이 멈춘 것을 봤습니다. 정지 명령을 더 보내지 않습니다."}
};
const elapsedSince = (t, stopped = false) => `<time id="elapsed" data-since="${t}" data-stopped="${stopped}"></time>`;
const factList = pairs => "<dl>" + pairs.map(([k,v,id]) => `<dt>${h(k)}</dt><dd${id ? ` id="${h(id)}"` : ""}>${h(v)}</dd>`).join("") + "</dl>";
function showCard(id) {
    const c = cards.get(id);
    if (!c)
        return;
    const r = S?.robots[c.robots[0]], job = mById(c.mission_id), def = c.options.find(o => o.id === c.default);
    const title = c.head_on ? "마주 봄" : c.context === "EXIT" ? "출구 막힘 대기" : c.context === "ZONE" ? "목적지 대기" : c.context === "PROXIMITY" ? "로봇 근접 정지" : c.kind === "BLOCKED" && r ? r.label + "이 막혔습니다" : c.title;
    const stalled = !c.context && c.kind === "BLOCKED" && r?.stall_s > 0;
    const facts = [["작업", job ? job.mission_id + " " + cfg.kinds[job.type] + ", Level " + job.level : c.mission_id || "직접 이동 또는 임무 없음"],
        [c.kind === "STOP_UNCONFIRMED" ? "지금 위치" : "멈춘 곳", locationText(r)]];
    if (c.head_on) facts.push(["되돌아가기 추천", robotName(c.robots[0])]);
    if (c.kind === "BLOCKED" && !c.context && !c.obstacle) facts.push(["앞에 있는 것", obstacleText(r || {}) || "분류 결과 없음"]);
    const photo = c.obstacle ? `<figure class="obstacle-photo" id="obstacle-photo" hidden><img alt="${h((r?.label || "로봇") + "이 찍은 장애물 사진")}"><figcaption></figcaption></figure>` : "";
    openModal("card", `<header class="pop-head attention">${r ? robotMark(r) : ""}<h2>${h(title)}</h2>${elapsedSince(stalled ? S.stamp-r.stall_s : c.created_at, stalled)}</header>
        <div class="pop-body"><p class="lead">${h(c.reason)}</p>${photo}${factList(facts)}
        <h3>어떻게 할까요?</h3><div class="choices">${c.options.map(o =>
            `<button data-act="choose" data-id="${h(o.id)}" ${c.context === "PROXIMITY" && ["peer_first","self_first"].includes(o.id) && !r?.proximity?.[o.id] ? "disabled" : ""}><strong>${h(o.label)}${o.id === c.recommended ? " · 추천" : ""}</strong>
            <small>${h(choiceDescriptions[c.context || c.kind]?.[o.id] || "선택한 로봇이 대피 구역으로 이동합니다.")}</small></button>`).join("")}</div>
        ${def ? `<p>시간이 지나면 ‘${h(def.label)}’로 자동 진행합니다. <b id="card-clock"></b></p><progress id="card-bar" max="${c.timeout_s}"></progress>` : ""}
        <footer class="row"><small>운영자의 결정을 기다립니다.</small>${btn("나중에", "defer")}</footer></div>`);
    modalCard = id;
    renderTop();
    if (c.obstacle) showObstaclePhoto(c);
}
// The PART 06 drawn photo if it came, else PART 04's own photo. No photo: the card keeps only its sentence.
async function showObstaclePhoto(c) {
    const robot = c.robots[0];
    for (const kind of ["masked", "raw"]) {
        let reply;
        try { reply = await fetch(`/api/obstacle_image/${encodeURIComponent(robot)}?kind=${kind}`, {cache: "no-store"}); }
        catch { return; }
        if (!reply.ok) continue;
        const url = URL.createObjectURL(await reply.blob());
        const figure = $("obstacle-photo");
        if (modalCard !== c.card_id || !figure) { URL.revokeObjectURL(url); return; }
        const o = c.obstacle, image = figure.querySelector("img");
        image.onload = () => URL.revokeObjectURL(url);
        image.src = url;
        figure.querySelector("figcaption").textContent = kind === "masked" && o.label_ko
            ? `판별: ${o.label_ko} ${Number(o.confidence).toFixed(2)}`
            : "로봇이 찍은 사진 " + tm(Number(reply.headers.get("X-Received-At")));
        figure.hidden = false;
        return;
    }
}
const alarmKinds = { BED_MONITOR_LOST: "red", BED_EXIT: "red", BUMP: "red", BLOCKED: "amber", LINK_LOST: "red", FAULT: "amber" };
const acknowledgedAlarms = new Map(), lastAlarmPause = new Map(), monitorWarnings = new Map();

function alarmTargets(e) {
    if (e.detail.targets) return e.detail.targets;
    if (e.robot) return [e.robot];
    return ["BED_EXIT", "BED_MONITOR_LOST"].includes(e.type) ? (cfg.bed_targets || []) : [];
}

const alarmKey = e => JSON.stringify([e.type, [...new Set(alarmTargets(e))].sort()]);

function clearLostAlarms(recoveredAt) {
    const recovered = e => e?.type === "BED_MONITOR_LOST" && e.ts <= recoveredAt;
    const close = modal === "alarm" && recovered(alerts[0]);
    alerts = alerts.filter(e => !recovered(e));
    if (close) closeModal();
}

function queueAlarm(e) {
    const key = alarmKey(e);
    const i = alerts.findIndex(other => alarmKey(other) === key);
    if (i >= 0) {
        // The event journal retains each original timestamp. Update only the popup copy.
        alerts[i] = {...alerts[i], ts: Math.max(alerts[i].ts, e.ts)};
    } else if (acknowledgedAlarms.has(key)) {
        acknowledgedAlarms.set(key, e);
    } else {
        alerts.push(e);
        beep();
        if (modal !== "alarm") alarm();
    }
}

function handleAlarmEvent(e) {
    if (alarmKinds[e.type]) {
        queueAlarm(e);
    } else if (e.type === "PAUSE" && alarmKinds[e.detail.cause]) {
        const stop = JSON.stringify([e.detail.cause, e.robot]);
        if (e.ts <= (lastAlarmPause.get(stop) ?? -Infinity)) return;
        lastAlarmPause.set(stop, e.ts);
        // A bumper alarm can cover both robots. Reopen only the matching alarm.
        const matches = alarm => alarm.type === e.detail.cause && alarmTargets(alarm).includes(e.robot);
        const previous = [...alerts, ...acknowledgedAlarms.values()].filter(matches).sort((a,b) => b.ts - a.ts)[0];
        if (previous && e.ts >= previous.ts && acknowledgedAlarms.delete(alarmKey(previous))) {
            queueAlarm({...previous, ts: e.ts, mission_id: e.mission_id ?? previous.mission_id});
        }
    } else if (e.type === "BED_MONITOR_BACK") {
        clearLostAlarms(e.ts);
    } else if (e.type === "BED_MONITOR_WARN") {
        const message = e.detail.reason, now = performance.now();
        if (now - (monitorWarnings.get(message) ?? -Infinity) >= 30_000) {
            monitorWarnings.set(message, now);
            toast(message, false);
        }
    }
}

function dismissAlarm() {
    const entry = alerts.shift();
    if (entry) acknowledgedAlarms.set(alarmKey(entry), entry);
    closeModal();
    nextNotice();
}
// 4K (D-32): the control's stop causes are a request. Only the robot's STATE confirms it stopped:
// PAUSED, or a state that is not driving at all. A robot whose STATE is not arriving is not confirmed.
const stillStates = new Set(["PAUSED", "IDLE", "COMPLETE", "CANCELED", "FAILED"]);
const stopConfirmed = r => r.link_ok !== false && stillStates.has(r.part4_state);
function stopAction(robots) {
    const done = robots.filter(stopConfirmed), waiting = robots.filter(r => !stopConfirmed(r));
    const text = [done.length ? done.map(r => r.label).join(", ") + " 정지 확인" : "",
        waiting.length ? waiting.map(r => r.label).join(", ") + " 정지 요청 중(확인 전)" : ""].filter(Boolean).join(" · ");
    return [text, waiting.length ? "amber-text" : "red-text"];
}

function alarmAction(e) {
    if (e.type === "BED_MONITOR_LOST") {
        const targets = e.detail.targets || [];
        const paused = targets.filter(id => S?.robots[id]?.state === "SAFE_PAUSE");
        return paused.length ? stopAction(paused.map(id => ({...S.robots[id], label: robotName(id)}))) : ["대상 로봇 출발 제한", ""];
    }
    if (e.type === "BED_EXIT" || e.type === "BUMP") {
        const robots = Object.values(S?.robots || {}).filter(r => (r.pause_causes || [r.paused_by]).includes(e.type));
        return robots.length ? stopAction(robots) : ["안전 정지 상태 확인 중", ""];
    }
    return [{ BLOCKED: "신규 진입 보류 · 장애물 확인 요청", LINK_LOST: "연결 복구 대기", FAULT: "해당 구간 중단 · 운영자 판단 대기" }[e.type], ""];
}

function alarm() {
    if (!alerts.length)
        return;
    const e = alerts[0];
    const targets = alarmTargets(e);
    const robot = S?.robots[targets[0]], job = mById(e.mission_id || robot?.mission_id);
    const facts = [["로봇", targets.map(robotName).join(" · ") || "관제"], ["작업", job ? job.mission_id + " " + cfg.kinds[job.type] : "배정된 작업 없음"], ["감지 시각", tm(e.ts), "alarm-at"]];
    const [action, tone] = alarmAction(e);
    openModal("alarm", `<header class="pop-head ${alarmKinds[e.type] === "red" ? "alarm-head" : "attention"}"><b aria-hidden="true">⚠</b><h2>${h(e.title)}</h2>${elapsedSince(e.ts)}</header>
        <div class="pop-body"><p class="lead">${h(e.type === "BED_EXIT" ? "침상에서 환자가 일어났습니다. 침상 카메라로 환자 상태를 확인하세요." : e.detail.reason)}</p>
        ${factList(facts)}<div class="action-fact">관제 조치 <b id="alarm-action" class="${tone}">${h(action)}</b></div>
        <div class="buttons">${btn("확인", "dismiss", "", "alarm-ok")}<button id="mute" data-act="sound">경보음 끄기</button></div>
        <small>확인 후에도 현재 상황은 위쪽 상황 줄에서 볼 수 있습니다.</small></div>`);

}

function nextNotice() { if (modal)
    return; if (alerts.length) {
    alarm();
    return;
} const c = [...cards.values()].find(c => !c.deferred); if (c)
    showCard(c.card_id); }

function beep() { if (!sound || !audioContext || audioContext.state !== "running")
    return; const o = audioContext.createOscillator(), g = audioContext.createGain(); o.frequency.value = 630; g.gain.value = .04; o.connect(g); g.connect(audioContext.destination); o.start(); o.stop(audioContext.currentTime + .13); }
Object.assign(actions, { close: () => { if (modal === "card")
        cards.get(modalCard).deferred = true; closeModal(); nextNotice(); }, defer: () => { cards.get(modalCard).deferred = true; closeModal(); nextNotice(); },
    card: showCard, alarm, dismiss: dismissAlarm, sound: () => { sound = !sound; renderTop(); },
    choose: (id, b) => send("decision", { card_id: modalCard, choice: id }, b),
    safety: (_, b) => command(b.textContent === "전체 재개" ? "RESUME_ALL" : "PAUSE_ALL", {}, b),
    cancel: id => openModal("confirm", dialogHead("임무 취소") + `<p>${h(id)} 임무를 취소하시겠습니까?</p>` + btn("임무 취소 실행", "cancel-ok", id, "danger")),
    "cancel-ok": async (id, b) => { const ack = await command("CANCEL_MISSION", { mission_id: id }, b); if (ack?.ok)
        closeModal(); },
    clear: id => openModal("confirm", dialogHead("현장 확인 후 " + resLabel(id) + " 비움") + `<p>현장에서 ${h(resLabel(id))}의 점유 상태를 확인했습니까?</p>` + btn(resLabel(id) + " 비움 확인", "clear-ok", id, "danger")),
    "clear-ok": async (id, b) => { if ((await command("RESOURCE_CLEAR_CONFIRM", { resource: id }, b))?.ok)
        closeModal(); },
    inspect, quick: () => quick("patient") });
document.addEventListener("click", e => {
    if (!audioContext && window.AudioContext) {
        audioContext = new AudioContext();
        audioContext.resume().catch(() => { });
    }
    const b = e.target.closest("[data-act]");
    if (b && !b.disabled)
        actions[b.dataset.act]?.(b.dataset.id, b);
});
$("dialog").addEventListener("cancel", e => { e.preventDefault(); if (modal === "alarm")
    actions.dismiss();
else
    actions.close(); });

function loadPage() {
    historySlots.clear();
    const target = location.hash.slice(2);
    page = nav[target] ? target : "dashboard";
    if (!nav[target])
        history.replaceState(null, "", "#/dashboard");
    closeModal();
    $("heading").hidden = page === "dashboard";
    $("title").textContent = nav[page][0];
    $("subtitle").textContent = nav[page][1];
    $("nav").innerHTML = Object.entries(nav).map(([key, v], i) => `<a href="#/${key}" class="${page === key ? "active" : ""}">${v[0]}</a>`).join("");
    initializers[page]();
    render();
    nextNotice();
    window.scrollTo(0, 0);
}
