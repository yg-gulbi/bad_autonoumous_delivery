// Cache only the visible page's queries. Fleet state updates never force a DB read.
const historySlots = new Map();
function stampText(ts) {
    if (ts == null) return "—";
    const d = new Date(ts * 1000), pad = v => String(v).padStart(2,"0");
    return [d.getFullYear(),pad(d.getMonth()+1),pad(d.getDate())].join("-") + " " +
        [d.getHours(),d.getMinutes(),d.getSeconds()].map(pad).join(":");
}
function historyURL(path, filters = {}) {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters))
        if (value != null && value !== "") params.set(key, value);
    return "/api/" + path + (params.size ? "?" + params : "");
}
function readHistory(slot, path, filters) {
    const url = historyURL(path, filters);
    let entry = historySlots.get(slot);
    if (entry?.url !== url) {
        entry = {url, stale:true};
        historySlots.set(slot, entry);
    }
    if (entry.stale && !entry.loading) {
        entry.stale = false;
        entry.loading = true;
        fetch(url).then(async response => {
            const body = await response.json();
            if (!response.ok) throw Error(body.message || "기록을 읽지 못했습니다.");
            entry.data = body;
            entry.error = "";
        }).catch(err => {
            entry.error = err.message;
            entry.data = null;
        }).finally(() => {
            entry.loading = false;
            if (historySlots.get(slot) === entry) render();
        });
    }
    return entry;
}
function historyChanged(event) {
    const lifecycle = ["MISSION_CREATED","MISSION_ASSIGNED","LEG_START","ARRIVE","CONFIRM_STEP",
                       "MISSION_DONE","MISSION_CANCELED","CARD_CLOSED"].includes(event.type);
    for (const [slot, entry] of historySlots) {
        if (slot === "events" || (slot === "kpi" && ["PAUSE","RESOURCE_HOLD","DEADLOCK_DETECTED","CARD_CLOSED","RESOURCE_RELEASE"].includes(event.type))
            || (slot === "cards" && ["CARD_OPEN","CARD_CLOSED"].includes(event.type))
            || (slot === "detail" && (event.mission_id === selected || lifecycle))
            || (lifecycle && ["missions","patients","supply"].includes(slot)))
            entry.stale = true;
    }
}
const recordNotice = result => empty(result.error || "기록을 읽는 중입니다.");
function historyTable(columns, rows, message = "조건에 맞는 기록이 없습니다.") {
    return rows.length ? '<div class="tablewrap"><table><thead><tr>' +
        columns.map(c => "<th>" + h(c) + "</th>").join("") + "</tr></thead><tbody>" + rows.join("") + "</tbody></table></div>" : empty(message);
}
const cells = fields => fields.map(v => "<td>" + h(v ?? "—") + "</td>").join("");
function pager(result, action) {
    return result.page === 1 && !result.has_more ? "" : '<div class="buttons history-pages">' +
        btn("이전", action, result.page - 1, "", result.page === 1) + "<span>" + result.page + "쪽</span>" +
        btn("다음", action, result.page + 1, "", !result.has_more) + "</div>";
}
const stopNames = {PENDING:"예정", ACTIVE:"이동 중", ARRIVED:"도착", CONFIRMED:"확인 완료", MISMATCH:"불일치", SKIPPED:"건너뜀"};
function stopTable(stops) {
    return historyTable(["순번","구역","품목","상태","도착 시각","확인 시각","확인한 사람"],
        stops.map(s => "<tr>" + cells([s.seq, zn(s.zone), s.item, stopNames[s.status] || s.status || "미기록",
                                      stampText(s.arrived_at), stampText(s.confirmed_at), s.confirmed_by]) + "</tr>"));
}
