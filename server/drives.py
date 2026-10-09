"""Contributed drives: uploads, in-app recordings and live sessions owned by platform users.

User API (/api/me/*): submit drives, list own drives with status/coins/hazards, coins + leaderboard.
Admin API (/api/admin/*): users, their drives and ledgers, coin adjustments, disabling accounts.
The analysis itself is the existing live engine (server/live.py); it reports back through
on_processing / on_finished / on_failed, which also settle the drive's reward coins.
"""
import asyncio
import csv
import hashlib
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from hazardmap.config import RUNS_DIR

from . import auth, db, rewards, weather

router = APIRouter()
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
GPS_EXT = {".gpx", ".csv"}
MAX_BYTES = 2 * 1024 ** 3
MAX_UPLOADS_PER_DAY = 20
MAX_GPS_ACCURACY_M = 50.0


# ---------------------------------------------------------------------- engine hooks
def register(run_id: str, user_id: int, source: str, name: str, sha: str | None = None,
             est_seconds: float | None = None):
    with db.tx() as c:
        c.execute("INSERT OR IGNORE INTO drives (run_id, user_id, source, name, status, video_sha256, duration_s, created_at) "
                  "VALUES (?, ?, ?, ?, 'queued', ?, ?, ?)", (run_id, user_id, source, name, sha, est_seconds, db.now()))
        rewards.add_estimate(c, user_id, run_id, est_seconds)


def owner_of(run_id: str) -> int | None:
    r = db.one("SELECT user_id FROM drives WHERE run_id = ?", (run_id,))
    return r["user_id"] if r else None


def contributors() -> dict:
    """run_id -> {name, source} for every contributed drive (admin tables)."""
    rows = db.all_("SELECT d.run_id, d.source, u.name, u.id FROM drives d JOIN users u ON u.id = d.user_id")
    return {r["run_id"]: {"user": r["name"], "user_id": r["id"], "source": r["source"]} for r in rows}


def on_processing(run_id: str):
    with db.tx() as c:
        c.execute("UPDATE drives SET status = 'processing' WHERE run_id = ? AND status = 'queued'", (run_id,))


def on_failed(run_id: str, error: str):
    with db.tx() as c:
        if not c.execute("SELECT 1 FROM drives WHERE run_id = ?", (run_id,)).fetchone():
            return
        c.execute("UPDATE drives SET status = 'failed', error = ?, finished_at = ? WHERE run_id = ?",
                  (error[:300], db.now(), run_id))
        c.execute("UPDATE coins SET status = 'void' WHERE run_id = ? AND status = 'pending'", (run_id,))


def on_finished(run_id: str, summary: dict):
    with db.tx() as c:
        d = c.execute("SELECT * FROM drives WHERE run_id = ?", (run_id,)).fetchone()
        if d is None:
            return                                             # admin / legacy drive: no rewards
        dup = bool(d["video_sha256"]) and c.execute(
            "SELECT 1 FROM drives WHERE video_sha256 = ? AND run_id != ? AND created_at <= ? AND status != 'failed'",
            (d["video_sha256"], run_id, d["created_at"])).fetchone() is not None
        hazards = int(summary.get("hazards") or 0)
        res = rewards.settle(c, run_id, d["user_id"], summary, hazards, dup)
        c.execute("UPDATE drives SET status = ?, duration_s = ?, valid_s = ?, hazards = ?, route_km = ?, note = ?, "
                  "finished_at = ? WHERE run_id = ?",
                  ("rejected" if dup else "done", summary.get("duration_s"), res["valid_s"], hazards,
                   summary.get("route_km"), res["note"], db.now(), run_id))


# ---------------------------------------------------------------------- uploads
async def _save(up: UploadFile, dst: Path, allowed: set, hash_it: bool = False) -> str | None:
    ext = Path(up.filename or "").suffix.lower()
    if ext not in allowed:
        raise HTTPException(400, f"Unsupported file type {ext or '(none)'}; use {', '.join(sorted(allowed))}")
    h = hashlib.sha256() if hash_it else None
    size = 0
    with open(dst.with_suffix(ext), "wb") as f:
        while chunk := await up.read(1 << 20):
            size += len(chunk)
            if size > MAX_BYTES:
                raise HTTPException(413, "File too large (max 2 GB)")
            if h:
                h.update(chunk)
            f.write(chunk)
    if size == 0:
        raise HTTPException(400, "Empty file")
    return h.hexdigest() if h else None


