let meta, raster, mapObserver, hits = [], zoom = 1, pan = [0, 0], overlay = false;
const transform = (m, x, y) => [m[0]*x + m[2]*y + m[4], m[1]*x + m[3]*y + m[5]];
const unitColor = id => id === "robot_a" ? "#2563EB" : "#7C4DDB";

async function loadMap() {
    meta = await (await fetch("/map/meta")).json();
    if (!meta.available) return;
    // The server emits a canonical P5 header followed by one trinary byte per cell.
    const bytes = new Uint8Array(await (await fetch("/map/image")).arrayBuffer());
    const pixels = bytes.subarray(bytes.length - meta.width * meta.height);
    raster = document.createElement("canvas");
    raster.width = meta.width;
    raster.height = meta.height;
    const ctx = raster.getContext("2d");
    const image = ctx.createImageData(meta.width, meta.height);
    for (let i = 0; i < pixels.length; i++) {
        const rgb = pixels[i] === 0 ? [64, 77, 96, 255] : pixels[i] === 254 ? [247, 249, 252, 255] : [0, 0, 0, 0];
        image.data.set(rgb, i * 4);
    }
    ctx.putImageData(image, 0, 0);
}

function resetMap() {
    zoom = 1;
    pan = [0, 0];
    paint();
}

function zoomAt(factor, x, y) {
    const canvas = $("map");
    const next = Math.max(.8, Math.min(4, zoom * factor));
    const ratio = next / zoom;
    pan = [x, y].map((point, i) => {
        const center = (i ? canvas.clientHeight : canvas.clientWidth) / 2;
        return (point - center) * (1 - ratio) + pan[i] * ratio;
    });
    zoom = next;
    paint();
}

actions.zoom = factor => zoomAt(Number(factor), $("map").clientWidth / 2, $("map").clientHeight / 2);
actions["map-go"] = async (value, button) => {
    const [robot, zone] = value.split(":");
    if ((await command("GO_ZONE", {robot, zone}, button))?.ok) closeModal();
};

function selectMapPoint(x, y) {
    const hit = hits.findLast(p => Math.abs(x-p.x) <= p.w && Math.abs(y-p.y) <= p.h);
    if (hit?.r) {
        const card = $("unit-" + hit.r);
        card?.classList.add("highlight");
        setTimeout(() => card?.classList.remove("highlight"), 1600);
    } else if (hit?.z) {
        const buttons = Object.entries(S?.robots || {}).map(([id, r]) =>
            btn(r.label, "map-go", id + ":" + hit.z, "", r.state !== "IDLE"));
        openModal("zone", dialogHead(zn(hit.z)) + "<p>이 구역으로 보내기</p>" + buttons.join(" "));
    }
}

function installMap() {
    const canvas = $("map"), toggle = $("overlay");
    toggle.disabled = !meta.available;
    toggle.checked = overlay && meta.available;
    toggle.onchange = () => { overlay = toggle.checked; paint(); };
    mapObserver?.disconnect();
    mapObserver = new ResizeObserver(resetMap);
    mapObserver.observe(canvas);
    let drag, clickTimer;
    canvas.onpointerdown = e => {
        canvas.setPointerCapture(e.pointerId);
        drag = {x:e.clientX, y:e.clientY, pan:[...pan]};
    };
    canvas.onpointermove = e => {
        if (!drag) return;
        pan = [drag.pan[0]+e.clientX-drag.x, drag.pan[1]+e.clientY-drag.y];
        paint();
    };
    canvas.onpointerup = e => {
        if (!drag) return;
        const moved = Math.hypot(e.clientX-drag.x, e.clientY-drag.y);
        drag = null;
        if (moved > 4) return;
        clearTimeout(clickTimer);
        clickTimer = setTimeout(() => selectMapPoint(e.offsetX, e.offsetY), 250);
    };
    canvas.onpointercancel = () => { drag = null; };
    canvas.ondblclick = () => { clearTimeout(clickTimer); resetMap(); };
    canvas.addEventListener("wheel", e => {
        e.preventDefault();
        zoomAt(e.deltaY < 0 ? 1.2 : 1/1.2, e.offsetX, e.offsetY);
    }, {passive:false});
    resetMap();
}

// Clip a screen-space segment against a label box expanded by the stroke radius.
function crossesBox([start, end, radius], box) {
    let low = 0, high = 1;
    for (const [axis, min, max] of [[0, box.l-radius, box.r+radius], [1, box.t-radius, box.b+radius]]) {
        const delta = end[axis] - start[axis];
        if (Math.abs(delta) < 1e-8) {
            if (start[axis] < min || start[axis] > max) return false;
        } else {
            const a = (min-start[axis])/delta, b = (max-start[axis])/delta;
            low = Math.max(low, Math.min(a,b));
            high = Math.min(high, Math.max(a,b));
            if (low > high) return false;
        }
    }
    return true;
}

