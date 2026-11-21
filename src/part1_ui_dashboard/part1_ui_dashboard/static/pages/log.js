const decisions = new Set(["ZONE_OCCUPIED_HOLD","PROXIMITY_STOP","PLAN_UPDATE","PLAN_WAIT","PLAN_YIELD_MOVE"]);
let logTab = "events", eventPage = 1, cardPage = 1;
function logQuery() {
    const {robot,type,mid,q,group,from,to} = logFilter;
    return {robot,type,mission:mid,q,group,from,to};
}
initializers.log = () => {
    $("page").innerHTML = '<div class="filters" id="log-period"><label>시작일<input type="date" name="from"></label><label>종료일<input type="date" name="to"></label></div><div id="kpis"></div><section class="panel">' +
        cardTitle("운영 기록",btn("CSV 저장","csv")) + '<div id="log-tabs"></div><div class="filters" id="log-filters"><select aria-label="로봇" name="robot"><option value="">모든 로봇</option>' + Object.entries(cfg.robot_names).map(([id,label]) => `<option value="${h(id)}">${h(label)}</option>`).join("") + '</select><select aria-label="이벤트 종류" name="type" id="event-types"></select><select aria-label="기록 구분" name="group"><option value="">전체 기록</option><option value="decision">통행 판단</option></select><input name="mid" placeholder="임무 ID"><input name="q" placeholder="기록 검색"></div><div id="logs-table"></div></section>';
    logTab = "events"; eventPage = cardPage = 1;
    $("event-types").innerHTML = '<option value="">모든 종류</option>' + [...new Set([...(cfg.event_types || []), ...E.map(e => e.type)])].sort().map(t => `<option value="${h(t)}">${h(cfg.event_labels[t] || "관제 기록")}</option>`).join("");
    document.querySelectorAll("#log-period input,#log-filters [name]").forEach(el => { el.value = logFilter[el.name] || ""; });
    $("log-period").onchange = $("log-filters").oninput = e => {
        logFilter[e.target.name] = e.target.value; eventPage = cardPage = 1; render();
    };
};
renderers.log = () => {
    const period = {from:logFilter.from, to:logFilter.to};
    const kpi = readHistory("kpi","kpi",period);
    if (kpi.data) {
        const k = kpi.data;
        put("kpis", Object.entries({"순서 대기 횟수":k.hold_count, "정지 횟수":k.pause_count}).map(([t,v]) => `<div class="metric"><label>${h(t)}</label><strong>${h(v)}</strong></div>`).join(""));
    } else put("kpis", recordNotice(kpi));
    put("log-tabs", '<div class="tabs">' + btn("이벤트","logtab","events",logTab === "events" ? "selected" : "") +
        btn("결정 카드","logtab","cards",logTab === "cards" ? "selected" : "") + "</div>");
    $("log-filters").hidden = logTab !== "events";
    document.querySelector('[data-act="csv"]').hidden = logTab !== "events";
    if (logTab === "cards") {
        const result = readHistory("cards","cards",{...period,page:cardPage});
        const rows = result.data?.items.map(c => "<tr>" + cells([cardNames[c.kind] || c.title,
            stampText(c.opened_at),stampText(c.closed_at),c.choice_label || c.choice,c.by,
            ({selected:"운영자 선택",timeout:"시간 만료",resolved:"상황 해소"})[c.close_reason] || "열림"]) + "</tr>");
        put("logs-table", rows ? historyTable(["종류","열린 시각","닫힌 시각","고른 것","누가","닫힌 이유"],rows) + pager(result.data,"cardpage") : recordNotice(result));
        return;
    }
    const result = readHistory("events","events",{...logQuery(),page:eventPage});
    const rows = result.data?.items.map(e => `<tr><td>${stampText(e.ts)}</td><td>${h(e.title)}</td>` +
        cells([robotName(e.robot),e.mission_id,e.by]) + "<td>" + h(e.detail.reason + repeatText(e)) + " " +
        (decisions.has(e.type) ? btn("판단 근거","event-basis",e.id,"linkbtn") : "") + "</td></tr>");
    put("logs-table", rows ? historyTable(["시각","종류","로봇","임무","누가","문장"],rows) + pager(result.data,"eventpage") : recordNotice(result));
};
actions.logtab = t => { logTab = t; render(); };
actions.cardpage = p => { cardPage = Number(p); render(); };
actions.eventpage = p => { eventPage = Number(p); render(); };
actions["event-basis"] = id => basis(historySlots.get("events")?.data?.items.find(e => e.id === Number(id)));
actions.csv = async () => {
    try {
        const response = await fetch(historyURL("events.csv",logQuery()));
        if (!response.ok) throw Error((await response.json()).message);
        const url = URL.createObjectURL(await response.blob());
        const a = document.createElement("a"); a.href = url; a.download = "관제-운영기록.csv"; a.click();
        setTimeout(() => URL.revokeObjectURL(url), 2000);
    } catch (error) { toast(error.message, false); }
};
