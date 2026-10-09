"""Live mode endpoints.

  POST /api/live/replay        upload MP4 + GPX, play back at real-time speed through the detectors
  POST /api/live/device        create a phone-camera session (phone opens /live.html?s=<id>)
  POST /api/live/{id}/stop     stop, re-cluster, save as a normal run
  GET  /api/live               active sessions
  WS   /ws/live/{id}           viewer event stream (dashboard)
  WS   /ws/live/{id}/ingest    phone -> server: binary [float64 capture ms][JPEG], JSON gps fixes
"""
import asyncio
import base64
import hmac
import json
import os
import shutil
import socket
import secrets
import struct
import subprocess
import threading
import time
import traceback
from pathlib import Path

import cv2
import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect

from hazardmap.config import RUNS_DIR
from hazardmap.calibrate import calibrate
from hazardmap.detect import iter_frames
from hazardmap.ingest import LiveTrajectory, Trajectory, load_gps, resolve_video_start, video_info
from hazardmap.live import LiveSession
from hazardmap.pipeline import get_detector

from . import auth, drives
from .workers import executor, make_cfg, new_run_id, num

router = APIRouter()
DEVICE_IDLE_TIMEOUT_S = 600


class Live:
    """Control state for one live session, shared by the worker thread and the event loop."""

    def __init__(self, sid: str, mode: str, name: str, loop: asyncio.AbstractEventLoop, owner: int | None = None):
        self.id, self.mode, self.name, self.loop = sid, mode, name, loop
        self.owner = owner             # platform user id (None: admin/control room)
        # secret in the QR link: lets a second device (phone) stream into this session without
        # signing in; valid only for this session and only while it runs
        self.pair_key = secrets.token_urlsafe(18)
        self.state = "queued"          # queued | running | finalizing | done | error
        self.error: str | None = None
        self.summary: dict | None = None
        self.created = time.time()
        self.session: LiveSession | None = None
        self.route: dict | None = None  # planned route (replay only)
        self.viewers: set[asyncio.Queue] = set()
        self.stop = threading.Event()
        # phone input: newest frame only (drop-to-latest) + incremental GPS
        self.traj: LiveTrajectory | None = None
        self.slot: tuple[float, bytes] | None = None
        self.slot_lock = threading.Lock()
        self.slot_event = threading.Event()
        self.last_input = time.time()

    def emit(self, ev: dict):
        """Thread-safe: hand an event to every connected viewer."""
        self.loop.call_soon_threadsafe(self._fanout, ev)

    def _fanout(self, ev: dict):
        for q in list(self.viewers):
            if q.qsize() > 100 and ev["type"] in ("frame", "pose", "stats"):
                continue               # slow viewer: drop transient events, never hazards
            q.put_nowait(ev)

    def info(self) -> dict:
        return {"id": self.id, "mode": self.mode, "name": self.name, "state": self.state,
                "created": self.created, "error": self.error}


lives: dict[str, Live] = {}


def _get(sid: str) -> Live:
    lv = lives.get(sid)
    if lv is None:
        raise HTTPException(404, "live session not found")
    return lv


def _finish(lv: Live):
    lv.state = "finalizing"
    lv.emit({"type": "status", "state": "finalizing"})
    if lv.session.frames == 0:                 # nothing was streamed: don't litter the run list
        shutil.rmtree(lv.session.out, ignore_errors=True)
        lv.state = "done"
        drives.on_failed(lv.id, "Nothing was streamed")
        lv.emit({"type": "done", "run_id": None, "summary": None})
        return
    lv.summary = lv.session.finalize()
    lv.state = "done"
    drives.on_finished(lv.id, lv.summary)
    lv.emit({"type": "done", "run_id": lv.id, "summary": {k: v for k, v in lv.summary.items() if k != "config"}})


def _guard(fn):
    def wrapped(lv: Live, *a):
        try:
            drives.on_processing(lv.id)
            fn(lv, *a)
        except Exception as e:
            traceback.print_exc()
            lv.state, lv.error = "error", str(e)
            drives.on_failed(lv.id, str(e))
            lv.emit({"type": "error", "message": str(e)})
    return wrapped


