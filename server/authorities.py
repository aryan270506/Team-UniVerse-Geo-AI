"""Responsible road authority for a location: the nearest NHAI field office, plus the Regional
Office of its state as escalation.

Offices come from data/authorities.json (NHAI office list, geocoded once by
tools/geocode_authorities.py). Matching is by straight-line distance; beyond MAX_KM there is
no NHAI office responsible (e.g. a drive outside India).
"""
import json
import math

from fastapi import APIRouter

from hazardmap.config import ROOT

router = APIRouter()
PATH = ROOT / "data" / "authorities.json"
FIELD_TYPES = {"PIU", "CMU", "Site Office", "RO-PIU"}
MAX_KM = 300.0

_offices: list[dict] = []
_by_code: dict[str, dict] = {}
_mtime: float | None = None


def load() -> list[dict]:
    """Offices with coordinates; re-read when the geocoder rewrites the file."""
    global _offices, _by_code, _mtime
    mtime = PATH.stat().st_mtime if PATH.exists() else None
    if mtime != _mtime:
        data = json.loads(PATH.read_text()) if mtime else []
        _offices = [o for o in data if o.get("lat") is not None]
        _by_code = {o["office_code"]: o for o in _offices}
        _mtime = mtime
    return _offices


def get(code: str | None) -> dict | None:
    load()
    return _by_code.get(code) if code else None


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def nearby(lat: float, lon: float, n: int = 8, types: set | None = FIELD_TYPES) -> list[dict]:
    """Offices sorted by distance, each with distance_km."""
    ranked = sorted(((_km(lat, lon, o["lat"], o["lon"]), o) for o in load() if not types or o["office_type"] in types),
                    key=lambda t: t[0])
    return [{**o, "distance_km": round(d, 1)} for d, o in ranked[:n] if d <= MAX_KM]


def escalation_for(office: dict) -> dict | None:
    """Regional Office of the office's state; the nearest RO if the state has none listed."""
    ros = [o for o in load() if o["office_type"] == "RO"]
    same = [o for o in ros if o["state"] == office["state"]]
    pool = same or ros
    if not pool:
        return None
    best = min(pool, key=lambda o: _km(office["lat"], office["lon"], o["lat"], o["lon"]))
    return best if best["office_code"] != office["office_code"] else None


def assignment(code: str | None, lat: float, lon: float) -> dict | None:
    """Full authority block for a case: field office (with distance from the hazard) + escalation."""
    office = get(code)
    if office is None:
        return None
    return {"field": {**office, "distance_km": round(_km(lat, lon, office["lat"], office["lon"]), 1)},
            "escalation": escalation_for(office)}


def nearest_code(lat: float, lon: float) -> str | None:
    hits = nearby(lat, lon, 1)
    return hits[0]["office_code"] if hits else None


@router.get("/api/authorities")
def api_nearby(lat: float, lon: float, n: int = 8):
    return {"offices": nearby(lat, lon, n), "max_km": MAX_KM}
