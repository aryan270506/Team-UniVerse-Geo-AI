"""Road closures and detours for blocking hazards (flooding, landslide, fallen tree, ...).

For each blocking hazard we ask a public OSRM router for alternative routes between points
before and after it (along the direction of travel), widening the search until a route keeps
well clear of the hazard. That route is the detour; if none exists, it is a hard closure.
Results are cached in runs/<id>/closures.json.
"""
import json
import urllib.request
from pathlib import Path

from pyproj import Geod

GEOD = Geod(ellps="WGS84")
BLOCKING = {"flooding", "landslide", "fallen_tree", "power_line", "blockage", "debris"}
OSRM = "https://router.project-osrm.org/route/v1/driving/"
CLEAR_M = 150          # a detour must stay this far from the hazard
OFFSETS_KM = (3, 6, 10)


def _osrm(points: list, alternatives: bool) -> list:
    coords = ";".join(f"{lon:.6f},{lat:.6f}" for lon, lat in points)
    url = f"{OSRM}{coords}?overview=full&geometries=geojson&alternatives={'3' if alternatives else 'false'}"
    req = urllib.request.Request(url, headers={"User-Agent": "hazardmap-hackathon/0.1"})
    data = json.load(urllib.request.urlopen(req, timeout=20))
    return data.get("routes", []) if data.get("code") == "Ok" else []


def _min_dist(coords: list, lon: float, lat: float) -> float:
    step = max(1, len(coords) // 400)
    return min(GEOD.inv(lon, lat, x, y)[2] for x, y in coords[::step])


def _heading(traj: list, lon: float, lat: float) -> float:
    i = min(range(len(traj)), key=lambda k: GEOD.inv(lon, lat, traj[k][0], traj[k][1])[2])
    a, b = traj[max(i - 3, 0)], traj[min(i + 3, len(traj) - 1)]
    if a == b:
        a, b = traj[0], traj[-1]
    return GEOD.inv(a[0], a[1], b[0], b[1])[0]


def closure_for(feature: dict, traj: list) -> dict:
    p = feature["properties"]
    lon, lat = p["centroid"]
    geom = feature["geometry"]
    blocked = geom["coordinates"] if geom["type"] == "LineString" else [
        c for c in traj if GEOD.inv(lon, lat, c[0], c[1])[2] <= 40] or [[lon, lat]]
    out = {"hazard_id": p["id"], "category": p["category"], "severity": p["severity"],
           "point": [lon, lat], "blocked": blocked, "detour": None}
    if len(traj) < 2:
        return out
    az = _heading(traj, lon, lat)
    for km in OFFSETS_KM:
        a = GEOD.fwd(lon, lat, az + 180, km * 1000)[:2]
        b = GEOD.fwd(lon, lat, az, km * 1000)[:2]
        try:
            routes = _osrm([a, b], True)
        except OSError:
            break
        clear = [r for r in routes if _min_dist(r["geometry"]["coordinates"], lon, lat) > CLEAR_M]
        if not clear:
            continue
        best = min(clear, key=lambda r: r["duration"])
        out["detour"] = {
            "coords": best["geometry"]["coordinates"],
            "km": round(best["distance"] / 1000, 1),
            "minutes": round(best["duration"] / 60),
            "clearance_m": round(_min_dist(best["geometry"]["coordinates"], lon, lat)),
            "search_radius_km": km,
        }
        break
    return out


def closures(run_dir: Path) -> dict:
    cache = run_dir / "closures.json"
    hazards = json.loads((run_dir / "hazards.geojson").read_text())
    if cache.exists() and cache.stat().st_mtime >= (run_dir / "hazards.geojson").stat().st_mtime:
        return json.loads(cache.read_text())
    traj = json.loads((run_dir / "trajectory.geojson").read_text())["features"][0]["geometry"]["coordinates"]
    res = {"closures": [closure_for(f, traj) for f in hazards["features"]
                        if f["properties"]["category"] in BLOCKING and f["properties"]["severity"] != "low"]}
    cache.write_text(json.dumps(res))
    return res