@_guard
def _file_worker(lv: Live, video: str, gps: str, cfg, speed: float | None, video_start: float | None = None):
    """Play a recorded drive through the live engine.
    speed=None: process every frame as fast as the GPU allows (Process drive).
    speed=x:    pace to x times real time, dropping frames if behind (Replay).
    video_start: exact epoch of the first frame (in-app recorder), overrides metadata sync."""
    if Path(video).suffix.lower() in (".webm", ".mkv"):
        lv.emit({"type": "status", "state": "converting"})
        video = _to_mp4(video)
    traj = Trajectory(load_gps(gps), cfg.max_gps_gap_s)
    info = video_info(video)
    t0, sync = resolve_video_start(info, traj, cfg.time_offset_s, video_start)
    lv.route = traj.geojson()
    meta = {"video": Path(video).name, "gps": Path(gps).name, "sync_method": sync}
    if speed:
        meta["playback_speed"] = speed
    detector = get_detector(cfg)
    if cfg.auto_calibrate:
        lv.emit({"type": "status", "state": "calibrating"})
        meta["calibration"] = calibrate(video, cfg, detector.world)
    lv.session = LiveSession(cfg, lv.id, lv.mode, detector, traj.pose, traj.geojson, lv.emit, meta, source_video=video)
    lv.state = "running"
    lv.emit({"type": "route", "feature": lv.route})
    lv.emit({"type": "status", "state": "running"})

    duration = info["duration"] or 1.0
    period = 1.0 / cfg.sample_fps
    start = time.monotonic()
    for idx, vt, frame in iter_frames(video, cfg.sample_fps):
        if lv.stop.is_set():
            break
        lv.session.progress = min(vt / duration, 1.0)
        lag = 0.0
        if speed:
            target = start + vt / speed                  # when this frame "happens" on the wall clock
            now = time.monotonic()
            if now < target:
                if lv.stop.wait(target - now):
                    break
            elif now - target > period:
                lv.session.skipped()                     # behind schedule: drop to stay live
                continue
            lag = max(0.0, time.monotonic() - target)
        lv.session.feed(frame, t0 + vt, idx, vt, lag)
    lv.session.progress = 1.0
    _finish(lv)


def _to_mp4(path: str) -> str:
    """Browser recordings (MediaRecorder WebM) lack a duration/seek index: re-encode once."""
    out = str(Path(path).with_suffix(".mp4"))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path, "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "25", "-pix_fmt", "yuv420p", "-an", out], check=True)
    Path(path).unlink(missing_ok=True)
    return out


def start_file_session(vpath: Path, gpath: Path, name: str, cfg, speed: float | None,
                       loop: asyncio.AbstractEventLoop, video_start: float | None = None,
                       owner: int | None = None) -> Live:
    """Start a session over files already saved under runs/<sid>/input."""
    sid = vpath.parent.parent.name
    lv = lives[sid] = Live(sid, "replay" if speed else "batch", name, loop, owner)
    executor.submit(_file_worker, lv, str(vpath), str(gpath), cfg, speed, video_start)
    return lv


@_guard
def _device_worker(lv: Live, cfg):
    lv.session = LiveSession(cfg, lv.id, "device", get_detector(cfg), lv.traj.pose, lv.traj.geojson, lv.emit, {
        "video": "phone camera (live)", "gps": "phone GPS (live)", "sync_method": "device clock"})
    lv.session.passthrough = True              # ingest forwards every phone frame; we add boxes
    lv.state = "running"
    lv.emit({"type": "status", "state": "running"})
    idx = 0
    while not lv.stop.is_set():
        if not lv.slot_event.wait(0.5):
            if time.time() - lv.last_input > DEVICE_IDLE_TIMEOUT_S:
                break
            continue
        with lv.slot_lock:
            item, lv.slot = lv.slot, None
            lv.slot_event.clear()
        if item is None:
            continue
        t_cap, jpeg = item
        frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            continue
        first = lv.session.first_epoch if lv.session.first_epoch is not None else t_cap
        lv.session.feed(frame, t_cap, idx, t_cap - first, max(0.0, time.time() - t_cap))
        idx += 1
    _finish(lv)