def _write_fixes(raw: str, dst: Path) -> int:
    try:
        fixes = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "Bad GPS data")
    good = [f for f in fixes if isinstance(f, dict) and all(k in f for k in ("t", "lat", "lon"))
            and (f.get("acc") is None or float(f["acc"]) <= MAX_GPS_ACCURACY_M)]
    with open(dst, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["time", "lat", "lon", "accuracy"])
        for f in sorted(good, key=lambda f: f["t"]):
            w.writerow([f"{float(f['t']) / 1000:.3f}", f"{float(f['lat']):.7f}", f"{float(f['lon']):.7f}", f.get("acc", "")])
    return len(good)


@router.post("/api/me/drives")
async def submit_drive(request: Request, video: UploadFile = File(...), gps: UploadFile | None = File(None),
                       gps_fixes: str = Form(""), name: str = Form(""), source: str = Form("upload"),
                       camera: str = Form("phone"), video_start: float | None = Form(None),
                       duration_s: float | None = Form(None)):
    from .live import start_file_session                        # late import: live imports this module
    from .workers import make_cfg, new_run_id
    user = auth.require_user(request)
    if source not in ("upload", "record"):
        raise HTTPException(400, "bad source")
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    n = db.one("SELECT COUNT(*) AS n FROM drives WHERE user_id = ? AND created_at >= ?", (user["id"], since))["n"]
    if n >= MAX_UPLOADS_PER_DAY and user["role"] != "admin":
        raise HTTPException(429, f"Upload limit reached ({MAX_UPLOADS_PER_DAY} drives per day)")
    title = (name.strip() or ("Recorded drive" if source == "record" else Path(video.filename or "drive").stem))[:40]
    run_id = new_run_id(title, "rec-" if source == "record" else "up-")
    d = RUNS_DIR / run_id / "input"
    d.mkdir(parents=True)
    try:
        sha = await _save(video, d / "video", VIDEO_EXT, hash_it=True)
        vpath = next(d.glob("video.*"))
        if source == "record":
            if _write_fixes(gps_fixes, d / "track.csv") < 2:
                raise HTTPException(400, "No GPS was recorded. Allow location access and record outdoors.")
            gpath = d / "track.csv"
        else:
            if gps is None:
                raise HTTPException(400, "Add the GPS track (GPX or CSV) recorded with the video")
            await _save(gps, d / "track", GPS_EXT)
            gpath = next(d.glob("track.*"))
        est = duration_s
        if est is None and vpath.suffix.lower() in (".mp4", ".mov", ".m4v"):
            from hazardmap.ingest import video_info
            try:
                est = video_info(str(vpath))["duration"]
            except Exception:
                est = None
    except BaseException:
        shutil.rmtree(RUNS_DIR / run_id, ignore_errors=True)
        raise
    owner = None if user["role"] == "admin" else user["id"]
    if owner is not None:
        register(run_id, owner, source, title, sha, est)
    cfg = make_cfg(5.0, 0.0, True, camera, True)
    start_file_session(vpath, gpath, title, cfg, None, asyncio.get_running_loop(),
                       video_start / 1000 if video_start else None, owner)
    return {"id": run_id, "estimate": rewards.estimate(est)}


@router.post("/api/me/live")
async def start_live(request: Request):
    from .live import create_device_session, pair_links
    user = auth.require_user(request)
    lv = create_device_session("phone drive", 30.0, True, asyncio.get_running_loop(),
                               None if user["role"] == "admin" else user["id"])
    links = pair_links(request, lv)
    return {"id": lv.id, "url": links["this_device"], **links}


