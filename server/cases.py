"""Case management: every AI-detected hazard becomes a case that departments work through.

Cases live in SQLite (data/cases.db). They are created lazily from runs/*/hazards.geojson,
so anything processed (batch, replay, phone) shows up as a NEW case on the next read.

    status pipeline: new -> verified -> assigned -> in_progress -> resolved
"""
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from hazardmap.config import ROOT, RUNS_DIR

from . import auth, authorities, drives, rewards, weather
from . import db as db_platform

router = APIRouter()
DB_PATH = Path(os.environ.get("TERRATRACE_CASES_DB") or ROOT / "data" / "cases.db")

STATUSES = ["new", "verified", "assigned", "in_progress", "resolved"]
STATUS_LABEL = {"new": "New", "verified": "Verified", "assigned": "Assigned", "in_progress": "In progress", "resolved": "Resolved"}
PRIORITY_OF = {"high": "P1", "medium": "P2", "low": "P3"}
DEPARTMENTS = ["Roads (PWD / municipal)", "Disaster management", "Electricity board", "Tree authority",
               "Traffic police", "Solid waste"]
DEPT_OF = {"pothole": 0, "crack": 0, "alligator_crack": 0, "flooding": 1, "landslide": 1, "power_line": 2,
           "fallen_tree": 3, "blockage": 4, "debris": 5}
CATEGORY_LABEL = {"pothole": "Pothole", "crack": "Crack", "alligator_crack": "Alligator crack", "flooding": "Flooding",
                  "fallen_tree": "Fallen tree", "power_line": "Power line down", "debris": "Debris",
                  "landslide": "Landslide", "blockage": "Blockage"}
PENALTY = {"high": 25, "medium": 10, "low": 3}

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_index: dict = {"mtimes": {}, "features": {}, "summaries": {}, "weather": {}}
_authority_version = [None]       # authorities file mtime the backfill last ran against


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript("""
            CREATE TABLE IF NOT EXISTS cases (
                num INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT UNIQUE NOT NULL, run_id TEXT NOT NULL, hazard_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'new', priority TEXT NOT NULL, department TEXT NOT NULL,
                assignee TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, resolved_at TEXT);
            CREATE TABLE IF NOT EXISTS activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL, ts TEXT NOT NULL,
                kind TEXT NOT NULL, actor TEXT NOT NULL, text TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS activity_case ON activity(case_id, ts);
        """)
        cols = {r[1] for r in _conn.execute("PRAGMA table_info(cases)")}
        if "authority" not in cols:   # NHAI office (office_code); '' = no office in range
            _conn.execute("ALTER TABLE cases ADD COLUMN authority TEXT")
            _conn.execute("ALTER TABLE cases ADD COLUMN authority_manual INTEGER NOT NULL DEFAULT 0")
        with _conn:  # rebrand: older logs credit the pre-rename AI actor
            _conn.execute("UPDATE activity SET actor = 'TerraTrace AI' WHERE actor = 'HazardMap AI'")
    return _conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def health(features: list, km: float) -> dict:
    pen = sum(PENALTY.get(f["properties"]["severity"], 0) for f in features)
    score = max(0, min(100, round(100 - pen / max(km or 0, 0.2))))
    grade = "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D" if score >= 40 else "F"
    return {"score": score, "grade": grade}