# ---------------------------------------------------------------------- HTTP
@router.post("/api/live/replay")
async def start_replay(video: UploadFile = File(...), gps: UploadFile = File(...),
                       name: str = Form(""), speed: float = Form(1.0), sample_fps: float = Form(5.0),
                       time_offset: float = Form(0.0), use_world: bool = Form(True),
                       camera: str = Form("phone"), auto_calibrate: bool = Form(True),
                       cam_height: str = Form(""), cam_pitch: str = Form(""),
                       hfov: str = Form(""), hood: str = Form(""),
                       road_half_width: float = Form(6.0)):
    sid = new_run_id(name or Path(video.filename or "drive").stem, "live-")
    d = RUNS_DIR / sid / "input"
    d.mkdir(parents=True)
    vpath = d / ("video" + Path(video.filename or "v.mp4").suffix.lower())
    gpath = d / ("track" + Path(gps.filename or "t.gpx").suffix.lower())
    for up, dst in ((video, vpath), (gps, gpath)):
        with open(dst, "wb") as f:
            shutil.copyfileobj(up.file, f)
    cfg = make_cfg(sample_fps, time_offset, use_world, camera, auto_calibrate,
                   num(cam_height), num(cam_pitch), num(hfov), num(hood), road_half_width)
    lv = start_file_session(vpath, gpath, name or video.filename or sid, cfg, max(0.25, min(speed, 8.0)),
                            asyncio.get_running_loop())
    return lv.info()


@router.post("/api/live/device")
async def start_device(request: Request, name: str = Form("phone drive"), sample_fps: float = Form(30.0),
                       use_world: bool = Form(True)):
    user = auth.current_user(request)
    lv = create_device_session(name, sample_fps, use_world, asyncio.get_running_loop(),
                               None if user is None or user["role"] == "admin" else user["id"])
    return {**lv.info(), **pair_links(request, lv, sample_fps)}


def pair_links(request: Request, lv: Live, fps: float = 30.0) -> dict:
    """QR / link targets for a phone. Phones only allow camera + GPS on HTTPS, so the links
    always point at the HTTPS server, on every LAN address of this machine."""
    secure = request.url.scheme == "https"
    port = request.url.port if secure and request.url.port else int(os.environ.get("HAZARDMAP_HTTPS_PORT", "8443"))
    q = f"live.html?s={lv.id}&fps={fps:g}&k={lv.pair_key}"
    return {"phone_urls": [f"https://{ip}:{port}/{q}" for ip in _lan_ips()], "secure": secure,
            "this_device": f"/live.html?s={lv.id}&fps={fps:g}"}


def key_ok(lv: Live | None, key: str | None) -> bool:
    return bool(lv and key and lv.state in ("queued", "running") and hmac.compare_digest(key, lv.pair_key))


@router.get("/api/pair/{sid}")
def pair_status(sid: str, k: str = ""):
    """Session state for a paired phone (no login; the key from the QR is the credential)."""
    lv = lives.get(sid)
    if not key_ok(lv, k):
        raise HTTPException(404, "This live link has expired. Create a new session.")
    return lv.info()


def create_device_session(name: str, sample_fps: float, use_world: bool, loop: asyncio.AbstractEventLoop,
                          owner: int | None) -> Live:
    # Sessions share one detector thread: release this owner's phone sessions nobody ever
    # streamed to, otherwise an abandoned link keeps the new session queued behind it.
    for other in lives.values():
        if other.mode == "device" and other.owner == owner and other.state in ("queued", "running") and \
                (other.session is None or other.session.frames == 0):
            other.stop.set()
            other.slot_event.set()
    sid = new_run_id(name, "phone-")
    cfg = make_cfg(sample_fps, 0.0, use_world, "phone", False)
    lv = lives[sid] = Live(sid, "device", name, loop, owner)
    lv.traj = LiveTrajectory(cfg.max_gps_gap_s)
    if owner is not None:
        drives.register(sid, owner, "live", name)
    executor.submit(_device_worker, lv, cfg)
    return lv


@router.post("/api/live/{sid}/stop")
def stop(sid: str):
    lv = _get(sid)
    lv.stop.set()
    lv.slot_event.set()
    return lv.info()


