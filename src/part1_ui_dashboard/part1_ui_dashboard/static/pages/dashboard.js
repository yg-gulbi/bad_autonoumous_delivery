let dashTab = "ACTIVE";
const checked = new Set(), goZones = {};
const missionTarget = m => m?.patient?.name || m?.patient?.chart_no || m?.items?.filter(Boolean).join(" · ") || (m ? cfg.kinds[m.type] : "임무 대기");
const cardTitle = (t, extra = "") => `<div class="row"><h2>${t}</h2>${extra}</div>`;

function confirmBox(r, id, m) {
    if (!m)
        return "";
    const key = m.mission_id + ":" + m.stop_index, final = m.stop_index === m.stops.length - 1;
    return `<div class="confirm-box"><strong>${h(zn(m.stops[m.stop_index]))} 도착</strong><p>${h(m.items?.[m.stop_index] || m.patient?.name || m.patient?.chart_no || cfg.kinds[m.type])} · 다음 ${h(final ? "홈 복귀" : zn(m.stops[m.stop_index + 1]))}</p>
 <label><input type="checkbox" data-check="${key}" ${checked.has(key) ? "checked" : ""}> 확인했습니다</label><div class="buttons">${btn(final ? "수령 완료" : "확인 후 다음 이동", "confirm", id, "primary", !checked.has(key))}${btn("불일치", "mismatch", id)}</div></div>`;
}
document.addEventListener("change", e => { const key = e.target.dataset.check; if (key) {
    if (e.target.checked)
        checked.add(key);
    else
        checked.delete(key);
    render();
} if (e.target.dataset.go)
    goZones[e.target.dataset.go] = e.target.value; });

function robotCard(id, r) {
    const job = mById(r.mission_id), st = r.state, home = st === "RETURNING";
    let controls = "";
    if (st === "IDLE")
        controls = `<select aria-label="${r.label} 이동 구역" data-go="${id}">${zoneOptions(goZones[id])}</select>` + btn("보내기", "go", id);
    else if (st === "WAIT_CONFIRM")
        controls = confirmBox(r, id, job);
    else if (st === "SAFE_PAUSE") {
        const reason = resumeReason(id);
        controls = btn("재개", "resume", id, "", !!reason) + (reason ? `<small>${h(reason)}</small>` : "");
    }
    else if (st === "OFFLINE")
        controls = `<small>연결 끊김 · 마지막 수신 ${duration(S.stamp - (r.last_state_at ?? r.state_since))} 전</small>`;
    else
        controls = (st === "FAULT" ? btn("다시 시도", "retry", id) : btn("정지", "pause", id)) + btn("임무 취소", "cancel", r.mission_id || "", "", !r.mission_id);
    return cardTitle(h(r.label), pill(cfg.states[st], stateColor(st))) + `<small class="link-ind ${r.link_ok ? "up" : ""}">${r.link_ok ? "연결 정상" : "연결 끊김"} · /${h(r.ns)}</small><h3>${h(job ? job.mission_id + " · " + missionTarget(job) : home ? "홈 구역 복귀" : "배정된 작업 없음")}</h3>
 <dl><dt>현재</dt><dd>${h(r.wait_reason || (r.zone ? zn(r.zone) : r.region === "CORRIDOR" ? "복도" : r.region || "위치 미수신"))}</dd><dt>목적지</dt><dd>${h(r.leg ? zn(r.leg.from) + " → " + zn(r.leg.to) : "이동 구간 없음")}</dd></dl>
 <div class="row meta"><span>배터리 ${r.battery_pct == null ? "미수신" : Math.round(r.battery_pct) + "%"} · 멈춤 ${Math.floor(r.stall_s)}초</span>${job ? btn("작업 상세", "inspect", job.mission_id, "linkbtn") : ""}</div><div class="buttons">${controls}</div>`;
}

