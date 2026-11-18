const tabs = { ACTIVE: "진행 중", QUEUED: "배정 대기", DONE: "완료", CANCELED: "취소", OPEN: "미완료" };

function jobTable(jobs) {
    if (!jobs.length)
        return empty("이 조건에 해당하는 작업이 없습니다.");
    return `<div class="tablewrap"><table><thead><tr>${["작업 / 대상", "이송 경로", "응급도", "현재 상태", "다음 업무"].map(t => "<th>" + t + "</th>").join("")}</tr></thead><tbody>${jobs.map(m => {
        const r = S.robots[m.robot], active = m.status === "ACTIVE", next = m.stops[m.stop_index];
        return `<tr data-act="inspect" data-id="${h(m.mission_id)}"><td><button class="table-link" data-act="inspect" data-id="${h(m.mission_id)}">${h(m.mission_id)}</button><small>${h(missionTarget(m))}</small></td><td>${h(routeText(m))}</td><td>${pill("Level " + m.level, m.level === 3 ? "purple" : "")}</td><td>${h(m.robot ? robotName(m.robot) : "미배정")}<small>${h(active && r ? cfg.states[r.state] : tabs[m.status])}</small></td><td>${h(active ? (r?.state === "WAIT_CONFIRM" ? "도착 확인" : zn(next) + " 이동") : m.wait_reason || tabs[m.status])}</td></tr>`;
    }).join("")}</tbody></table></div>`;
}

function selectFilter(key, values, labels = {}) {
    return `<select aria-label="${key}" data-filter="${key}"><option value="">${({ kind: "모든 종류", robot: "모든 로봇", level: "모든 응급도", type: "모든 이벤트" })[key]}</option>${values.map(v => `<option value="${h(v)}" ${String(missionFilter[key]) === String(v) ? "selected" : ""}>${h(labels[v] || v)}</option>`).join("")}</select>`;
}
initializers.missions = () => {
    $("page").innerHTML = '<div class="split"><section class="panel"><div id="mission-tabs"></div><div id="filters" class="filters"></div><div id="job-list"></div></section><aside id="detail" class="panel"></aside></div>';
    $("filters").innerHTML = selectFilter("kind", Object.keys(cfg.kinds), cfg.kinds) + selectFilter("robot", ["robot_a", "robot_b"], { robot_a: "Robot A", robot_b: "Robot B" }) + selectFilter("level", [1, 2, 3]);
    $("filters").onchange = e => { missionFilter[e.target.dataset.filter] = e.target.value; render(); };
};
actions.mtab = t => { missionFilter.tab = t; selected = null; render(); };
renderers.missions = () => {
    if (!S)
        return;
    const t = missionFilter.tab || "ACTIVE";
    put("mission-tabs", '<div class="tabs">' + Object.entries(tabs).filter(([k]) => k !== "OPEN" || t === "OPEN").map(([k, v]) => btn(v, "mtab", k, k === t ? "selected" : "")).join("") + "</div>");
    const jobs = S.missions.filter(m => (t === "OPEN" ? ["ACTIVE", "QUEUED"].includes(m.status) : m.status === t) && (!missionFilter.kind || m.type === missionFilter.kind) && (!missionFilter.robot || m.robot === missionFilter.robot) && (!missionFilter.level || m.level == missionFilter.level));
    put("job-list", jobTable(jobs));
    const m = mById(selected);
    if (!m) {
        put("detail", cardTitle("작업 상세") + empty("표에서 확인할 작업을 선택하세요."));
        return;
    }
    const r = S.robots[m.robot], active = m.status === "ACTIVE", events = E.filter(e => e.mission_id === m.mission_id);
    put("detail", `<span class="eyebrow">작업 상세</span>${cardTitle(h(m.mission_id), pill(tabs[m.status], "blue"))}<h3>${h(cfg.kinds[m.type])} · Level ${m.level}</h3><dl><dt>요청자</dt><dd>${h(m.requested_by || "요청자 미기록")}</dd><dt>접수 시각</dt><dd>${tm(m.created_at)}</dd><dt>대상</dt><dd>${h(m.patient ? [...new Set([m.patient.chart_no, m.patient.name].filter(Boolean))].join(" · ") : missionTarget(m))}</dd>${m.patient?.tag_id ? "<dt>손목 태그</dt><dd>" + h(m.patient.tag_id) + " · QR 연동 후 확인</dd>" : ""}${m.requesting_department ? "<dt>요청처</dt><dd>" + h(m.requesting_department) + "</dd>" : ""}<dt>담당 로봇</dt><dd>${h(m.robot ? robotName(m.robot) : "배정 대기")} · ${h(active && r ? cfg.states[r.state] : tabs[m.status])}</dd></dl>
 <ol class="steps">${m.stops.map((z, i) => `<li class="${i < m.stop_index ? "done" : active && i === m.stop_index ? "current" : ""}">${i < m.stop_index ? "✓ " : ""}${h(zn(z))}　${h(m.items?.[i] || "")}</li>`).join("")}</ol>
 ${m.wait_reason ? "<p class='notice'>" + h(m.wait_reason) + "</p>" : ""}${active && r?.state === "WAIT_CONFIRM" ? confirmBox(r, m.robot, m) : ""}<div class="buttons">${active && r?.state === "FAULT" ? btn("다시 시도", "retry", m.robot) : ""}${active || m.status === "QUEUED" ? btn("임무 취소", "cancel", m.mission_id, "danger") : ""}</div><hr><h3>작업 기록</h3>${events.map(e => `<p class="record"><time>${tm(e.ts)}</time> ${h(e.title)}<small>${h(e.detail.reason)}</small></p>`).join("") || empty("수신된 기록이 없습니다.")}`);
};
