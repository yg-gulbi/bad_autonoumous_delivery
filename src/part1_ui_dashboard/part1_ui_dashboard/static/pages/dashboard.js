let dashTab = "ACTIVE";
const checked = new Set(), goZones = {};
const missionTarget = m => m?.patient?.name || m?.patient?.chart_no || m?.items?.filter(Boolean).join(" · ") || (m ? cfg.kinds[m.type] : "임무 대기");
const cardTitle = (t, extra = "") => `<div class="row"><h2>${t}</h2>${extra}</div>`;

function confirmBox(r, id, m) {
    if (!m)
        return "";
    const key = m.mission_id + ":" + m.stop_index, final = m.stop_index === m.stops.length - 1;
    return `<div class="confirm-box"><strong>${h(zs(m.stops[m.stop_index], r.zone === m.stops[m.stop_index] ? r.slot : null))} 도착</strong><p>${h(m.items?.[m.stop_index] || m.patient?.name || m.patient?.chart_no || cfg.kinds[m.type])} · 다음 ${h(final ? "홈 복귀" : zn(m.stops[m.stop_index + 1]))}</p>
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

function obstacleText(r) {
    if (r.obstacle_pending) return "장애물 분류 중…";
    const obs = r.obstacle;
    if (!obs) return "";
    if (obs.text) return obs.text;  // report mode: the sentence already says what PART 06 saw
    if (obs.kind === "PERSON") return "앞에 사람 감지";
    if (obs.kind === "OBJECT") {
        const names = obs.labels.map(label => obs.detections.find(d => d.label === label)?.label_ko || label);
        return "앞에 " + names.join(", ") + " 감지";
    }
    return obs.kind === "NONE" ? "카메라에 보이는 장애물 없음" : "장애물 분류 불가";
}

const locationText = r => r?.zone ? zs(r.zone, r.slot) : ({CORRIDOR:"복도 안",ROOM_B:zn("B"),ROOM_E:zn("E"),TOP:zn("A") + ", " + zn("D") + " 쪽",BOTTOM:zn("C") + ", " + zn("F") + " 쪽"})[r?.region] || "위치 미수신";
const robotMark = r => `<span class="mark ${r.mark === "B" ? "square" : ""}">${h(r.mark)}</span>`;
function destinationText(r, job) {
    if (!r.leg) return "—";
    let purpose = "직접 이동";
    if (r.leg.purpose) purpose = r.leg.purpose;
    else if (r.returning || r.state === "RETURNING") purpose = "홈 복귀";
    else if (job?.type === "PATIENT_TRANSPORT") purpose = job.stop_index === 0 ? "환자 탑승" : "환자 인계";
    else if (job?.type === "MED_ROUND") purpose = "정류장 " + (job.stop_index + 1) + "번째";
    else if (job?.type === "CALL") purpose = "수령 확인";
    return zs(r.leg.to, r.leg.slot) + " · " + purpose;
}
function robotCard(id, r) {
    const job = mById(r.mission_id), st = r.state, home = r.returning || st === "RETURNING";
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
    const mark = r.mark;
    const fields = [["가는 곳", destinationText(r, job)], ["지금", locationText(r)],
        ["배터리", (r.battery_pct == null ? "미수신" : Math.round(r.battery_pct) + "%") + (r.battery_status === "CHARGING" ? " 충전 중" : "")], ["연결", "/" + r.ns]];
    return `<div class="unit-title">${robotMark(r)}<div><h2>${h(r.label)}</h2><small>Robot ${h(mark)}</small></div>${pill(...displayState(id, r))}</div>
        <p>${h(job ? job.mission_id + "　" + cfg.kinds[job.type] + "　Level " + job.level : home ? (r.leg?.purpose === "도크 복귀" ? "도크 복귀" : "홈 구역 복귀") : r.leg ? "직접 이동" : "배정된 작업 없음")}</p>
        <div class="unit-facts">${fields.map(([key,value]) => `<span><small>${key}</small> ${h(value)}</span>`).join("")}</div>
        ${r.via ? `<p class="via-note">교차로 경유${r.via.passed ? " · 통과" : ""}</p>` : ""}
        ${r.yolo ? `<p data-yolo-until="${Number(r.yolo.at) + 30}">${h(r.yolo.text)} ${btn("감지 사진", "yolo-photo", id)}</p>` : ""}
        ${obstacleText(r) ? `<p>${h(obstacleText(r))}</p>` : ""}
        <div class="buttons">${controls}${job ? btn("작업 상세", "inspect", job.mission_id, "linkbtn") : ""}</div>`;
}

function roadPanel() {
    const c = S.resources.CORRIDOR, track = S.resume_track;
    const directions = [...new Set((c.holder_detail || []).map(entry => entry.direction))];
    const heading = directions.map(d => d > 0 ? "왼쪽에서 오른쪽 →" : d < 0 ? "← 오른쪽에서 왼쪽" : "횡단 또는 방향 미확인").join(", ");
    const waiting = [...new Set([...c.queue.map(q => q.robot), c.follow_stop?.rear, c.give_way?.yielding].filter(Boolean))];
    const labels = track?.kind === "follow" ? [c.follow_stop?.rule === "GAP" ? "재개 간격 확보" : "선행 로봇 재출발 또는 이탈", "뒤 로봇 연결", "별도 정지 없음", "출발 응답"] : track?.kind === "yield" ? ["우선 로봇 통과", "양보 로봇 연결", "별도 정지 없음", "양보 로봇 재출발"] :
        ["선행 로봇 완전 통과", "복도 비움 확인", "진입 허가", "출발 응답"];
    const passing = (c.holder_detail || []).filter(entry => !waiting.includes(entry.robot));
    return `<div class="road-line"><h2>복도</h2><strong>${h(passing.map(entry => robotName(entry.robot) + (stateColor(S.robots[entry.robot].state) === "red" ? " 정지" : entry.state === "OCCUPIED" ? " 통과 중" : " 진입 허가")).join(", ") || "통행 없음")}</strong>
        ${heading ? "<span>사용 방향　" + h(heading) + "</span>" : ""}<span>${waiting.length ? "대기　" + h(waiting.map(r => robotName(r) + ", " + locationText(S.robots[r])).join(", ")) : "대기 없음"}</span></div>
        ${waiting.length && track ? '<div class="checks"><b>출발 조건</b>' + labels.map((l,i) => `<span>${S.resume_checks[i] ? "☑" : "□"} ${l}</span>`).join("") + '</div>' : ""}
        ${Object.entries(S.resources).filter(([, res]) => res.state === "UNKNOWN")
            .map(([key]) => btn("현장 확인 후 " + resLabel(key) + " 비움", "clear", key, "danger")).join("")}`;
}

function basis(e) {
    if (!e)
        return;
    const d = e.detail;
    if (["ZONE_OCCUPIED_HOLD","PROXIMITY_STOP"].includes(e.type)) {
        const zone = e.type === "ZONE_OCCUPIED_HOLD";
        const access = ["NEAR_START","INBOUND","OUTBOUND"].includes(d.rule);
        openModal("basis", dialogHead(access ? "구역 출입 판단" : zone ? "목적지 점유 판단" : "로봇 근접 판단") +
            factList([["대기 로봇",robotName(e.robot)], ["상대 로봇",robotName(d.peer)],
                ...(zone ? [[access ? "구역" : "목적지",zn(access ? d.zone : d.destination)]] : []), ["판단 시각",tm(e.ts)]]) +
            '<p class="notice blue-surface">' + h(d.reason) + '</p>' + btn("판단 기록 보기","decision-log"));
        return;
    }
    openModal("basis", `<span class="eyebrow">복도</span>${dialogHead("통행 판단 상세")}<label>판단 당시 자원 상태</label><p class="tile">${h(resNames[d.state_before] || "상태 미수신")} · ${tm(e.ts)}</p><div>${(d.contenders || []).map(c => `<p><b class="${color(c.robot)}-text">${h(robotName(c.robot))}</b> · ${h(cfg.kinds[c.type] || "직접 이동")} · Level ${c.level} · ${tm(c.requested_at)}</p>`).join("")}</div>
 <p class="notice blue-surface"><b>${h(cfg.rules[d.rule] || e.title || "현장 상태 확인")}</b><br>${h(d.reason)}</p><p class="muted">동급이면 먼저 요청한 쪽, 허가된 통행은 회수하지 않음</p>${btn("판단 기록 보기", "decision-log")}`);
}
function basisEvent() {
    if (!S) return null;
    const top = situation(true)[0];
    if (top?.basis?.detail) return top.basis;
    const follow = S.resources.CORRIDOR.follow_stop;
    if (follow) return E.findLast(e => e.type === "CORRIDOR_FOLLOW_STOP" && e.robot === follow.rear) || S.last_decision;
    if ([...cards.values()].some(c => c.kind === "DEADLOCK")) return E.findLast(e => e.type === "DEADLOCK_DETECTED") || S.last_decision;
    if (Object.values(S.resources).some(r => r.state === "UNKNOWN")) return E.findLast(e => e.type === "RESOURCE_UNKNOWN") || S.last_decision;
    if (Object.values(S.resources).some(r => r.blocked)) return E.findLast(e => e.type === "BLOCKED") || S.last_decision;
    return S.last_decision;
}
Object.assign(actions, { basis: () => basis(basisEvent()), "decision-log": () => { logFilter = { group: "decision" }; navigate("log"); },
    go: (id, b) => command("GO_ZONE", { robot: id, zone: goZones[id] || Object.keys(cfg.zones)[0] }, b),
    pause: (id, b) => command("PAUSE_ROBOT", { robot: id }, b), resume: (id, b) => command("RESUME_ROBOT", { robot: id }, b), retry: (id, b) => command("RETRY", { robot: id }, b),
    confirm: (id, b) => command("CONFIRM_STEP", { robot: id, mission_id: S.robots[id].mission_id, result: "OK" }, b),
    mismatch: (id, b) => command("CONFIRM_STEP", { robot: id, mission_id: S.robots[id].mission_id, result: "MISMATCH" }, b),
    dashTab: t => { dashTab = t; render(); }, alljobs: () => { missionFilter = { tab: "ACTIVE" }; selected = null; navigate("missions"); }, logs: () => { logFilter = {}; navigate("log"); },
});
initializers.dashboard = () => {
    $("page").innerHTML = `<div class="board"><section class="floor"><div class="row"><h2>병원 평면도</h2>
        <div class="buttons"><label class="map-switch"><input type="checkbox" role="switch" id="overlay"> 실제 맵</label>${btn("−","zoom","0.8")}${btn("＋","zoom","1.25")}</div></div>
        <canvas id="map" aria-label="병원 구역과 로봇 실시간 위치"></canvas><div id="road"></div>
        <footer class="legend"><span>● A　침상형 이송 로봇</span><span>■ B　약품 운송 로봇</span><span>┄ 남은 경로</span><span>➜ 복도 사용 방향</span></footer></section>
        <aside class="units"><div id="robot-blocks"></div><section id="dash-jobs"></section></aside></div><section id="latest"></section>`;
    installMap();
};
renderers.dashboard = () => {
    if (!S) return;
    const ids = Object.keys(S.robots);
    put("robot-blocks", ids.map(id => `<article id="unit-${h(id)}"></article>`).join(""));
    for (const id of ids) put("unit-" + id, robotCard(id, S.robots[id]));
    paint();
    put("road", roadPanel());
    const jobs = S.missions.filter(m => m.status === dashTab);
    const controls = ["ACTIVE","QUEUED"].map(t => btn((t === "ACTIVE" ? "진행 중 " : "배정 대기 ") +
        S.missions.filter(m => m.status === t).length, "dashTab", t, t === dashTab ? "selected" : "")).join("");
    put("dash-jobs", '<div class="row"><h2>작업</h2><div class="buttons">' + controls + btn("전체 작업", "alljobs", "", "linkbtn") + '</div></div>' +
        '<table><thead><tr><th>작업</th><th>경로</th><th>응급도</th><th>로봇</th></tr></thead><tbody>' +
        jobs.map(m => `<tr><td>${btn(m.mission_id, "inspect", m.mission_id, "table-link")}<small>${h(cfg.kinds[m.type])}</small></td>
        <td>${h(routeText(m))}</td><td>Level ${m.level}</td><td>${h(m.robot ? robotName(m.robot) : "미배정")}</td></tr>`).join("") +
        '</tbody></table>' + (jobs.length ? "" : empty("해당 작업 없음")));
    put("latest", cardTitle("최근 기록", btn("전체 기록","logs","","linkbtn")) +
        (E.length ? E.slice(-3).reverse().map(e => `<div class="timeline"><time>${tm(e.ts)}</time><b>${h(e.title)}</b><span>${h(e.title.replace(/[ ·]/g,"") === e.detail.reason.replace(/[ ·]/g,"") ? "" : e.detail.reason)}</span></div>`).join("") : empty("수신된 이벤트가 없습니다.")));
};


actions["yolo-photo"] = id => {
    const sighting = S?.robots[id]?.yolo;
    if (!sighting) return;
    openModal("yolo-photo", dialogHead("주행 중 감지 사진") +
        `<figure class="obstacle-photo"><img id="yolo-image" alt="주행 중 감지 사진"><figcaption>${h(sighting.text)}</figcaption></figure>`);
    const image = $("yolo-image");
    image.onerror = () => { image.hidden = true; image.nextElementSibling.textContent = "이 감지에 해당하는 사진이 없습니다."; };
    image.src = `/api/obstacle_image/${encodeURIComponent(id)}?kind=yolo&at=${encodeURIComponent(sighting.at)}`;
};
