"use strict";
const $ = id => document.getElementById(id);
const h = v => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nav = { dashboard: ["종합 관제", "로봇 운행과 이송 작업을 실시간으로 확인하세요."], missions: ["이송 작업", "요청부터 완료까지 이송 작업의 진행 상황을 확인하세요."], patient: ["환자 등록", "환자 정보와 이동 경로를 등록하세요."], supply: ["의료물품 요청", "수령지와 전달지를 지정하고 인계 과정을 확인하세요."], log: ["운영 기록", "로봇 운행과 관제 판단을 시간순으로 확인하세요."] };
const robotName = r => S?.robots[r]?.label || ({ robot_a: "Robot A", robot_b: "Robot B" })[r] || "관제";
const zn = z => cfg.names[z] || "구역 미확인";
const resNames = { FREE: "비어 있음", GRANTED: "허가", OCCUPIED: "점유 중", UNKNOWN: "점유 불명" };
const routeText = m => m.stops.map(zn).join(" → ");
const tm = t => t == null ? "미수신" : new Date(t * 1000).toLocaleTimeString("ko-KR", { hour12: false });
const duration = n => [Math.floor(Math.max(0, n) / 60), Math.floor(Math.max(0, n) % 60)].map(v => String(v).padStart(2, "0")).join(":");
const pill = (text, color = "") => `<span class="badge ${color}">${h(text)}</span>`;
const btn = (text, act, id = "", cls = "", disabled = false) => `<button type="button" class="${cls}" data-act="${act}" data-id="${h(id)}" ${disabled ? "disabled" : ""}>${h(text)}</button>`;
const zoneOptions = initial => Object.keys(cfg.zones).map(z => `<option value="${z}" ${z === initial ? "selected" : ""}>${h(zn(z))}</option>`).join("");
const stateColor = s => ({ WAIT_CONFIRM: "purple", SAFE_PAUSE: "red", OFFLINE: "red", FAULT: "red", HOLD: "amber", GIVE_WAY: "amber", YIELDING: "amber", MOVING: "blue", SENDING: "blue" })[s] || "";
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

function inspect(id) { selected = id; missionFilter = { tab: mById(id)?.status || "ACTIVE" }; navigate("missions"); }

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

function renderBed() {
    const m = S?.bed_exit.monitor, badge = $("bed-monitor"), messages = [];
    let label = "침상 감시 연결 안 됨", color = "";
    if (S?.bed_exit.value)
        messages.push("침상 이탈 감지 · 이탈이 해제될 때까지 전체 재개가 제한됩니다.");
    if (m) {
        const elapsed = duration(S.stamp - (m.last_rx ?? m.since));
        if (m.bypass) {
            label = "감시 없이 운행 중";
            color = "red bypass";
            messages.push("침상 감시 없이 운행 중");
        } else if (monitorLocked(m)) {
            color = "red";
            if (m.state === "LOST")
                label = "침상 감시 끊김 " + elapsed;
            const stopped = (m.targets || cfg.bed_targets || []).filter(id => S.robots[id]?.state === "SAFE_PAUSE").map(robotName);
            messages.push((m.state === "LOST" ? "침상 감시 끊김" : label) + " — " +
                (stopped.length ? stopped.join(", ") + " 정지" : "대상 로봇 출발 제한"));
        } else if (m.state === "WARN") {
            label = m.warning === "gap" ? "침상 감시 끊김 " + elapsed : "침상 감시 불안정";
            color = "amber";
        } else if (m.state === "OK") {
            label = "침상 감시 " + Math.round(m.rate_hz) + " Hz";
            color = "green";
        }
    }
    badge.className = "badge " + color;
    if (badge.textContent !== label) badge.textContent = label;
    $("bed").hidden = !messages.length;
    $("bed").textContent = messages.join(" · ");
}

