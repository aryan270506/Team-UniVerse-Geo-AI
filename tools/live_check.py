"""Headless end-to-end check of live mode (no browser, no phone needed).

    python tools/live_check.py replay --video data/sample/road_bad_25s.mp4 --gps data/sample/road_bad.gpx --speed 2
    python tools/live_check.py device --video data/sample/road_bad_25s.mp4 --gps data/sample/road_bad.gpx --seconds 15

`device` pretends to be the phone: it streams frames (JPEG) and GPS fixes over the ingest
WebSocket at real time, re-stamped to "now", while a second socket listens as a viewer.
"""
import argparse
import asyncio
import json
import struct
import time
from collections import Counter

import cv2
import httpx
import websockets

from hazardmap.ingest import load_gps


async def watch(base_ws: str, sid: str, counts: Counter, out: dict, verbose: bool):
    async with websockets.connect(f"{base_ws}/ws/live/{sid}", max_size=None) as ws:
        async for raw in ws:
            ev = json.loads(raw)
            counts[ev["type"]] += 1
            if ev["type"] == "hazard" and verbose:
                p = ev["feature"]["properties"]
                print(f"  hazard {'NEW ' if ev['new'] else 'upd '}{p['id']} {p['category']} {p['severity']} conf={p['confidence']}")
            elif ev["type"] == "stats":
                out["stats"] = ev
            elif ev["type"] in ("done", "error"):
                out["end"] = ev
                return


async def replay(a):
    base = a.server.rstrip("/")
    with open(a.video, "rb") as v, open(a.gps, "rb") as g:
        r = httpx.post(f"{base}/api/live/replay", files={"video": v, "gps": g},
                       data={"name": "check-replay", "speed": str(a.speed)}, timeout=120)
    r.raise_for_status()
    sid = r.json()["id"]
    print("session", sid)
    counts, out = Counter(), {}
    t = time.time()
    await watch(base.replace("http", "ws", 1), sid, counts, out, True)
    report(counts, out, time.time() - t)


async def device(a):
    base = a.server.rstrip("/")
    r = httpx.post(f"{base}/api/live/device", data={"name": "check-device"}, timeout=30)
    r.raise_for_status()
    info = r.json()
    sid = info["id"]
    print("session", sid, info.get("phone_urls"))
    ws_base = base.replace("http", "ws", 1)
    counts, out = Counter(), {}
    viewer = asyncio.create_task(watch(ws_base, sid, counts, out, True))

    gps = load_gps(a.gps)
    g0 = gps.t.iloc[0]
    cap = cv2.VideoCapture(a.video)
    native = cap.get(cv2.CAP_PROP_FPS) or 30
    step = max(1, round(native / a.fps))
    acks = []
    async with websockets.connect(f"{ws_base}/ws/live/{sid}/ingest", max_size=None) as ws:
        start = time.time()
        gi, idx, sent = 0, 0, 0
        while time.time() - start < a.seconds:
            vt = time.time() - start
            while gi < len(gps) and gps.t.iloc[gi] - g0 <= vt:      # GPS fixes due by now
                row = gps.iloc[gi]
                await ws.send(json.dumps({"type": "gps", "t": (start + row.t - g0) * 1000,
                                          "lat": row.lat, "lon": row.lon, "acc": 5}))
                gi += 1
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(vt * native))
            ok, frame = cap.read()
            if not ok:
                break
            frame = cv2.resize(frame, (960, int(frame.shape[0] * 960 / frame.shape[1])))
            jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])[1].tobytes()
            await ws.send(struct.pack("<d", time.time() * 1000) + jpeg)
            acks.append(json.loads(await ws.recv()))
            sent += 1
            await asyncio.sleep(max(0, (sent / a.fps) - (time.time() - start)))
        await ws.send(json.dumps({"type": "stop"}))
        await ws.recv()
    print(f"sent {sent} frames, last ack {acks[-1] if acks else None}")
    await asyncio.wait_for(viewer, 120)
    report(counts, out, a.seconds)


def report(counts, out, secs):
    print("events:", dict(counts))
    print("last stats:", {k: v for k, v in out.get("stats", {}).items() if k != "type"})
    end = out.get("end", {})
    if end.get("type") == "done":
        s = end["summary"]
        print(f"DONE run={end['run_id']} hazards={s['hazards']} {s['by_category']} frames={s['frames_processed']} "
              f"dropped={s.get('frames_dropped')} in {secs:.1f}s")
    else:
        print("FAILED:", end)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["replay", "device"])
    ap.add_argument("--server", default="http://localhost:8000")
    ap.add_argument("--video", required=True)
    ap.add_argument("--gps", required=True)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--fps", type=float, default=5.0)
    ap.add_argument("--seconds", type=float, default=15.0)
    a = ap.parse_args()
    asyncio.run(replay(a) if a.mode == "replay" else device(a))


if __name__ == "__main__":
    main()