@router.get("/api/live")
def active():
    return [lv.info() for lv in lives.values() if lv.state in ("queued", "running", "finalizing")]


@router.get("/api/live/{sid}")
def status(sid: str):
    lv = _get(sid)
    return {**lv.info(), "stats": lv.session.stats() if lv.session else None}


# ---------------------------------------------------------------------- WebSockets
@router.websocket("/ws/live/{sid}")
async def viewer(ws: WebSocket, sid: str):
    lv = lives.get(sid)
    user = auth.user_from_token(ws.cookies.get(auth.COOKIE))
    await ws.accept()
    if user is None or not (user["role"] == "admin" or (lv is not None and lv.owner == user["id"])):
        await ws.send_json({"type": "error", "message": "not allowed"})
        await ws.close(code=4403)
        return
    if lv is None:
        await ws.send_json({"type": "error", "message": "live session not found"})
        await ws.close()
        return
    q: asyncio.Queue = asyncio.Queue()
    lv.viewers.add(q)                       # subscribe before the snapshot so nothing is missed
    try:
        await ws.send_json({"type": "status", "state": lv.state, **lv.info()})
        if lv.route:
            await ws.send_json({"type": "route", "feature": lv.route})
        if lv.session:
            await ws.send_json(lv.session.snapshot())
        if lv.state == "done":
            await ws.send_json({"type": "done", "run_id": lv.id})
            return
        if lv.state == "error":
            await ws.send_json({"type": "error", "message": lv.error})
            return
        while True:
            ev = await q.get()
            await ws.send_json(ev)
            if ev["type"] in ("done", "error"):
                break
    except WebSocketDisconnect:
        pass
    finally:
        lv.viewers.discard(q)


@router.websocket("/ws/live/{sid}/ingest")
async def ingest(ws: WebSocket, sid: str):
    lv = lives.get(sid)
    user = auth.user_from_token(ws.cookies.get(auth.COOKIE))
    paired = key_ok(lv, ws.query_params.get("k"))
    await ws.accept()
    if not paired and (user is None or (lv is not None and user["role"] != "admin" and lv.owner != user["id"])):
        await ws.send_json({"type": "error", "message": "This live link is not valid any more. Scan a new QR code."})
        await ws.close(code=4401)
        return
    if lv is None or lv.mode != "device":
        await ws.send_json({"type": "error", "message": "no phone session with this id"})
        await ws.close()
        return
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            lv.last_input = time.time()
            if msg.get("bytes") is not None:
                data = msg["bytes"]
                if len(data) <= 8 or lv.state not in ("queued", "running"):
                    continue
                t_cap = struct.unpack("<d", data[:8])[0] / 1000.0
                with lv.slot_lock:
                    if lv.slot is not None and lv.session:
                        lv.session.skipped()        # previous frame never processed: drop it
                    lv.slot = (t_cap, data[8:])
                    lv.slot_event.set()
                if lv.viewers:                        # full-rate preview, independent of inference speed
                    lv._fanout({"type": "frame", "jpeg": base64.b64encode(data[8:]).decode()})
                st = lv.session.stats() if lv.session else {}
                boxes = lv.session.last_boxes if lv.session else None
                await ws.send_json({"type": "ack", "state": lv.state, "hazards": st.get("hazards", 0),
                                    "fps": st.get("fps", 0), "latency_s": st.get("latency_s", 0),
                                    "gps_fixes": len(lv.traj.fixes), "gps_rejected": lv.traj.rejected,
                                    "boxes": boxes if boxes and time.time() - boxes["t"] < 1.0 else None})
            elif msg.get("text"):
                m = json.loads(msg["text"])
                if m.get("type") == "gps" and lv.traj is not None:
                    lv.traj.add_fix(float(m["t"]) / 1000.0, float(m["lat"]), float(m["lon"]), m.get("acc"))
                elif m.get("type") == "stop":
                    lv.stop.set()
                    lv.slot_event.set()
                    await ws.send_json({"type": "stopping"})
    except WebSocketDisconnect:
        pass


def _lan_ips() -> list[str]:
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))     # no packets are sent; picks the outbound interface
        ips.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return ips or ["localhost"]
