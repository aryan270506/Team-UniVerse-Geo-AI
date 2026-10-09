"""Route checker for contributors: search places, get driving routes, and see which *open*
hazards lie on each route (in driving order), like a navigation app's incident layer.

Services (free, keyless, called server-side with an identified User-Agent):
  Photon (photon.komoot.io)  place search / type-ahead, biased to the user's position
  OSRM (router.project-osrm.org) driving routes with alternatives (server/closures.py)
  Nominatim                  reverse geocoding (server/places.py)
Only open cases are exposed, with public fields (type, severity, status, date, weather, photo);
admin workflow fields stay private.
"""
import json
import time
import urllib.parse
import urllib.request

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from hazardmap.config import RUNS_DIR

from . import auth, cases, closures, places

router = APIRouter()
UA = {"User-Agent": "TerraTrace-hackathon/0.1 (road hazard mapping demo)"}
PHOTON = "https://photon.komoot.io/api"
ON_ROUTE_M = 35.0
SEARCH_TTL_S = 600
SEARCH_PER_MIN = 60
STATUS_PUBLIC = {"new": "Reported", "verified": "Verified", "assigned": "Repair scheduled", "in_progress": "Repair in progress"}
RISK = {"high": 3, "medium": 2, "low": 1}

_search_cache: dict = {}
_rate: dict = {}


# ---------------------------------------------------------------------- hazards (public view)
def public_hazards() -> list[dict]:
    out = []
    for c in cases.all_cases():
        if c["status"] == "resolved":
            continue
        out.append({
            "id": c["id"], "category": c["category"], "label": c["label"], "severity": c["severity"],
            "priority": c["priority"], "status": STATUS_PUBLIC.get(c["status"], "Reported"),
            "first_seen": c["first_seen"], "centroid": c["centroid"], "geometry": c["geometry"],
            "weather": c.get("weather"), "blocking": c["category"] in closures.BLOCKING,
            "photo": f"/api/map/photo/{c['run_id']}/{c['hazard_id']}" if c.get("snapshot") else None,
        })
    return out


def _hazard_points(h: dict) -> np.ndarray:
    g = h["geometry"]
    pts = g["coordinates"] if g["type"] == "LineString" else [g["coordinates"]] if g["type"] == "Point" else [h["centroid"]]
    return np.asarray(pts, dtype=float).reshape(-1, 2)


def match_route(coords: list, hazards: list[dict]) -> list[tuple[float, dict]]:
    """Hazards within ON_ROUTE_M of the route polyline, with distance along the route (km)."""
    r = np.asarray(coords, dtype=float)
    if len(r) < 2:
        return []
    lat0 = float(r[:, 1].mean())
    kx, ky = 111_320.0 * np.cos(np.radians(lat0)), 110_540.0          # local equirectangular metres
    xy = np.column_stack(((r[:, 0] - r[0, 0]) * kx, (r[:, 1] - r[0, 1]) * ky))
    a, b = xy[:-1], xy[1:]
    seg = b - a
    seg_len = np.hypot(seg[:, 0], seg[:, 1])
    cum = np.concatenate(([0.0], np.cumsum(seg_len)))
    lo, hi = r.min(axis=0) - 0.002, r.max(axis=0) + 0.002              # ~200 m bbox prefilter
    hits = []
    for h in hazards:
        pts = _hazard_points(h)
        if not ((pts >= lo) & (pts <= hi)).all(axis=1).any():
            continue
        best = (np.inf, 0.0)
        for lon, lat in pts:
            p = np.array([(lon - r[0, 0]) * kx, (lat - r[0, 1]) * ky])
            t = np.clip(((p - a) * seg).sum(1) / np.maximum(seg_len ** 2, 1e-9), 0, 1)
            proj = a + seg * t[:, None]
            d = np.hypot(*(proj - p).T)
            i = int(d.argmin())
            if d[i] < best[0]:
                best = (float(d[i]), float(cum[i] + t[i] * seg_len[i]))
        if best[0] <= ON_ROUTE_M:
            hits.append((best[1] / 1000, h))
    return sorted(hits, key=lambda x: x[0])


# ---------------------------------------------------------------------- API
def _throttle(user: dict):
    now = time.time()
    q = [t for t in _rate.get(user["id"], []) if now - t < 60]
    if len(q) >= SEARCH_PER_MIN:
        raise HTTPException(429, "Too many searches. Wait a moment.")
    q.append(now)
    _rate[user["id"]] = q