def sync():
    """Pick up new/changed runs and create cases for hazards we haven't seen."""
    conn = _db()
    seen = set()
    for d in sorted(RUNS_DIR.iterdir()) if RUNS_DIR.exists() else []:
        hz, sm = d / "hazards.geojson", d / "summary.json"
        if d.name.startswith("eval-") or not hz.exists() or not sm.exists():
            continue
        seen.add(d.name)
        mtime = hz.stat().st_mtime
        if _index["mtimes"].get(d.name) == mtime:
            continue
        feats = json.loads(hz.read_text())["features"]
        summary = json.loads(sm.read_text())
        summary.pop("config", None)
        _index["mtimes"][d.name] = mtime
        _index["features"][d.name] = {f["properties"]["id"]: f for f in feats}
        _index["summaries"][d.name] = {**summary, "health": health(feats, summary.get("route_km", 0))}
        with conn:
            for f in feats:
                p = f["properties"]
                cid = f"{d.name}/{p['id']}"
                cur = conn.execute(
                    "INSERT OR IGNORE INTO cases (id, run_id, hazard_id, status, priority, department, authority, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'new', ?, ?, ?, ?, ?)",
                    (cid, d.name, p["id"], PRIORITY_OF.get(p["severity"], "P3"),
                     DEPARTMENTS[DEPT_OF.get(p["category"], 0)], _auto_authority(p), p["first_seen"], _now()))
                if cur.rowcount:
                    conn.execute("INSERT INTO activity (case_id, ts, kind, actor, text) VALUES (?, ?, 'detected', 'TerraTrace AI', ?)",
                                 (cid, p["first_seen"], f"Detected {CATEGORY_LABEL.get(p['category'], p['category'])} "
                                  f"({p['severity']}, {round(p['confidence'] * 100)}% confidence) on drive {d.name}"))
    for gone in set(_index["features"]) - seen:                 # run deleted
        for k in ("mtimes", "features", "summaries", "weather"):
            _index[k].pop(gone, None)
    _backfill_authorities(conn)
    _refresh_weather()
    _index["contributors"] = drives.contributors()


_weather_busy = threading.Event()
_weather_tried: dict[str, float] = {}     # run_id -> last fetch attempt (offline / archive lag: retry later)
WEATHER_RETRY_S = 600


def _refresh_weather():
    """Load cached capture weather; fetch what's missing in the background (never blocks a request)."""
    todo = []
    for run_id, feats in _index["features"].items():
        f = RUNS_DIR / run_id / "weather.json"
        m = f.stat().st_mtime if f.exists() else None
        if run_id not in _index["weather"] or _index["weather"][run_id].get("__mtime") != m:
            _index["weather"][run_id] = {**weather.cached(RUNS_DIR / run_id), "__mtime": m}
        if any(h not in _index["weather"][run_id] for h in feats) and \
                time.time() - _weather_tried.get(run_id, 0) > WEATHER_RETRY_S:
            todo.append(run_id)
    if todo and not _weather_busy.is_set():
        _weather_busy.set()
        threading.Thread(target=_prefetch, args=(todo,), daemon=True).start()


def _prefetch(run_ids: list[str]):
    try:
        for run_id in run_ids:
            _weather_tried[run_id] = time.time()
            feats = list(_index["features"].get(run_id, {}).values())
            if feats:
                weather.for_run(RUNS_DIR / run_id, feats)
    finally:
        _weather_busy.clear()


def _auto_authority(p: dict) -> str | None:
    """Nearest NHAI field office for a hazard; '' when none is in range, None when unknown yet."""
    if not authorities.load():
        return None                                              # office list not geocoded yet
    lon, lat = p["centroid"]
    return authorities.nearest_code(lat, lon) or ""


def _backfill_authorities(conn: sqlite3.Connection):
    """Auto-assign cases created before the office list existed (or when it changes)."""
    version = authorities.PATH.stat().st_mtime if authorities.PATH.exists() else None
    if version is None or version == _authority_version[0]:
        return
    _authority_version[0] = version
    rows = conn.execute("SELECT id, run_id, hazard_id FROM cases WHERE authority_manual = 0").fetchall()
    with conn:
        for r in rows:
            feat = _index["features"].get(r["run_id"], {}).get(r["hazard_id"])
            if feat is not None:
                conn.execute("UPDATE cases SET authority = ? WHERE id = ?", (_auto_authority(feat["properties"]), r["id"]))


def authority_codes(run_id: str) -> dict:
    """hazard_id -> assigned office_code for one drive (used by the report)."""
    with _lock:
        sync()
        rows = _db().execute("SELECT hazard_id, authority FROM cases WHERE run_id = ?", (run_id,)).fetchall()
    return {r["hazard_id"]: r["authority"] for r in rows if r["authority"]}