function roadPanel() {
    const c = S.resources.CORRIDOR, holders = c.holders, queue = c.queue_detail || [], track = S.resume_track;
    const list = holders.map(id => { const r = S.robots[id], m = mById(r.mission_id); return `<div class="holder"><strong class="${color(id)}-text">${h(r.label)}</strong><small>${m ? h("Level " + m.level + " · " + missionTarget(m)) : h(r.state === "RETURNING" ? "홈 복귀" : "구역 점유")}</small><progress max="100" value="${r.progress_pct || 0}"></progress></div>`; }).join("");
    const labels = !track ? [] : track.kind === "yield" ? [robotName(track.first) + " 지나감", robotName(track.next) + " 다시 출발"] : [robotName(track.first) + " 완전 통과 확인", "복도 비움 확인", robotName(track.next) + " 진입 허가", "재개 명령 전달 · 로봇 확인"];
    return cardTitle("복도", pill(c.blocked ? "막힘" : resNames[c.state], c.blocked || c.state === "UNKNOWN" ? "red" : holders.length ? "purple" : "green")) +
        `<label>현재 통행</label>${list || '<strong class="vacant">통행 로봇 없음</strong>'}<hr><label>다음 대기</label>${queue.length ? queue.map((q, i) => `<p><b>${i + 1}　${h(robotName(q.robot))}</b>　${pill("Level " + q.level, "blue")}<small>진입 전 안전 대기 · ${duration(S.stamp - q.since)}</small></p>`).join("") : '<p class="muted">대기 로봇 없음</p>'}
 ${c.give_way ? `<p class="notice">${h(robotName(c.give_way.yielding))}가 ${h(robotName(c.give_way.to))}에게 양보 중</p>` : ""}${c.blocked ? `<p class="warn">막힌 시간 ${duration(S.stamp - (c.blocked_since ?? S.stamp))}</p>` : ""}
 ${labels.length ? "<label>재개 조건</label>" + labels.map((l, i) => `<small>${S.resume_checks[i] ? "✓" : "○"} ${h(l)}</small>`).join("") : ""}
 <div class="rooms">${["B", "E"].map(z => `<small>${h(zn(z))} 출입 · ${resNames[S.resources["ROOM_" + z].state]}</small>`).join("")}</div>
 <div class="buttons">${btn("통행 판단 상세", "basis", "", "", !S.last_decision)}${c.state === "UNKNOWN" ? btn("현장 확인 후 복도 비움", "clear", "", "danger") : ""}</div>`;
}

function basis(e) {
    if (!e)
        return;
    const d = e.detail;
    openModal("basis", `<span class="eyebrow">복도</span>${dialogHead("통행 판단 상세")}<label>판단 당시 자원 상태</label><p class="tile">${h(resNames[d.state_before] || "상태 미수신")} · ${tm(e.ts)}</p><div>${(d.contenders || []).map(c => `<p><b class="${color(c.robot)}-text">${h(robotName(c.robot))}</b> · ${h(cfg.kinds[c.type] || "직접 이동")} · Level ${c.level} · ${tm(c.requested_at)}</p>`).join("")}</div>
 <p class="notice blue-surface"><b>${h(cfg.rules[d.rule])}</b><br>${h(d.reason)}</p><p class="muted">동급이면 먼저 요청한 쪽, 허가된 통행은 회수하지 않음</p>${btn("판단 기록 보기", "decision-log")}`);
}
Object.assign(actions, { basis: () => basis(S?.last_decision), "decision-log": () => { logFilter = { group: "decision" }; navigate("log"); },
    go: (id, b) => command("GO_ZONE", { robot: id, zone: goZones[id] || Object.keys(cfg.zones)[0] }, b),
    pause: (id, b) => command("PAUSE_ROBOT", { robot: id }, b), resume: (id, b) => command("RESUME_ROBOT", { robot: id }, b), retry: (id, b) => command("RETRY", { robot: id }, b),
    confirm: (id, b) => command("CONFIRM_STEP", { robot: id, mission_id: S.robots[id].mission_id, result: "OK" }, b),
    mismatch: (id, b) => command("CONFIRM_STEP", { robot: id, mission_id: S.robots[id].mission_id, result: "MISMATCH" }, b),
    dashTab: t => { dashTab = t; render(); }, alljobs: () => { missionFilter = { tab: "ACTIVE" }; selected = null; navigate("missions"); }, logs: () => { logFilter = {}; navigate("log"); },
    summary: id => { if (id === "online" || id === "wait") {
        const key = id === "wait" ? S.summary.waiting[0] : Object.keys(S.robots).find(k => !S.robots[k].link_ok);
        const card = $("unit-" + (key || "robot_a"));
        card?.scrollIntoView({ block: "center" });
        card?.classList.add("highlight");
        setTimeout(() => card?.classList.remove("highlight"), 1600);
    }
    else {
        missionFilter = { tab: id === "urgent" ? "OPEN" : "ACTIVE", level: id === "urgent" ? "3" : "" };
        selected = null;
        navigate("missions");
    } } });