@router.get("/api/map/search")
def search(request: Request, q: str, lat: float | None = None, lon: float | None = None):
    user = auth.require_user(request)
    q = q.strip()[:120]
    if len(q) < 2:
        return {"results": []}
    key = (q.lower(), None if lat is None else round(lat, 1), None if lon is None else round(lon, 1))
    hit = _search_cache.get(key)
    if hit and time.time() - hit[0] < SEARCH_TTL_S:
        return {"results": hit[1], "cached": True}
    _throttle(user)
    params = {"q": q, "limit": 7, "lang": "en"}
    if lat is not None and lon is not None:
        params.update(lat=f"{lat:.4f}", lon=f"{lon:.4f}")
    try:
        data = json.load(urllib.request.urlopen(urllib.request.Request(f"{PHOTON}?{urllib.parse.urlencode(params)}", headers=UA), timeout=6))
    except (OSError, ValueError):
        raise HTTPException(503, "Search is unavailable right now. Tap the map to drop a pin.")
    res = []
    for f in data.get("features", []):
        p = f.get("properties", {})
        lon_, lat_ = f["geometry"]["coordinates"]
        name = p.get("name") or p.get("street") or p.get("city")
        if not name:
            continue
        detail = ", ".join(dict.fromkeys(x for x in (p.get("district") or p.get("locality"), p.get("city"), p.get("state"), p.get("country"))
                                          if x and x != name))
        res.append({"name": name, "detail": detail, "lat": lat_, "lon": lon_, "type": p.get("osm_value") or p.get("type")})
    _search_cache[key] = (time.time(), res)
    if len(_search_cache) > 2000:
        _search_cache.pop(next(iter(_search_cache)))
    return {"results": res}


@router.get("/api/map/reverse")
def reverse(request: Request, lat: float, lon: float):
    auth.require_user(request)
    try:
        name = places._lookup(lat, lon)
    except (OSError, ValueError):
        name = None
    return {"name": name or f"{lat:.5f}, {lon:.5f}"}


@router.get("/api/map/hazards")
def hazards_in_view(request: Request, bbox: str):
    auth.require_user(request)
    try:
        w, s, e, n = (float(x) for x in bbox.split(","))
    except ValueError:
        raise HTTPException(400, "bbox = west,south,east,north")
    out = [h for h in public_hazards() if w <= h["centroid"][0] <= e and s <= h["centroid"][1] <= n]
    return {"hazards": out[:500], "total": len(out)}


class RouteReq(BaseModel):
    origin: list[float] = Field(min_length=2, max_length=2)        # [lon, lat]
    destination: list[float] = Field(min_length=2, max_length=2)


@router.post("/api/map/route")
def route(body: RouteReq, request: Request):
    auth.require_user(request)
    for lon, lat in (body.origin, body.destination):
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise HTTPException(400, "bad coordinates")
    try:
        found = closures._osrm([body.origin, body.destination], True)
    except (OSError, ValueError):
        raise HTTPException(503, "Routing is unavailable right now. Try again in a moment.")
    if not found:
        raise HTTPException(404, "No driving route found between these places.")
    pool = public_hazards()
    hazards, routes = {}, []
    for i, r in enumerate(found[:3]):
        coords = r["geometry"]["coordinates"]
        hits = match_route(coords, pool)
        ids = []
        for along, h in hits:
            hazards[h["id"]] = h
            ids.append({"id": h["id"], "along_km": round(along, 2)})
        sev = {k: sum(1 for _, h in hits if h["severity"] == k) for k in ("high", "medium", "low")}
        routes.append({"id": i, "coords": coords, "km": round(r["distance"] / 1000, 1), "minutes": round(r["duration"] / 60),
                       "hazards": ids, "counts": sev, "risk": sum(RISK[h["severity"]] for _, h in hits),
                       "blocking": any(h["blocking"] for _, h in hits)})
    fastest = min(routes, key=lambda r: r["minutes"])["id"]
    safest = min(routes, key=lambda r: (r["blocking"], r["risk"], r["minutes"]))["id"]
    return {"routes": routes, "hazards": hazards, "fastest": fastest, "recommended": safest}


THUMB_WIDTHS = (160, 480)


def _thumb(path, w: int):
    """Resized JPEG cached next to the snapshot: list thumbnails are ~10x lighter on slow networks."""
    from PIL import Image
    out = path.parent / "thumbs" / f"{path.stem}-{w}.jpg"
    if not out.exists() or out.stat().st_mtime < path.stat().st_mtime:
        out.parent.mkdir(exist_ok=True)
        with Image.open(path) as im:
            im.thumbnail((w, w * 2))
            im.convert("RGB").save(out, "JPEG", quality=72, optimize=True)
    return out


@router.get("/api/map/photo/{run_id}/{hazard_id}")
def photo(run_id: str, hazard_id: str, request: Request, w: int = 0):
    """Detection photo of an *open* case, for any signed-in user (other run files stay private)."""
    auth.require_user(request)
    c = next((x for x in cases.all_cases() if x["run_id"] == run_id and x["hazard_id"] == hazard_id), None)
    if c is None or c["status"] == "resolved" or not c.get("snapshot"):
        raise HTTPException(404, "photo not available")
    path = RUNS_DIR / run_id / c["snapshot"].split(f"/runs/{run_id}/", 1)[-1]
    if not path.resolve().is_relative_to(RUNS_DIR.resolve()) or not path.exists():
        raise HTTPException(404, "photo not available")
    if w in THUMB_WIDTHS:
        path = _thumb(path, w)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})
