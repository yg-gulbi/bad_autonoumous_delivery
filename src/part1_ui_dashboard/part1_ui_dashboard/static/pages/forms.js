const formNames = { patient: "환자 이송", supply: "물품 전달", call: "로봇 호출" };
const zoneField = (title, key, initial) => `<label>${title}<select name="${key}">${zoneOptions(initial)}</select></label>`;
const inputField = (name, title, placeholder, required = false) => `<label>${title}<input name="${name}" placeholder="${placeholder}" ${required ? "required" : ""}></label>`;
let supplyTab = "supply", supplyJob = "";

function jobForm(kind) {
    const patient = kind === "patient", call = kind === "call";
    return `<form class="job-form" data-kind="${kind}"><div class="row"><span class="eyebrow">${patient ? "환자 정보" : call ? "호출 정보" : "요청 정보"}</span>${pill(patient ? "환자 이송" : call ? "단일 목적지" : "수령 · 인계", patient ? "blue" : "purple")}</div>
 ${patient ? `<div class="field-row">${inputField("chart_no", "환자 ID (차트번호)", "차트번호 입력", true)}${inputField("name", "환자 이름 (선택)", "이름 입력")}</div>${inputField("tag_id", "손목 태그 ID (선택)", "태그 ID 입력")}<small>태그 확인: QR 연동 후</small>` : call ? inputField("item", "품목 (선택)", "요청할 품목 입력") : `<div class="field-row">${inputField("item", "물품 이름", "물품 이름 입력", true)}${inputField("department", "요청처 (선택)", "요청 부서 입력")}</div>`}
 <h3>이송 경로</h3><div class="field-row">${call ? "" : zoneField(patient ? "픽업 구역" : "수령지 구역", "pickup", "A")}${zoneField("목적지 구역", "destination", call ? "A" : "B")}</div>${kind === "supply" ? '<div class="extra"></div>' + btn("＋ 정류장 추가", "extra") : ""}
 <h3>응급도</h3><div class="levels">${["비응급", "보통", "응급"].map((v, i) => `<label><input type="radio" name="level" value="${i + 1}" ${i === 0 ? "checked" : ""}><span>Level ${i + 1} · ${v}</span></label>`).join("")}</div>
 <div class="form-result" role="status"></div><button class="primary submit" type="submit">${patient ? "환자 등록" : call ? "호출 등록" : "운송 요청 등록"}</button></form>`;
}

function quick(kind) {
    openModal("quick", dialogHead("빠른 이송 요청") + '<div class="tabs">' + Object.keys(formNames).map(k => btn(formNames[k], "quick-kind", k, k === kind ? "selected" : "")).join("") + "</div>" + jobForm(kind));
}
actions["quick-kind"] = quick;
actions.extra = (_, b) => {
    const list = b.closest("form").querySelector(".extra");
    if (list.children.length >= 4)
        return;
    const line = document.createElement("div");
    line.className = "field-row";
    line.innerHTML = zoneField("추가 정류장", "extra", "C") + btn("삭제", "remove-stop");
    list.append(line);
    b.disabled = list.children.length >= 4;
};
actions["remove-stop"] = (_, b) => { const f = b.closest("form"); b.parentElement.remove(); f.querySelector('[data-act="extra"]').disabled = false; };
document.addEventListener("submit", async (e) => {
    const f = e.target;
    if (!f.matches(".job-form"))
        return;
    e.preventDefault();
    const data = Object.fromEntries(new FormData(f)), ids = { patient: "PATIENT_TRANSPORT", supply: "MED_ROUND", call: "CALL" };
    data.type = ids[f.dataset.kind];
    data.extra = new FormData(f).getAll("extra");
    const out = f.querySelector(".form-result"), ack = await send("mission", data, f.querySelector(".submit"));
    if (!ack)
        return;
    if (!ack.ok) {
        out.className = "form-result error";
        out.textContent = ack.message;
        return;
    }
    out.className = "form-result success";
    out.dataset.mid = ack.mission_id;
    formResults();
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
initializers.patient = () => { $("page").innerHTML = '<section class="panel form-page">' + cardTitle("신규 환자 등록") + jobForm("patient") + "</section>"; };
renderers.patient = formResults;
initializers.supply = () => { $("page").innerHTML = '<div class="split"><section class="panel"><div class="tabs">' + ["supply", "call"].map(k => btn(formNames[k], "supply-tab", k, k === supplyTab ? "selected" : "")).join("") + '</div>' + jobForm(supplyTab) + '</section><aside class="panel" id="supply-progress"></aside></div>'; };
actions["supply-tab"] = k => { supplyTab = k; initializers.supply(); render(); };
renderers.supply = () => {
    formResults();
    const jobs = (S?.missions || []).filter(m => m.type === "MED_ROUND" && m.status === "ACTIVE");
    const m = jobs.find(m => m.mission_id === supplyJob) || jobs[0];
    const stage = !m ? 0 : m.stop_index === 0 ? (S.robots[m.robot]?.state === "WAIT_CONFIRM" ? 2 : 1) : (S.robots[m.robot]?.state === "WAIT_CONFIRM" ? 4 : 3);
    put("supply-progress", cardTitle("의료물품 수령 · 확인 상태") + (m ? `<select aria-label="진행 중인 물품 임무" id="supply-job">${jobs.map(j => `<option value="${j.mission_id}" ${j === m ? "selected" : ""}>${h(j.mission_id + " · " + missionTarget(j))}</option>`).join("")}</select><ol class="steps">${["요청·응급도 확인", "로봇 수령지 이동·도착", "물품 수령 확인", "전달지 이동", "인계 확인"].map((v, i) => `<li class="${stage > i ? "done" : stage === i ? "current" : ""}">${i < stage ? "✓ " : ""}${v}</li>`).join("")}</ol>${btn("작업 상세", "inspect", m.mission_id)}` : empty("진행 중인 물품 전달 작업이 없습니다.")));
    if ($("supply-job"))
        $("supply-job").onchange = e => { supplyJob = e.target.value; render(); };
};
