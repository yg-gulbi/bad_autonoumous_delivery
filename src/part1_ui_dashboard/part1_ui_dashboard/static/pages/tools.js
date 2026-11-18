let runningScenario = 0;

function toolsPanel() {
    if (!cfg.dev_tools)
        return;
    openModal("tools", `<span class="eyebrow">시연 모드</span>${dialogHead("시험 도구")}<h3>시나리오 시작</h3><div class="scenario-list">${["두 로봇 동시 임무", "복도 양보", "교착", "복도 막힘", "CALL 세 건 연속"].map((v, i) => btn((i + 1) + ". " + v, "scenario", i + 1, "", !!runningScenario)).join("")}</div><h3>가짜 침상 감시</h3><div class="buttons">${[["off", "끄기"], ["normal", "정상 송신"], ["exit", "이탈 송신"]].map(([mode, text]) => btn(text, "bed-mode", mode, mode === S?.fake_bed_mode ? "selected" : "")).join("")}</div><h3>mock 로봇 제어</h3>${["robot_a", "robot_b"].map(id => `<div class="tool-unit"><b>${robotName(id)}</b><div class="buttons"><label>정지 시간(초)<input type="number" min="1" value="40" id="secs-${id}"></label>${btn("장애물 놓기", "stall", id)}${btn("다음 주행 실패", "fail", id)}${btn("연결 끊기 10초", "drop", id)}</div><div class="buttons"><label>배터리(%)<input type="number" min="0" max="100" value="${S?.robots[id]?.battery_pct ?? 80}" id="pct-${id}"></label>${btn("배터리 적용", "battery", id)}</div></div>`).join("")}`);
}
const testInput = (kind, data, b) => send("test/" + kind, data, b);
Object.assign(actions, { tools: toolsPanel, "bed-mode": async (mode, b) => {
        const reply = await testInput("bed", { mode }, b);
        if (reply?.mode && modal === "tools")
            document.querySelectorAll('[data-act="bed-mode"]').forEach(n => n.classList.toggle("selected", n.dataset.id === reply.mode));
    },
    stall: (robot, b) => testInput("stall", { robot, seconds: Number($("secs-" + robot).value) }, b), fail: (robot, b) => testInput("fail_next", { robot }, b),
    drop: (robot, b) => testInput("drop_link", { robot, seconds: 10 }, b), battery: (robot, b) => testInput("battery", { robot, value: Number($("pct-" + robot).value) }, b) });

async function until(check, description) {
    const end = performance.now() + 120000;
    while (!check()) {
        if (performance.now() > end)
            throw Error(description + " 시간 초과");
        await new Promise(r => setTimeout(r, 100));
    }
}
actions.scenario = async (id) => {
    if (!cfg.dev_tools || runningScenario)
        return;
    if (!S || Object.values(S.robots).some(r => r.state !== "IDLE")) {
        toast("두 로봇이 대기 상태일 때 시작하세요.", false);
        return;
    }
    const req = async (path, data) => { const ack = await send(path, data); if (!ack || ack.ok === false)
        throw Error(ack?.message || "시험 요청 실패"); return ack; };
    const patient = (pickup, level = 2) => req("mission", { type: "PATIENT_TRANSPORT", pickup, destination: "A", level, name: "모의환자", chart_no: "DEMO" });
    const med = zones => req("mission", { type: "MED_ROUND", level: 3, stops: [...zones].map(zone => ({ zone, item: "모의물품" })) });
    const go = (robot, zone) => req("command", { cmd: "GO_ZONE", robot, zone });
    runningScenario = Number(id);
    closeModal();
    navigate("dashboard");
    try {
        if (id !== "3") {
            const away = Object.entries(cfg.start_zones).filter(([robot, home]) => S.robots[robot].zone !== home);
            for (const [robot, home] of away)
                await go(robot, home);
            if (away.length)
                await until(() => away.every(([robot, home]) => S.robots[robot].state === "IDLE" && S.robots[robot].zone === home), "홈 배치");
        }
        if (id === "1") {
            await patient("C");
            await med("FEB");
            await patient("D");
            await med("F");
        }
        if (id === "2") {
            await patient("C", 1);
            await med("A");
        }
        if (id === "3") {
            await go("robot_a", "B");
            await go("robot_b", "E");
            await until(() => Object.values(S.robots).every(r => r.state === "IDLE") && S.robots.robot_a.zone === "B" && S.robots.robot_b.zone === "E", "병실 배치");
            await patient("E");
            await med("B");
        }
        if (id === "4") {
            await go("robot_a", "C");
            await until(() => S.robots.robot_a.region === "CORRIDOR", "복도 진입");
            await req("test/stall", { robot: "robot_a", seconds: 40 });
            await until(() => S.resources.CORRIDOR.blocked, "막힘 확인");
            await go("robot_b", "A");
        }
        if (id === "5") {
            for (const destination of ["A", "B", "E"])
                await req("mission", { type: "CALL", destination, item: "거즈", level: 1 });
        }
    }
    catch (e) {
        toast(e.message, false);
    }
    finally {
        runningScenario = 0;
    }
};
