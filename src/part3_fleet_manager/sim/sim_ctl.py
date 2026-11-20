#!/usr/bin/env python3
"""Control & observation helper for PART 03 Gazebo simulation via PART 01 HTTP/SSE API."""
import argparse
import base64
import json
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


BASE = "http://127.0.0.1:8090"


def post(path: str, body: dict):
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def snapshot(timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(f"{BASE}/events", timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace")
            if line.startswith("data: "):
                return json.loads(line[6:])
    raise RuntimeError("No SSE snapshot received")


def fmt_robot(r: dict) -> str:
    p = r.get("pose")
    pstr = f"({p['x']:+.2f},{p['y']:+.2f})" if p else "(none)"
    return (
        f"{r.get('state'):12s} part4={str(r.get('part4_state') or '-'):14s} "
        f"dock={r.get('docked')} z={r.get('zone') or '-'} s={r.get('slot') or '-'} "
        f"reg={r.get('region') or '-'} {pstr}"
    )


def print_status(snap: dict | None = None):
    snap = snap or snapshot()
    st = snap["state"]
    cor = st.get("resources", {}).get("CORRIDOR", {})
    print(f"bed={st['bed_exit']['monitor']['state']}({st['bed_exit']['monitor']['rate_hz']}Hz) "
          f"exit={st['bed_exit']['value']} cards={len(st['open_cards'])} "
          f"corridor={cor.get('state')}:{cor.get('holders')}")
    for rid in ("robot_a", "robot_b"):
        r = st["robots"][rid]
        print(f"  {rid}: {fmt_robot(r)}")
    if snap.get("cards"):
        for c in snap["cards"]:
            print(f"  CARD {c['card_id']} [{c['kind']}] robots={c['robots']} reason={c.get('reason')}")


def confirm_waiting(st: dict, operator: str = "sim"):
    confirmed = []
    for rid, r in st["robots"].items():
        if r.get("state") == "WAIT_CONFIRM":
            mid = r.get("mission_id")
            if mid:
                code, resp = post("/api/command", {
                    "cmd": "CONFIRM_STEP", "robot": rid, "mission_id": mid,
                    "result": "OK", "operator": operator,
                })
                confirmed.append((rid, mid, code))
    return confirmed


def wait_idle(timeout_s: float = 240.0, auto_confirm: bool = True, poll_s: float = 2.0,
              require_undocked: bool = False):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        snap = snapshot()
        st = snap["state"]
        ra, rb = st["robots"]["robot_a"], st["robots"]["robot_b"]
        dt = time.monotonic() - t0
        print(f"[{dt:5.1f}s] A:{ra['state']:11s}({ra.get('zone') or '-'}->{ra.get('destination') or '-'}) "
              f"B:{rb['state']:11s}({rb.get('zone') or '-'}->{rb.get('destination') or '-'}) "
              f"cards={len(st['open_cards'])}", flush=True)
        if auto_confirm:
            for item in confirm_waiting(st):
                print(f"  -> auto CONFIRM_STEP: {item}", flush=True)
        if (ra["state"] == "IDLE" and rb["state"] == "IDLE"
                and not st["open_cards"]
                and (not require_undocked or (not ra.get("docked") and not rb.get("docked")))):
            return True, st
        time.sleep(poll_s)
    return False, snapshot()["state"]


def capture_screenshot(out_path: Path, width: int = 1440, height: int = 900, wait_s: float = 2.5):
    """Capture http://127.0.0.1:8090/ using headless google-chrome --screenshot."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sim_chrome_", ignore_cleanup_errors=True) as tmp:
        # Use virtual-time-budget / small JS delay via DevTools or --screenshot directly.
        # Note: SSE stream stays open so --screenshot needs a tiny DevTools websocket capture
        # OR --run-all-compositor-stages-before-draw with timeout. Let's use Chrome CDP!
        port = 9229
        proc = subprocess.Popen(
            [
                "google-chrome", "--headless=new", "--disable-gpu", "--no-sandbox",
                f"--user-data-dir={tmp}", f"--remote-debugging-port={port}",
                f"--window-size={width},{height}", "--hide-scrollbars",
                f"{BASE}/",
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            ws_url = None
            for _ in range(30):
                time.sleep(0.2)
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as r:
                        tabs = json.loads(r.read().decode())
                    for tab in tabs:
                        if tab.get("type") == "page" and tab.get("webSocketDebuggerUrl"):
                            ws_url = tab["webSocketDebuggerUrl"]
                            break
                    if ws_url:
                        break
                except Exception:
                    pass
            if not ws_url:
                raise RuntimeError("Chrome DevTools tab not found")
            time.sleep(wait_s)
            png_bytes = _cdp_screenshot(ws_url, width, height)
            out_path.write_bytes(png_bytes)
            print(f"[screenshot] saved {out_path} ({len(png_bytes)} bytes)")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()


def _cdp_screenshot(ws_url: str, width: int, height: int) -> bytes:
    """Minimal RFC 6455 WebSocket client for Chrome DevTools Page.captureScreenshot."""
    import hashlib
    import os
    import socket
    import struct
    from urllib.parse import urlparse

    u = urlparse(ws_url)
    sock = socket.create_connection((u.hostname, u.port), timeout=10)
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        f"GET {u.path} HTTP/1.1\r\n"
        f"Host: {u.hostname}:{u.port}\r\n"
        f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(req.encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += sock.recv(4096)
    _, _, extra = buf.partition(b"\r\n\r\n")

    def send_json(obj):
        payload = json.dumps(obj).encode()
        mask = os.urandom(4)
        header = bytearray([0x81])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", n))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", n))
        header.extend(mask)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        sock.sendall(bytes(header) + masked)

    rx = bytearray(extra)

    def recv_exact(k):
        while len(rx) < k:
            chunk = sock.recv(65536)
            if not chunk:
                raise EOFError("ws closed")
            rx.extend(chunk)
        out = bytes(rx[:k])
        del rx[:k]
        return out

    def recv_msg():
        while True:
            b1, b2 = recv_exact(2)
            opcode = b1 & 0x0F
            ln = b2 & 0x7F
            if ln == 126:
                ln = struct.unpack("!H", recv_exact(2))[0]
            elif ln == 127:
                ln = struct.unpack("!Q", recv_exact(8))[0]
            data = recv_exact(ln)
            if opcode == 1:
                return json.loads(data.decode())
            if opcode == 9:  # ping -> ignore
                continue

    try:
        send_json({"id": 1, "method": "Emulation.setDeviceMetricsOverride",
                   "params": {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False}})
        while True:
            m = recv_msg()
            if m.get("id") == 1:
                break
        time.sleep(0.4)
        send_json({"id": 2, "method": "Page.captureScreenshot", "params": {"format": "png"}})
        while True:
            m = recv_msg()
            if m.get("id") == 2:
                return base64.b64decode(m["result"]["data"])
    finally:
        sock.close()


def main():
    p = argparse.ArgumentParser(description="PART 03 simulation control helper")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    g = sub.add_parser("go")
    g.add_argument("robot", choices=["robot_a", "robot_b"])
    g.add_argument("zone", choices=list("ABCDEF"))
    w = sub.add_parser("wait")
    w.add_argument("--timeout", type=float, default=240.0)
    w.add_argument("--no-confirm", action="store_true")
    w.add_argument("--undocked", action="store_true")
    s = sub.add_parser("screenshot")
    s.add_argument("out")
    args = p.parse_args()

    if args.cmd == "status":
        print_status()
    elif args.cmd == "go":
        print(post("/api/command", {"cmd": "GO_ZONE", "robot": args.robot, "zone": args.zone, "operator": "sim"}))
    elif args.cmd == "wait":
        ok, _ = wait_idle(args.timeout, auto_confirm=not args.no_confirm, require_undocked=args.undocked)
        raise SystemExit(0 if ok else 1)
    elif args.cmd == "screenshot":
        capture_screenshot(Path(args.out))


if __name__ == "__main__":
    main()