@router.post("/api/me/live/{sid}/stop")
def stop_live(sid: str, request: Request):
    """Stop & save the user's own phone session (from the watching screen)."""
    from .live import lives
    user = auth.require_user(request)
    lv = lives.get(sid)
    if lv is None or (user["role"] != "admin" and lv.owner != user["id"]):
        raise HTTPException(404, "live session not found")
    lv.stop.set()
    lv.slot_event.set()
    return lv.info()


@router.get("/api/me/live/{sid}")
def live_status(sid: str, request: Request):
    """State of a phone session (owner or admin), with the QR links to pair a phone."""
    from .live import lives, pair_links
    user = auth.require_user(request)
    lv = lives.get(sid)
    if lv is None or (user["role"] != "admin" and lv.owner != user["id"]):
        raise HTTPException(404, "live session not found")
    return {**lv.info(), **pair_links(request, lv)}


# ---------------------------------------------------------------------- user views
def _queue_position(run_id: str) -> int | None:
    from .live import lives
    q = sorted((lv for lv in lives.values() if lv.state == "queued"), key=lambda lv: lv.created)
    ids = [lv.id for lv in q]
    return ids.index(run_id) + 1 if run_id in ids else None


def _drive_dict(r) -> dict:
    coins = db.one("SELECT COALESCE(SUM(CASE WHEN status = 'credited' THEN amount END), 0) AS credited, "
                   "COALESCE(SUM(CASE WHEN status = 'pending' THEN amount END), 0) AS pending FROM coins WHERE run_id = ?",
                   (r["run_id"],))
    out = {k: r[k] for k in ("run_id", "source", "name", "status", "duration_s", "valid_s", "hazards", "route_km",
                             "note", "error", "created_at", "finished_at")}
    out.update(coins=coins["credited"], coins_pending=coins["pending"],
               queue=_queue_position(r["run_id"]) if r["status"] == "queued" else None)
    return out


def _own(request: Request, run_id: str):
    user = auth.require_user(request)
    r = db.one("SELECT * FROM drives WHERE run_id = ?", (run_id,))
    if r is None or (r["user_id"] != user["id"] and user["role"] != "admin"):
        raise HTTPException(404, "drive not found")
    return r


@router.get("/api/me/drives")
def my_drives(request: Request):
    user = auth.require_user(request)
    rows = db.all_("SELECT * FROM drives WHERE user_id = ? ORDER BY created_at DESC", (user["id"],))
    return {"drives": [_drive_dict(r) for r in rows]}


@router.get("/api/me/drives/{run_id}")
def my_drive(run_id: str, request: Request):
    r = _own(request, run_id)
    out = _drive_dict(r)
    d = RUNS_DIR / run_id
    hz = d / "hazards.geojson"
    feats = json.loads(hz.read_text())["features"] if hz.exists() else []
    wx = weather.cached(d) if hz.exists() else {}
    out["hazards_list"] = [{
        "id": f["properties"]["id"], "category": f["properties"]["category"], "severity": f["properties"]["severity"],
        "confidence": f["properties"]["confidence"], "centroid": f["properties"]["centroid"],
        "first_seen": f["properties"]["first_seen"], "geometry": f["geometry"],
        "snapshot": f"/runs/{run_id}/{f['properties']['snapshot']}" if f["properties"].get("snapshot") else None,
        "weather": weather.compact(wx.get(f["properties"]["id"])),
    } for f in feats]
    tj = d / "trajectory.geojson"
    out["route"] = (json.loads(tj.read_text())["features"] or [{}])[0].get("geometry", {}).get("coordinates", []) if tj.exists() else []
    out["ledger"] = [x for x in rewards.ledger(r["user_id"], 500) if x["run_id"] == run_id]
    return out


@router.get("/api/me/summary")
def my_summary(request: Request):
    user = auth.require_user(request)
    st = db.one("SELECT COUNT(*) AS drives, COALESCE(SUM(valid_s), 0) AS seconds, COALESCE(SUM(hazards), 0) AS hazards, "
                "COALESCE(SUM(route_km), 0) AS km FROM drives WHERE user_id = ? AND status IN ('done', 'rejected')", (user["id"],))
    return {"user": user, **rewards.balance(user["id"]), "drives": st["drives"], "minutes": round(st["seconds"] / 60, 1),
            "hazards": st["hazards"], "km": round(st["km"], 1), "rank": rewards.leaderboard(user["id"])["me"],
            "rules": rewards.RULES}