function renderTop() {
    if (modal === "alarm" && alerts.length && $("alarm-action"))
        $("alarm-action").textContent = alarmAction(alerts[0]);
    const fresh = S && performance.now() - received <= 2000;
    $("connection").textContent = fresh ? "관제 연결 정상" : "관제 연결 끊김";
    $("connection").className = fresh ? "online" : "offline";
    document.querySelectorAll(".mode").forEach(n => n.textContent = (cfg.mode === "demo" || (cfg.mode === "auto" && (cfg.demo || S?.demo))) ? "시연 모드" : "실기 모드");
    $("clock").textContent = tm(Date.now() / 1000);
    const paused = Object.values(S?.robots || {}).some(r => r.state === "SAFE_PAUSE");
    $("safety").textContent = paused ? "전체 재개" : "안전 정지 요청";
    $("safety").title = paused ? resumeReason() : "";
    $("safety").disabled = !!$("safety").title;
    renderBed();
    put("inbox", [...cards.values()].map(c => btn("결정 · " + (cardNames[c.kind] || c.title), "card", c.card_id, "amber")).join("") + (alerts.length ? btn("경보 " + alerts.length, "alarm", "", "danger") : "") + btn(sound ? "경보음 켜짐" : "경보음 꺼짐", "sound", "", "quiet"));
    if (modal === "card" && modalCard) {
        const c = cards.get(modalCard);
        if (!c) {
            closeModal();
            nextNotice();
            return;
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
const cardNames = { BED_MONITOR_LOST: "침상 감시 끊김", BLOCKED: "경로 막힘", DEADLOCK: "교착 해소", NAV_FAILED: "주행 실패", MOVE_REJECTED: "이동 거절", STOP_MISMATCH: "확인 불일치" };

function showCard(id) {
    const c = cards.get(id);
    if (!c)
        return;
    const def = c.options.find(o => o.id === c.default);
    openModal("card", `<span class="eyebrow">운영자 결정</span>${dialogHead(c.title === c.kind ? (cardNames[c.kind] || c.title) : c.title)}<p class="notice">${h(c.reason)}</p><p>${h(c.robots.map(robotName).join(" · "))}</p>${c.timeout_s == null ? "<p>시간 제한 없음</p>" : `<span id="card-clock"></span><progress id="card-bar" max="${c.timeout_s}" value="${c.timeout_s}"></progress>`}${def ? `<p>시간이 지나면 ‘${h(def.label)}’로 자동 진행</p>` : ""}<div class="buttons">${c.options.map(o => btn(o.label + (o.id === c.recommended ? " · 추천" : ""), "choose", o.id, o.id === c.recommended ? "primary" : "")).join("")}${btn("나중에", "defer")}</div>`);
    modalCard = id;
    renderTop();
}
const alarmKinds = { BED_MONITOR_LOST: "red", BED_EXIT: "red", BUMP: "red", BLOCKED: "amber", LINK_LOST: "amber", FAULT: "amber" };

function alarmAction(e) {
    if (e.type === "BED_MONITOR_LOST") {
        const targets = e.detail.targets || [];
        const paused = targets.filter(id => S?.robots[id]?.state === "SAFE_PAUSE");
        return paused.length ? paused.map(robotName).join(", ") + " 안전 정지" : "대상 로봇 출발 제한";
    }
    if (e.type === "BED_EXIT" || e.type === "BUMP") {
        const robots = Object.values(S?.robots || {}).filter(r => r.paused_by === e.type);
        return robots.length ? robots.map(r => r.label).join(", ") + " 안전 정지" : "안전 정지 상태 확인 중";
    }
    return { BLOCKED: "신규 진입 보류 · 장애물 확인 요청", LINK_LOST: "연결 복구 대기", FAULT: "해당 구간 중단 · 운영자 판단 대기" }[e.type];
}

function alarm() {
    if (!alerts.length)
        return;
    const e = alerts[0];
    openModal("alarm", `<span class="badge ${alarmKinds[e.type]}">운행 경보</span><h2>${h(e.title)}</h2><p>${h(robotName(e.robot))} · ${tm(e.ts)}</p><p class="notice">${h(e.detail.reason)}</p><p id="alarm-action">${h(alarmAction(e))}</p>${btn("확인", "dismiss", "", "primary")}`);
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
    card: showCard, alarm, dismiss: () => { alerts.shift(); closeModal(); nextNotice(); }, sound: () => { sound = !sound; renderTop(); },
    choose: (id, b) => send("decision", { card_id: modalCard, choice: id }, b),
    safety: (_, b) => send("service/" + (b.textContent === "전체 재개" ? "resume" : "pause"), {}, b),
    cancel: id => openModal("confirm", dialogHead("임무 취소") + `<p>${h(id)} 임무를 취소하시겠습니까?</p>` + btn("임무 취소 실행", "cancel-ok", id, "danger")),
    "cancel-ok": async (id, b) => { const ack = await command("CANCEL_MISSION", { mission_id: id }, b); if (ack?.ok)
        closeModal(); },
    clear: () => openModal("confirm", dialogHead("현장 확인 후 복도 비움") + "<p>현장에서 복도의 점유 상태를 확인했습니까?</p>" + btn("복도 비움 확인", "clear-ok", "", "danger")),
    "clear-ok": async (_, b) => { if ((await command("RESOURCE_CLEAR_CONFIRM", { resource: "CORRIDOR" }, b))?.ok)
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
    const target = location.hash.slice(2);
    page = nav[target] ? target : "dashboard";
    if (!nav[target])
        history.replaceState(null, "", "#/dashboard");
    closeModal();
    $("title").textContent = nav[page][0];
    $("subtitle").textContent = nav[page][1];
    $("nav").innerHTML = Object.entries(nav).map(([key, v], i) => `<a href="#/${key}" class="${page === key ? "active" : ""}"><span>${["▦", "⇄", "♙", "✚", "≡"][i]}</span>${v[0]}</a>`).join("");
    initializers[page]();
    render();
    nextNotice();
    window.scrollTo(0, 0);
}
