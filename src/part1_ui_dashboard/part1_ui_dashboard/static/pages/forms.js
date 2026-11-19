const formNames = {patient:"환자 이송", supply:"약품 운송", call:"로봇 호출"};
const formTypes = {patient:"PATIENT_TRANSPORT", supply:"MED_ROUND", call:"CALL"};
const stopLimit = {patient:2, supply:6, call:1};
let supplyJob = "";
const drafts = new WeakMap();

function formShell(kinds) {
    return `<form class="job-form" data-kinds="${kinds.join(" ")}"></form>`;
}

function freshDraft(kind) {
    return {kind, stops:[], level:1, items:{}, chart_no:"", name:"", item:""};
}

function renderForm(form) {
    const kinds = form.dataset.kinds.split(" ");
    const d = drafts.get(form) || freshDraft(kinds[0]);
    drafts.set(form,d);
    const {kind, stops, level} = d;
    const patient = kind === "patient", med = kind === "supply";
    const role = k => robotName(k === "patient" ? "robot_a" : "robot_b");
    const zone = z => {
        const i = stops.indexOf(z);
        const badge = med ? i + 1 : patient ? (i === 0 ? "출발" : "도착") : "목적지";
        return `<button type="button" data-act="pick-zone" data-id="${z}" aria-pressed="${i >= 0}">${i >= 0 ? '<b>' + badge + '</b>' : ''}${h(zn(z))}</button>`;
    };
    const field = (name, label, placeholder, value, required=false) =>
        `<label>${label}<input name="${name}" placeholder="${placeholder}" value="${h(value)}" ${required ? "required" : ""}></label>`;
    const tabBar = kinds.map(k => `<button type="button" data-act="form-tab" data-id="${k}" aria-pressed="${k === kind}"><strong>${formNames[k]}</strong><small>${h(role(k))}</small></button>`).join("");
    const names = stops.map(zn);
    const summary = patient ? (stops.length === 2 ? role(kind) + "이 " + names[0] + "에서 환자를 태워 " + names[1] + "으로 갑니다." : stops.length ? "도착 구역을 누르세요." : "환자를 태울 구역부터 누르세요.") :
        med ? (stops.length ? role(kind) + "이 " + names.join(" → ") + " 순서로 갑니다. 정류장마다 화면에서 확인을 받습니다." : "들를 구역을 순서대로 누르세요.") :
        (stops.length ? role(kind) + "이 " + names[0] + "으로 갑니다." : "로봇을 부를 구역을 누르세요.");
    form.innerHTML = `<div class="form-tabs">${tabBar}</div><div class="pick"><div class="pick-head"><h3>${patient ? "출발과 도착" : med ? "들를 구역" : "부를 곳"}</h3>
        <small>${patient ? "처음 누른 곳이 출발, 다음에 누른 곳이 도착입니다." : med ? "누른 순서대로 들릅니다. 다시 누르면 빠집니다. 6곳까지 고를 수 있습니다." : "한 곳만 고릅니다."}</small></div>
        <div class="zone-picker"><div>${[..."DEF"].map(zone).join("")}</div><span>복도</span><div>${[..."ABC"].map(zone).join("")}</div></div></div>
        ${patient ? '<div class="field-row">' + field("chart_no","환자 ID · 필수","차트번호",d.chart_no,true) + field("name","이름 · 선택","환자 이름",d.name) + '<label>손목 태그<input disabled placeholder="QR 연동 후 사용"></label></div>' :
          med ? '<section><h3>정류장별 약품</h3>' + (stops.map((z,i) => `<div class="med-stop"><b>${i+1}</b><strong>${h(zn(z))}</strong>
            <input data-med="${z}" aria-label="${h(zn(z))} 약품" placeholder="약품 이름" value="${h(d.items[z])}" required>${btn("빼기","pick-zone",z)}</div>`).join("") || empty("아직 고른 구역이 없습니다. 위에서 구역을 누르세요.")) + '</section>' :
          field("item","가져올 것 · 선택","품목 입력",d.item)}
        <section><h3>응급도</h3><div class="levels">${["비응급","보통","응급"].map((v,i) => btn(v + "　Level " + (i+1),"form-level",i+1,level === i+1 ? (level === 3 ? "urgent" : "selected") : "")).join("")}</div></section>
        <footer class="form-footer"><p>${h(summary)}</p>${btn("초기화","reset-form")}<button class="primary submit" type="submit">요청 등록</button></footer><div class="form-result" role="status"></div>`;
    updateReady(form);
}
function updateReady(form) {
    const d = drafts.get(form);
    const ready = d.kind === "patient" ? d.stops.length === 2 && d.chart_no.trim() :
        d.kind === "call" ? d.stops.length === 1 : d.stops.length && d.stops.every(z => d.items[z]?.trim());
    form.querySelector(".submit").disabled = !ready;
}
function editForm(button, change) {
    const f = button.closest("form");
    change(drafts.get(f));
    renderForm(f);
}
actions["pick-zone"] = (z,b) => editForm(b, d => {
    if (d.stops.includes(z)) d.stops = d.stops.filter(s => s !== z);
    else if (d.stops.length < stopLimit[d.kind]) d.stops.push(z);
    else d.stops[d.stops.length-1] = z;
});
actions["form-tab"] = (kind,b) => editForm(b, d => { d.kind = kind; d.stops = d.stops.slice(0,stopLimit[kind]); });
actions["form-level"] = (n,b) => editForm(b, d => { d.level = Number(n); });
actions["reset-form"] = (_,b) => editForm(b, d => Object.assign(d,freshDraft(d.kind)));
document.addEventListener("input", e => {
    const f = e.target.closest(".job-form");
    if (!f) return;
    const d = drafts.get(f);
    if (e.target.dataset.med) d.items[e.target.dataset.med] = e.target.value;
    else if (e.target.name) d[e.target.name] = e.target.value;
    updateReady(f);
});
function quick() {
    openModal("quick", dialogHead("이송 요청") + formShell(["patient","supply","call"]));
    renderForm($("dialog").querySelector("form"));
}
document.addEventListener("submit", async e => {
    const f = e.target;
    if (!f.matches(".job-form")) return;
    e.preventDefault();
    const d = drafts.get(f), out = f.querySelector(".form-result");
    const data = {type:formTypes[d.kind], level:d.level};
    if (d.kind === "supply") data.stops = d.stops.map(zone => ({zone, item:d.items[zone].trim()}));
    else if (d.kind === "patient") Object.assign(data,{pickup:d.stops[0], destination:d.stops[1], chart_no:d.chart_no.trim(), name:d.name.trim()});
    else Object.assign(data,{destination:d.stops[0],item:d.item.trim()});
    const ack = await send("mission",data,f.querySelector(".submit"));
    if (ack?.ok) { out.className = "form-result success"; out.dataset.mid = ack.mission_id; formResults(); }
    else if (ack) { out.className = "form-result error"; out.textContent = ack.message; }
});
function formResults() {
    for (const out of document.querySelectorAll(".form-result[data-mid]")) {
        const id = out.dataset.mid, r = mById(id)?.robot;
        const text = h(id) + " 접수 · " + h(r ? robotName(r) + " 배정" : "배정 대기") + " " + btn("작업 보기", "view-job", id, "linkbtn");
        if (out.innerHTML !== text)
            out.innerHTML = text;
    }
}
actions["view-job"] = id => { closeModal(); inspect(id); };
initializers.patient = () => { $("page").innerHTML = '<section class="panel form-page">' + cardTitle("신규 환자 등록") + formShell(["patient"]) + '</section><section class="panel history-patients">' + cardTitle("최근 환자 이송") + '<div id="recent-patients"></div></section>'; renderForm($('page').querySelector('form')); };
renderers.patient = () => {
    formResults();
    const result = readHistory("patients","patients/recent");
    put("recent-patients", result.data ? historyTable(["접수 시각","환자 ID","이름","경로","응급도","상태"],
        result.data.items.map(m => `<tr data-act="patient-history" data-id="${h(m.id)}" data-status="${h(m.status)}">` +
            cells([stampText(m.created_at),m.chart_no,m.name || "—",routeText(m),"Level " + m.level,tabs[m.status]]) + "</tr>")) : recordNotice(result));
};
actions["patient-history"] = (id,row) => { selected = id; missionFilter = {tab:row.dataset.status}; navigate("missions"); };
initializers.supply = () => {
    $("page").innerHTML = '<div class="split"><section class="panel">' + formShell(["supply","call"]) + '</section><aside class="panel" id="supply-progress"></aside></div>';
    renderForm($("page").querySelector("form"));
};
renderers.supply = () => {
    formResults();
    const jobs = (S?.missions || []).filter(m => m.type === "MED_ROUND" && m.status === "ACTIVE");
    const m = jobs.find(m => m.mission_id === supplyJob) || jobs[0];
    const stage = !m ? 0 : m.stop_index === 0 ? (S.robots[m.robot]?.state === "WAIT_CONFIRM" ? 2 : 1) : (S.robots[m.robot]?.state === "WAIT_CONFIRM" ? 4 : 3);
    put("supply-progress", cardTitle("약품 수령 · 확인 상태") + (m ? `<select aria-label="진행 중인 물품 임무" id="supply-job">${jobs.map(j => `<option value="${j.mission_id}" ${j === m ? "selected" : ""}>${h(j.mission_id + " · " + missionTarget(j))}</option>`).join("")}</select><ol class="steps">${["요청·응급도 확인", "로봇 수령지 이동·도착", "물품 수령 확인", "전달지 이동", "인계 확인"].map((v, i) => `<li class="${stage > i ? "done" : stage === i ? "current" : ""}">${i < stage ? "✓ " : ""}${v}</li>`).join("")}</ol>${btn("작업 상세", "inspect", m.mission_id)}` : empty("진행 중인 약품 운송 작업이 없습니다.")));
    if (m) {
        const result = readHistory("supply","missions/" + encodeURIComponent(m.mission_id));
        const panel = $("supply-progress");
        let stops = $("supply-stops");
        if (!stops) { stops = document.createElement("div"); stops.id = "supply-stops"; panel.append(stops); }
        put("supply-stops", result.data ? stopTable(result.data.stops) : recordNotice(result));
    }
    if ($("supply-job"))
        $("supply-job").onchange = e => { supplyJob = e.target.value; render(); };
};