@router.get("/api/me/coins")
def my_coins(request: Request):
    user = auth.require_user(request)
    return {**rewards.balance(user["id"]), "ledger": rewards.ledger(user["id"]), "rules": rewards.RULES}


@router.get("/api/me/leaderboard")
def my_leaderboard(request: Request):
    user = auth.require_user(request)
    return rewards.leaderboard(user["id"])


# ---------------------------------------------------------------------- admin
class Adjust(BaseModel):
    amount: int = Field(ge=-100000, le=100000)
    note: str = Field(min_length=3, max_length=200)


class Revoke(BaseModel):
    note: str = Field("Revoked by admin", min_length=3, max_length=200)


class Disable(BaseModel):
    disabled: bool


@router.get("/api/admin/users")
def admin_users(request: Request):
    auth.require_admin(request)
    rows = db.all_(
        "SELECT u.id, u.email, u.name, u.role, u.disabled, u.created_at, "
        "(SELECT COUNT(*) FROM drives d WHERE d.user_id = u.id) AS drives, "
        "(SELECT COALESCE(SUM(valid_s), 0) FROM drives d WHERE d.user_id = u.id) AS seconds, "
        "(SELECT COALESCE(SUM(hazards), 0) FROM drives d WHERE d.user_id = u.id) AS hazards, "
        "(SELECT MAX(created_at) FROM drives d WHERE d.user_id = u.id) AS last_drive "
        "FROM users u ORDER BY u.created_at DESC")
    out = []
    for r in rows:
        out.append({**{k: r[k] for k in r.keys()}, "minutes": round(r["seconds"] / 60, 1), **rewards.balance(r["id"])})
    issued = db.one("SELECT COALESCE(SUM(amount), 0) AS n FROM coins WHERE status = 'credited'")["n"]
    return {"users": out, "coins_issued": issued, "leaderboard": rewards.leaderboard()["top"]}


@router.get("/api/admin/users/{user_id}")
def admin_user(user_id: int, request: Request):
    auth.require_admin(request)
    u = db.one("SELECT id, email, name, role, disabled, created_at FROM users WHERE id = ?", (user_id,))
    if u is None:
        raise HTTPException(404, "user not found")
    rows = db.all_("SELECT * FROM drives WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
    return {"user": dict(u), **rewards.balance(user_id), "drives": [_drive_dict(r) for r in rows],
            "ledger": rewards.ledger(user_id, 300)}


@router.post("/api/admin/users/{user_id}/adjust")
def admin_adjust(user_id: int, body: Adjust, request: Request):
    admin = auth.require_admin(request)
    if not db.one("SELECT 1 FROM users WHERE id = ?", (user_id,)):
        raise HTTPException(404, "user not found")
    with db.tx() as c:
        rewards.adjust(c, user_id, body.amount, admin["name"], body.note.strip())
    return admin_user(user_id, request)


@router.post("/api/admin/users/{user_id}/disable")
def admin_disable(user_id: int, body: Disable, request: Request):
    admin = auth.require_admin(request)
    if user_id == admin["id"]:
        raise HTTPException(400, "You can't disable your own account")
    with db.tx() as c:
        c.execute("UPDATE users SET disabled = ? WHERE id = ?", (int(body.disabled), user_id))
        if body.disabled:
            c.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    return admin_user(user_id, request)


@router.post("/api/admin/drives/{run_id}/revoke")
def admin_revoke(run_id: str, body: Revoke, request: Request):
    admin = auth.require_admin(request)
    r = db.one("SELECT user_id FROM drives WHERE run_id = ?", (run_id,))
    if r is None:
        raise HTTPException(404, "not a contributed drive")
    with db.tx() as c:
        n = rewards.revoke_drive(c, run_id, admin["name"], body.note.strip())
        c.execute("UPDATE coins SET status = 'void' WHERE run_id = ? AND status = 'pending'", (run_id,))
    return {"revoked": n, **admin_user(r["user_id"], request)}