function paint() {
    const canvas = $("map");
    if (!canvas || !meta) return;
    const width = canvas.clientWidth, height = canvas.clientHeight, dpr = window.devicePixelRatio || 1;
    if (!width || !height) return;
    if (canvas.width !== Math.round(width*dpr) || canvas.height !== Math.round(height*dpr)) {
        canvas.width = Math.round(width*dpr);
        canvas.height = Math.round(height*dpr);
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    hits = [];
    const plan = meta.floor_plan;
    const world = (x, y) => transform(meta.matrix, x, y);
    const zones = Object.entries(cfg.zones);
    const extent = plan ? plan.walls.slice(0,4).flatMap(([x,y,u,v]) => [world(x,y), world(u,v)]) :
        raster ? [[0,0], [meta.width,meta.height]] : zones.map(([,p]) => world(p.x,p.y));
    const xs = extent.map(p => p[0]), ys = extent.map(p => p[1]);
    const left = Math.min(...xs), right = Math.max(...xs), top = Math.min(...ys), bottom = Math.max(...ys);
    const margin = plan || raster ? 16 : 50;
    const scale = zoom * Math.min((width-2*margin)/(right-left || 1), (height-2*margin)/(bottom-top || 1));
    const tx = width/2 + pan[0] - scale*(left+right)/2, ty = height/2 + pan[1] - scale*(top+bottom)/2;
    function screen(x, y) {
        const [u,v] = world(x,y);
        return [tx+u*scale, ty+v*scale];
    }
    function path(points) {
        ctx.beginPath();
        points.forEach(([x,y], i) => i ? ctx.lineTo(x,y) : ctx.moveTo(x,y));
    }
    function wall(segment, color, weight) {
        const [x,y,u,v] = segment;
        path([screen(x,y), screen(u,v)]);
        ctx.lineWidth = weight;
        ctx.strokeStyle = color;
        ctx.stroke();
    }
    if (plan) {
        const [a,b,c,d] = plan.walls[0], [e,f,g,k] = plan.walls[1];
        path([screen(a,b), screen(c,d), screen(g,k), screen(e,f)]);
        ctx.closePath();
        ctx.fillStyle = "#F7F9FC";
        ctx.fill();
    }
    if (raster && (overlay || !plan)) {
        ctx.save();
        ctx.globalAlpha = plan ? .35 : 1;
        ctx.imageSmoothingEnabled = false;
        ctx.drawImage(raster, tx, ty, raster.width*scale, raster.height*scale);
        ctx.restore();
    }
    ctx.lineCap = "butt";
    if (plan) {
        for (const segment of plan.walls) wall(segment, "#9EACC1", 6);
        for (const segment of Object.values(plan.doors)) wall(segment, "#BEC8D6", 1);
    }
    if (overlay && raster) {
        ctx.save();
        path(cfg.polygon.map(p => screen(...p)));
        ctx.closePath();
        ctx.setLineDash([5,4]);
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = "#675A90";
        ctx.stroke();
        ctx.restore();
    }
    const routes = [];
    const units = Object.entries(S?.robots || {});
    for (const [id, r] of units) {
        if (!r.route?.length) continue;
        const points = r.route.map(p => screen(...p));
        const end = points.at(-1), prev = points.findLast(p => Math.hypot(end[0]-p[0], end[1]-p[1]) > .01);
        if (!prev) continue;
        points.slice(1).forEach((p,i) => routes.push([points[i], p, 1.5]));
        path(points);
        ctx.lineJoin = "round";
        ctx.lineWidth = 3;
        ctx.strokeStyle = ctx.fillStyle = unitColor(id);
        ctx.stroke();
        const angle = Math.atan2(end[1]-prev[1], end[0]-prev[0]);
        ctx.save();
        ctx.translate(...end);
        ctx.rotate(angle);
        path([[0,0],[-11,-5],[-11,5]]);
        ctx.fill();
        ctx.restore();
    }
    function dot(x,y,radius,color) {
        ctx.beginPath();
        ctx.arc(x,y,radius,0,Math.PI*2);
        ctx.fillStyle = color;
        ctx.fill();
    }
    function tag(x,y,text,background,ink,border) {
        ctx.font = '600 12px "Noto Sans CJK KR",sans-serif';
        const half = ctx.measureText(text).width/2+8;
        ctx.beginPath();
        ctx.roundRect(x-half,y-12,2*half,24,6);
        ctx.fillStyle = background;
        ctx.fill();
        if (border) {
            ctx.strokeStyle = border;
            ctx.lineWidth = 1.3;
            ctx.stroke();
        }
        ctx.fillStyle = ink;
        ctx.fillText(text,x,y);
        return half;
    }
    const labels = [], robots = [];
    for (const [z, pos] of zones) {
        const [x,y] = screen(pos.x,pos.y);
        dot(x,y,4,"#9EACC1");
        const room = S?.resources["ROOM_"+z];
        const dest = units.find(([,r]) => r.leg?.to === z);
        labels.push({x,y,z,
            bg:room?.blocked || room?.state === "UNKNOWN" ? "#FDECEE" : "#FFFFFF",
            border:dest ? unitColor(dest[0]) : "#BFCEE1"});
        hits.push({x,y,w:6,h:6,z});
    }
    for (const [id,r] of units) {
        if (!r.pose) continue;
        const [x,y] = screen(r.pose.x,r.pose.y);
        dot(x,y,14,unitColor(id));
        ctx.strokeStyle = "#FFFFFF";
        ctx.lineWidth = 2;
        ctx.stroke();
        robots.push({id,r,x,y});
    }
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    const center = cfg.polygon.reduce(([x,y],[u,v]) => [x+u/cfg.polygon.length,y+v/cfg.polygon.length],[0,0]);
    const [cx,cy] = screen(...center), corridor = S?.resources.CORRIDOR;
    let caption = plan?.labels?.corridor || "복도";
    if (corridor?.state === "UNKNOWN") caption += " · 점유 불명";
    else if (corridor?.blocked) caption += " · 막힘 " + duration(S.stamp-(corridor.blocked_since ?? S.stamp));
    const walls = (plan?.walls || []).map(([x,y,u,v]) => [screen(x,y),screen(u,v),3]);
    const placed = [];
    const tagFont = '600 12px "Noto Sans CJK KR",sans-serif';
    const directions = (x,y,dy,side) => [[x,y+dy],[x,y-dy],[x+side,y],[x-side,y]];
    function place(candidates, half, height, captionOnly=false) {
        for (const [x,y] of candidates) {
            const box = {l:x-half-1, r:x+half+1, t:y-height-1, b:y+height+1};
            if (box.l < 0 || box.r > width || box.t < 0 || box.b > canvas.clientHeight) continue;
            if (placed.some(p => box.l <= p.r && p.l <= box.r && box.t <= p.b && p.t <= box.b)) continue;
            if (robots.some(r => Math.hypot(Math.max(box.l-r.x, 0, r.x-box.r),
                                             Math.max(box.t-r.y, 0, r.y-box.b)) <= 15)) continue;
            if (walls.some(w => crossesBox(w,box)) || (captionOnly && routes.some(r => crossesBox(r,box)))) continue;
            placed.push(box);
            return {x,y};
        }
        if (captionOnly) return null;
        const [x,y] = candidates[0];
        placed.push({l:x-half-1,r:x+half+1,t:y-height-1,b:y+height+1});
        return {x,y, fallback:true};
    }
    for (const {id,r,x,y} of robots) {
        ctx.font = "bold 14px sans-serif";
        ctx.fillStyle = "white";
        ctx.fillText(id === "robot_a" ? "A" : "B",x,y);
        const status = r.region === "CORRIDOR" && r.state === "MOVING" ? "통과 중" : (cfg.states[r.state] || r.state);
        const upper = r.zone ? "DEF".includes(r.zone) : y <= cy;
        ctx.font = tagFont;
        const half = ctx.measureText(status).width/2 + 8;
        const slot = place(directions(x,y,upper ? 30 : -30,half+18),half,12);
        tag(slot.x,slot.y,status,slot.fallback ? "#FFFFFFBF" : "#FFFFFF",unitColor(id));
        hits.push({x,y,w:14,h:14,r:id});
    }
    for (const {x,y,z,bg,border} of labels) {
        ctx.font = tagFont;
        const half = ctx.measureText(zn(z)).width/2 + 8;
        const slot = place(directions(x,y,"DEF".includes(z) ? -26 : 26,half+8),half,12);
        tag(slot.x,slot.y,zn(z),slot.fallback ? "#FFFFFFBF" : bg,"#263651",border);
        hits.push({x:slot.x,y:slot.y,w:half,h:12,z});
    }
    const ends = [[0,1],[2,3]].map(([a,b]) => screen(
        (cfg.polygon[a][0]+cfg.polygon[b][0])/2, (cfg.polygon[a][1]+cfg.polygon[b][1])/2
    )).sort((a,b) => a[0]-b[0]);
    const candidates = [[cx,cy], ...ends.map(([x,y]) => [(cx+x)/2,(cy+y)/2])];
    ctx.font = '600 14px "Noto Sans CJK KR",sans-serif';
    const slot = place(candidates,ctx.measureText(caption).width/2,8,true);
    if (slot) {
        ctx.fillStyle = corridor?.blocked || corridor?.state === "UNKNOWN" ? "#C62839" : "#6B7689";
        ctx.fillText(caption,slot.x,slot.y);
    }
}
