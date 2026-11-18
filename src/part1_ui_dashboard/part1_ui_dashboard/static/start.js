async function start() {
    cfg = await (await fetch("/api/map")).json();
    await loadMap();
    const demo = cfg.mode === "demo" || (cfg.mode === "auto" && cfg.demo);
    document.querySelectorAll(".mode").forEach(n => n.textContent = demo ? "시연 모드" : "실기 모드");
    $("gear").hidden = !cfg.dev_tools;
    window.addEventListener("hashchange", loadPage);
    loadPage();
    const source = new EventSource("/events"), on = (name, f) => source.addEventListener(name, e => f(JSON.parse(e.data)));
    on("snapshot", s => {
        historySlots.clear();
        S = s.state;
        received = performance.now() - (s.age ?? 0) * 1000;
        E = s.events;
        cards = new Map(s.cards.map(c => [c.card_id, c]));
        for (const a of s.acks)
            remember(a);
        render();
        nextNotice();
    });
    on("state", s => { S = s; received = performance.now(); render(); });
    on("card", c => { if (!cards.has(c.card_id)) {
        cards.set(c.card_id, c);
        if (!modal || modal === "tools" || modal === "quick")
            showCard(c.card_id);
    } renderTop(); });
    on("ack", remember);
    on("event", e => {
        historyChanged(e);
        E.push(e);
        if (E.length > cfg.event_limit)
            E.shift();
        if (e.type === "CARD_CLOSED") {
            const id = e.detail.card_id;
            cards.delete(id);
            if (modalCard === id)
                closeModal();
        }
        if (e.type === "BED_MONITOR_WARN")
            toast(e.detail.reason, false);
        if (alarmKinds[e.type]) {
            alerts.push(e);
            beep();
            if (modal !== "alarm")
                alarm();
        }
        render();
        nextNotice();
    });
    setInterval(renderTop, 250);
}
start().catch(e => toast("화면을 불러오지 못했습니다. " + e.message, false));