initializers.dashboard = () => {
    $("page").innerHTML = `<div id="metrics"></div><div id="situation"></div><div class="board"><section class="panel map-panel">${cardTitle("병원 구역 지도", '<label class="map-switch"><input type="checkbox" id="overlay" role="switch">실제 맵</label>' + btn("−", "zoom", "0.8") + btn("＋", "zoom", "1.25"))}<p class="muted" id="map-note"></p><canvas id="map" aria-label="병원 구역과 로봇 실시간 위치"></canvas><footer class="legend"><span>🔵 Robot A</span><span class="violet-text">● Robot B</span><span>━ 계획 경로</span><span>구역 6 · 복도 1</span></footer></section><section class="panel road" id="road"></section><div class="units"><article class="panel" id="unit-robot_a"></article><article class="panel" id="unit-robot_b"></article></div></div>
 <div class="lower"><section class="panel" id="dash-jobs"></section><section class="panel" id="latest"></section></div>`;
    $("map-note").textContent = meta.floor_plan ? "실측 평면도" : meta.available ? "평면도 없음 · 실제 맵" : "맵 파일 없음 — 간이 지도";
    installMap();
};
renderers.dashboard = () => {
    if (!S)
        return;
    const info = S.summary, road = S.resources.CORRIDOR, units = Object.entries(S.robots);
    const waiting = info.waiting[0], robot = S.robots[waiting], urgent = mById(info.urgent[0]);
    const blocked = Object.values(S.resources).some(resource => resource.blocked_robots?.includes(waiting));
    let reason = robot?.wait_reason;
    if (!reason && blocked) {
        reason = E.findLast(e => e.robot === waiting && e.type === "BLOCKED")?.detail.reason || "이동 경로 막힘";
    } else if (!reason && robot?.state === "SAFE_PAUSE") {
        reason = E.findLast(e => e.robot === waiting && e.type === "PAUSE")?.detail.reason || "안전 정지";
    }
    const metric = (name, value, detail, cls, target) =>
        `<button class="metric" data-act="summary" data-id="${target}"><label>${name}</label><div><strong class="${cls}">${value}</strong><small>${h(detail)}</small></div></button>`;
    put("metrics", [
        metric("로봇 연결", info.online + " / " + units.length, units.filter(([,r]) => !r.link_ok).map(([,r]) => r.label).join(", ") || "모두 연결됨", "green-text", "online"),
        metric("진행 중 작업", info.active, `환자 ${info.kinds.PATIENT_TRANSPORT} · 물품 ${info.kinds.MED_ROUND} · 호출 ${info.kinds.CALL}`, "blue-text", "active"),
        metric("응급 작업", info.urgent.length, urgent ? urgent.mission_id + " · " + missionTarget(urgent) : "Level 3 작업 없음", "violet-text", "urgent"),
        metric("안전 대기", info.waiting.length, reason || cfg.states[robot?.state] || "대기 로봇 없음", "amber-text", "wait")
    ].join(""));
    const urgentHolder = road.holders.map(id => mById(S.robots[id].mission_id)).find(job => job?.level === 3);
    let title = "";
    if (road.state === "UNKNOWN") title = "복도 점유 불명";
    else if (road.blocked) title = "복도 막힘";
    else if (road.give_way) title = "복도 양보 중";
    else if ([...cards.values()].some(card => card.kind === "DEADLOCK")) title = "교착 해소 대기";
    else if (waiting) title = "안전 대기 중";
    else if (urgentHolder) title = urgentHolder.type === "MED_ROUND" ? "응급 의료물품 통행 중" : "응급 작업 통행 중";
    const message = reason || S.last_decision?.detail.reason || road.reason || "";
    put("situation", title ? `<div class="banner"><b>${h(title)}</b><span>${h(message)}</span>${btn("판단 근거", "basis", "", "", !S.last_decision)}</div>` : "");
    for (const [id, r] of units)
        put("unit-" + id, robotCard(id, r));
    put("road", roadPanel());
    paint();
    put("dash-jobs", cardTitle("이송 작업", btn("전체 작업", "alljobs")) + `<div class="tabs">${["ACTIVE", "QUEUED"].map(t => btn((t === "ACTIVE" ? "진행 중 " : "배정 대기 ") + S.missions.filter(m => m.status === t).length, "dashTab", t, t === dashTab ? "selected" : "")).join("")}</div>` + jobTable(S.missions.filter(m => m.status === dashTab)));
    put("latest", cardTitle("최근 운영 기록", btn("전체 기록", "logs", "", "linkbtn")) + (E.length ? E.slice(-8).reverse().map(e => `<button class="timeline" data-act="logs"><time>${tm(e.ts)}</time><div><b>${h(e.title || e.type)}</b><small>${h(e.detail.reason)}</small></div></button>`).join("") : empty("수신된 이벤트가 없습니다.")));
};
