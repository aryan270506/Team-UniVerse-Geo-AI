"""Human-readable place names for drives (reverse geocoding of the start point).

Uses the public OSM Nominatim service (coordinates only, identified User-Agent, max 1 request
per second as its usage policy asks). Each drive's answer is cached in runs/<id>/place.json,
so a drive is looked up once.
"""
import json
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

_lock = threading.Lock()
_last = [0.0]


def _lookup(lat: float, lon: float) -> str | None:
    with _lock:                                   # Nominatim policy: <= 1 request/second
        wait = 1.05 - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
    q = urllib.parse.urlencode({"lat": f"{lat:.5f}", "lon": f"{lon:.5f}", "format": "jsonv2", "zoom": 12, "accept-language": "en"})
    req = urllib.request.Request(f"https://nominatim.openstreetmap.org/reverse?{q}",
                                 headers={"User-Agent": "HazardMap-hackathon/0.1 (road hazard mapping demo)"})
    data = json.load(urllib.request.urlopen(req, timeout=3))
    a = data.get("address", {})
    local = a.get("village") or a.get("town") or a.get("city") or a.get("suburb") or a.get("county") or a.get("state_district")
    region = a.get("state") or a.get("country")
    parts = [p for p in (local, region) if p]
    return ", ".join(dict.fromkeys(parts)) or data.get("display_name", "").split(",")[0] or None


def place_for(run_dir: Path, lat: float, lon: float) -> str:
    cache = run_dir / "place.json"
    if cache.exists():
        return json.loads(cache.read_text()).get("name") or f"{lat:.4f}, {lon:.4f}"
    try:
        name = _lookup(lat, lon)
    except (OSError, ValueError):
        return f"{lat:.4f}, {lon:.4f}"            # offline: don't cache, try again next time
    cache.write_text(json.dumps({"name": name, "lat": lat, "lon": lon}))
    return name or f"{lat:.4f}, {lon:.4f}"
