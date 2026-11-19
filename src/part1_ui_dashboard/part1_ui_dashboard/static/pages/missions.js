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
    $("page").innerHTML = '<div class="split missions-layout"><section class="panel"><div id="mission-tabs"></div><div id="filters" class="filters"></div><div id="job-list"></div></section><aside id="detail" class="panel"></aside></div>';
    $("filters").innerHTML = selectFilter("kind", Object.keys(cfg.kinds), cfg.kinds) + selectFilter("robot", Object.keys(cfg.robot_names), cfg.robot_names) + selectFilter("level", [1, 2, 3]);
    $("filters").insertAdjacentHTML("beforeend", ["from","to"].map((k,i) =>
        `<label>${i ? "종료일" : "시작일"}<input type="date" data-filter="${k}" value="${h(missionFilter[k] || "")}"></label>`).join(""));
    $("filters").onchange = e => { missionFilter[e.target.dataset.filter] = e.target.value; missionFilter.page = 1; render(); };
};
actions.mtab = t => { missionFilter.tab = t; missionFilter.page = 1; selected = null; render(); };
actions.mpage = p => { missionFilter.page = Number(p); render(); };
function missionList(rows) {
    return historyTable(["임무 / 종류","대상","경로","응급도","로봇 / 상태","요청자","접수 시각"], rows.map(m =>
        `<tr data-act="inspect" data-id="${h(m.mission_id)}"><td>${btn(m.mission_id,"inspect",m.mission_id,"table-link")}<small>${h(cfg.kinds[m.type])}</small></td>` +
        cells([missionTarget(m), routeText(m), "Level " + m.level, (m.robot ? robotName(m.robot) : "미배정") +
            " · " + (tabs[m.status] || m.status), m.requested_by, stampText(m.created_at)]) + "</tr>"));
}
renderers.missions = () => {
    const f = missionFilter, t = f.tab || "ACTIVE";
    put("mission-tabs", '<div class="tabs">' + Object.entries(tabs).filter(([k]) => k !== "OPEN" || t === "OPEN").map(([k,v]) => btn(v,"mtab",k,k === t ? "selected" : "")).join("") + "</div>");
    if (t === "DONE" || t === "CANCELED") {
        const res = readHistory("missions","missions",{status:t, type:f.kind, robot:f.robot, level:f.level, from:f.from, to:f.to, page:f.page});
        put("job-list", res.data ? missionList(res.data.items) + pager(res.data, "mpage") : recordNotice(res));
    } else {
        const jobs = (S?.missions || []).filter(m => {
            const d = new Date(m.created_at * 1000);
            const day = [d.getFullYear(),String(d.getMonth()+1).padStart(2,"0"),String(d.getDate()).padStart(2,"0")].join("-");
            return (t === "OPEN" ? ["ACTIVE","QUEUED"].includes(m.status) : m.status === t) &&
                (!f.kind || m.type === f.kind) && (!f.robot || m.robot === f.robot) && (!f.level || m.level == f.level) &&
                (!f.from || day >= f.from) && (!f.to || day <= f.to);
        });
        put("job-list", missionList(jobs));
    }
    if (!selected) {
        put("detail", cardTitle("작업 상세") + empty("표에서 확인할 작업을 선택하세요."));
        return;
    }
    const result = readHistory("detail","missions/" + encodeURIComponent(selected));
    const stored = result.data, live = mById(selected);
    const m = stored?.mission || live;
    if (!m) { put("detail", cardTitle("작업 상세") + recordNotice(result)); return; }
    const r = S?.robots[m.robot], active = live?.status === "ACTIVE" && r?.mission_id === selected;
    const patient = m.patient, target = patient ? [patient.chart_no, patient.name].filter(Boolean).join(" · ") : missionTarget(m);
    put("detail", '<span class="eyebrow">작업 상세</span>' + cardTitle(h(m.mission_id),pill(tabs[m.status],"blue")) +
        `<h3>${h(cfg.kinds[m.type])} · Level ${h(m.level)}</h3><dl><dt>요청자</dt><dd>${h(m.requested_by || "미기록")}</dd><dt>접수 시각</dt><dd>${stampText(m.created_at)}</dd><dt>대상</dt><dd>${h(target)}</dd>` +
        (patient?.tag_id ? "<dt>손목 태그</dt><dd>" + h(patient.tag_id) + " · QR 연동 후 확인</dd>" : "") +
        (m.requesting_department ? "<dt>요청처</dt><dd>" + h(m.requesting_department) + "</dd>" : "") +
        (m.cancel_reason ? "<dt>취소 이유</dt><dd>" + h(m.cancel_reason) + "</dd>" : "") +
        "<dt>담당 로봇</dt><dd>" + h(m.robot ? robotName(m.robot) : "배정 대기") + "</dd></dl>" +
        (live?.wait_reason ? '<p class="notice">' + h(live.wait_reason) + "</p>" : "") +
        (active && r.state === "WAIT_CONFIRM" ? confirmBox(r,m.robot,live) : "") +
        '<div class="buttons">' + (active && r.state === "FAULT" ? btn("다시 시도","retry",m.robot) : "") +
        (active || live?.status === "QUEUED" ? btn("임무 취소","cancel",m.mission_id,"danger") : "") + "</div>" +
        "<h3>정류장</h3>" + (stored ? stopTable(stored.stops) : recordNotice(result)) + "<h3>작업 기록</h3>" +
        (stored ? historyTable(["시각","이벤트","누가","문장"], stored.events.map(e =>
            "<tr>" + cells([stampText(e.ts),e.title,e.by,e.detail.reason]) + "</tr>")) : recordNotice(result)));
};