def _case_dict(row: sqlite3.Row) -> dict | None:
    feat = _index["features"].get(row["run_id"], {}).get(row["hazard_id"])
    if feat is None:
        return None                                              # hazard vanished after re-processing
    p = feat["properties"]
    s = _index["summaries"].get(row["run_id"], {})
    return {
        "id": row["id"], "number": f"TT-{row['num']:04d}", "run_id": row["run_id"], "hazard_id": row["hazard_id"],
        "status": row["status"], "priority": row["priority"], "department": row["department"],
        "assignee": row["assignee"], "created_at": row["created_at"], "updated_at": row["updated_at"],
        "resolved_at": row["resolved_at"],
        "category": p["category"], "label": CATEGORY_LABEL.get(p["category"], p["category"]),
        "severity": p["severity"], "confidence": p["confidence"], "frames": p["frames"],
        "centroid": p["centroid"], "extent_m": p["extent_m"], "first_seen": p["first_seen"],
        "video_time_s": p["video_time_s"], "gps_quality": p["gps_quality"],
        "snapshot": f"/runs/{row['run_id']}/{p['snapshot']}" if p.get("snapshot") else None,
        "drive": {"id": row["run_id"], "video": s.get("video"), "start": s.get("video_start"), "mode": s.get("mode"),
                  "annotated": bool((s.get("media") or {}).get("annotated"))},
        "geometry": feat["geometry"],
        **_authority_block(row, p),
        "contributor": (_index.get("contributors") or {}).get(row["run_id"]),
        "weather": weather.compact(_index["weather"].get(row["run_id"], {}).get(row["hazard_id"])),
    }


def _authority_block(row: sqlite3.Row, p: dict) -> dict:
    lon, lat = p["centroid"]
    a = authorities.assignment(row["authority"], lat, lon)
    return {"authority": a["field"] if a else None, "escalation": a["escalation"] if a else None,
            "authority_code": a["field"]["office_code"] if a else None,
            "authority_auto": not row["authority_manual"]}


def all_cases() -> list[dict]:
    with _lock:
        sync()
        rows = _db().execute("SELECT * FROM cases ORDER BY num").fetchall()
        return [c for c in (_case_dict(r) for r in rows) if c]


def _get(case_id: str) -> sqlite3.Row:
    row = _db().execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "case not found")
    return row


def _activity(case_id: str | None = None, limit: int = 200) -> list[dict]:
    q = "SELECT a.*, c.num FROM activity a JOIN cases c ON c.id = a.case_id"
    args: tuple = ()
    if case_id:
        q += " WHERE a.case_id = ?"
        args = (case_id,)
    q += " ORDER BY a.ts DESC, a.id DESC LIMIT ?"
    rows = _db().execute(q, (*args, limit)).fetchall()
    return [{"case_id": r["case_id"], "number": f"TT-{r['num']:04d}", "ts": r["ts"], "kind": r["kind"],
             "actor": r["actor"], "text": r["text"]} for r in rows]


# ---------------------------------------------------------------------- API
@router.get("/api/cases")
def list_cases():
    return {"cases": all_cases(), "statuses": STATUSES, "status_labels": STATUS_LABEL, "departments": DEPARTMENTS}


@router.get("/api/cases/{run_id}/{hazard_id}")
def get_case(run_id: str, hazard_id: str):
    with _lock:
        sync()
        case = _case_dict(_get(f"{run_id}/{hazard_id}"))
        if case is None:
            raise HTTPException(404, "hazard no longer present in this drive")
        closures = RUNS_DIR / run_id / "closures.json"
        closure = None
        if closures.exists():
            closure = next((c for c in json.loads(closures.read_text()).get("closures", []) if c["hazard_id"] == hazard_id), None)
        return {"case": case, "activity": _activity(case["id"]), "closure": closure,
                "statuses": STATUSES, "status_labels": STATUS_LABEL, "departments": DEPARTMENTS}


class CaseUpdate(BaseModel):
    status: str | None = None
    priority: str | None = None
    department: str | None = None
    assignee: str | None = Field(None, max_length=80)
    authority: str | None = Field(None, max_length=80)   # NHAI office_code
    note: str | None = Field(None, max_length=1000)
    actor: str = Field("Control room", max_length=60)


