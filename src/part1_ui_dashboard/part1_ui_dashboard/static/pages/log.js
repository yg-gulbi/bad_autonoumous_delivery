const decisions = new Set(["RESOURCE_GRANT", "RESOURCE_HOLD", "CORRIDOR_YIELD_START"]);

function filteredEvents() { return E.filter(e => (!logFilter.robot || e.robot === logFilter.robot) && (!logFilter.type || e.type === logFilter.type) && (!logFilter.group || decisions.has(e.type)) && (!logFilter.mid || e.mission_id?.includes(logFilter.mid)) && (!logFilter.q || [e.title, e.type, e.detail.reason, e.mission_id, robotName(e.robot)].join(" ").toLowerCase().includes(logFilter.q.toLowerCase()))); }
initializers.log = () => {
    $("page").innerHTML = '<div id="kpis"></div><section class="panel">' + cardTitle("운영 기록", btn("CSV 저장", "csv")) + `<div class="filters" id="log-filters"><select name="robot"><option value="">모든 로봇</option><option value="robot_a">Robot A</option><option value="robot_b">Robot B</option></select><select name="type" id="event-types"></select><select name="group"><option value="">전체 기록</option><option value="decision">통행 판단</option></select><input name="mid" placeholder="임무 ID"><input name="q" placeholder="기록 검색"></div><div id="logs-table"></div></section>`;
    for (const input of $("log-filters").children)
        input.value = logFilter[input.name] || "";
    $("log-filters").oninput = e => { logFilter[e.target.name] = e.target.value; render(); };
};
renderers.log = () => {
    const k = S?.kpi || {};
    put("kpis", Object.entries({ "대기 횟수": k.hold_count, "교착 횟수": k.deadlock_count, "교착 평균 해소 시간": k.deadlock_resolve_s_avg == null ? "해소 기록 없음" : k.deadlock_resolve_s_avg.toFixed(1) + "초", "정지 횟수": k.pause_count }).map(([t, v]) => `<div class="metric"><label>${t}</label><strong>${h(v ?? "미수신")}</strong></div>`).join(""));
    put("event-types", '<option value="">모든 종류</option>' + [...new Set(E.map(e => e.type))].sort().map(k => `<option ${k === logFilter.type ? "selected" : ""} value="${h(k)}">${h(k)}</option>`).join(""));
    const records = filteredEvents();
    put("logs-table", records.length ? `<table><thead><tr><th>시각</th><th>종류</th><th>로봇</th><th>임무</th><th>판단 및 기록</th></tr></thead><tbody>${records.slice().reverse().map(e => `<tr><td>${tm(e.ts)}</td><td>${h(e.title)}<small>${h(e.type)}</small></td><td>${h(robotName(e.robot))}</td><td>${h(e.mission_id || "관제 기록")}</td><td>${h(e.detail.reason)} ${decisions.has(e.type) ? btn("판단 근거", "event-basis", E.indexOf(e), "linkbtn") : ""}</td></tr>`).join("")}</tbody></table>` : empty("조건에 맞는 기록이 없습니다."));
};
actions["event-basis"] = i => basis(E[Number(i)]);
actions.csv = () => { const rows = [["시각", "종류", "로봇", "임무", "문장"], ...filteredEvents().map(e => [tm(e.ts), e.type, e.robot, e.mission_id, e.detail.reason])]; const escape = v => '"' + String(v ?? "").replace(/^[=+@-]/, "'$&").replaceAll('"', '""') + '"'; const url = URL.createObjectURL(new Blob(["\ufeff" + rows.map(r => r.map(escape).join(",")).join("\r\n")], { type: "text/csv;charset=utf-8" })); const a = document.createElement("a"); a.href = url; a.download = "관제-운영기록.csv"; a.click(); setTimeout(() => URL.revokeObjectURL(url), 2000); };