@router.patch("/api/cases/{run_id}/{hazard_id}")
def update_case(run_id: str, hazard_id: str, u: CaseUpdate, request: Request):
    cid = f"{run_id}/{hazard_id}"
    me = auth.current_user(request)
    actor = me["name"] if me else ((u.actor or "Control room").strip() or "Control room")
    with _lock:
        sync()
        row = _get(cid)
        conn, now, logs, sets = _db(), _now(), [], {}
        if u.status and u.status != row["status"]:
            if u.status not in STATUSES:
                raise HTTPException(400, "bad status")
            sets["status"] = u.status
            sets["resolved_at"] = now if u.status == "resolved" else None
            logs.append(("status", f"Status {STATUS_LABEL[row['status']].upper()} → {STATUS_LABEL[u.status].upper()}"))
        if u.priority and u.priority != row["priority"]:
            if u.priority not in ("P1", "P2", "P3"):
                raise HTTPException(400, "bad priority")
            sets["priority"] = u.priority
            logs.append(("priority", f"Priority {row['priority']} → {u.priority}"))
        if u.department and u.department != row["department"]:
            if u.department not in DEPARTMENTS:
                raise HTTPException(400, "bad department")
            sets["department"] = u.department
            logs.append(("department", f"Assigned to {u.department}"))
        if u.authority and u.authority != row["authority"]:
            office = authorities.get(u.authority)
            if office is None:
                raise HTTPException(400, "unknown authority office")
            sets["authority"], sets["authority_manual"] = u.authority, 1
            logs.append(("authority", f"Authority set to {office['office_code']} ({office['designation']})"))
        if u.assignee is not None and u.assignee.strip() != (row["assignee"] or ""):
            sets["assignee"] = u.assignee.strip() or None
            logs.append(("assignee", f"Owner set to {u.assignee.strip() or '—'}"))
        if u.note and u.note.strip():
            logs.append(("note", u.note.strip()))
        if not logs:
            return get_case(run_id, hazard_id)
        with conn:
            if sets:
                sets["updated_at"] = now
                conn.execute(f"UPDATE cases SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), cid))
            for kind, text in logs:
                conn.execute("INSERT INTO activity (case_id, ts, kind, actor, text) VALUES (?, ?, ?, ?, ?)",
                             (cid, now, kind, actor, text))
    return get_case(run_id, hazard_id)


@router.get("/api/cases/{run_id}/{hazard_id}/weather")
def case_weather(run_id: str, hazard_id: str):
    """Capture-time weather (cached per drive) + 48 h outlook for open cases."""
    with _lock:
        sync()
        row = _get(f"{run_id}/{hazard_id}")
        feat = _index["features"].get(run_id, {}).get(hazard_id)
    if feat is None:
        raise HTTPException(404, "hazard no longer present in this drive")
    p = feat["properties"]
    lon, lat = p["centroid"]
    at = weather.for_run(RUNS_DIR / run_id, [feat]).get(hazard_id)
    ahead = weather.outlook(lat, lon, p["category"]) if row["status"] != "resolved" else None
    return {"at_capture": at, "outlook": ahead, "attribution": weather.ATTRIBUTION}


@router.get("/api/overview")
def overview():
    cases = all_cases()
    open_cases = [c for c in cases if c["status"] != "resolved"]
    week = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    by = lambda key, items: {k: sum(1 for c in items if c[key] == k) for k in sorted({c[key] for c in items})}
    with _lock:
        drives = [{"id": k, **{x: v.get(x) for x in ("video", "video_start", "mode", "route_km", "hazards", "duration_s")},
                   "health": v["health"]} for k, v in sorted(_index["summaries"].items(), key=lambda kv: kv[1].get("video_start") or "")]
        activity = _activity(limit=20)
    km = sum(d["route_km"] or 0 for d in drives)
    feats = [{"properties": {"severity": c["severity"]}} for c in open_cases]
    return {
        "total": len(cases), "open": len(open_cases),
        "p1_open": sum(1 for c in open_cases if c["priority"] == "P1"),
        "resolved_7d": sum(1 for c in cases if c["resolved_at"] and c["resolved_at"] >= week),
        "by_status": {s: sum(1 for c in cases if c["status"] == s) for s in STATUSES},
        "by_priority": by("priority", open_cases), "by_department": by("department", open_cases),
        "by_category": by("label", open_cases),
        "by_authority": by("authority_code", [c for c in open_cases if c["authority_code"]]),
        "top_contributors": [[r["name"], r["coins"]] for r in rewards.leaderboard()["top"]],
        "vouchers": dict(db_platform.one("SELECT COUNT(*) AS n, COALESCE(SUM(value_inr), 0) AS inr FROM redemptions WHERE status = 'issued'")),
        "network_health": health(feats, km), "km_surveyed": round(km, 2),
        "drives": drives, "activity": activity, "status_labels": STATUS_LABEL,
    }
