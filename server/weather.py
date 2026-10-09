"""Weather at a hazard's location and capture time, plus a short dispatch outlook.

Source: Open-Meteo (open-meteo.com, CC BY 4.0, no API key). Captures from the last
RECENT_DAYS use the forecast API (best-match high-resolution models); older ones use the ERA5
reanalysis archive (back to 1940). Capture weather never changes, so it is cached per drive in
runs/<id>/weather.json; the outlook is cached in memory for OUTLOOK_TTL_S.
"""
import json
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hazardmap.config import ROAD_SURFACE, SCENE_CATEGORIES

UA = {"User-Agent": "TerraTrace-hackathon/0.1 (road hazard mapping demo)"}
FORECAST = "https://api.open-meteo.com/v1/forecast"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
HOURLY = ["temperature_2m", "relative_humidity_2m", "precipitation", "rain", "snowfall", "weather_code",
          "cloud_cover", "wind_speed_10m", "wind_gusts_10m", "visibility", "is_day"]
RECENT_DAYS = 85
OUTLOOK_TTL_S = 1800
ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"

# WMO weather interpretation codes -> (label, lucide icon)
WMO = {0: ("Clear", "sun"), 1: ("Mainly clear", "sun"), 2: ("Partly cloudy", "cloud-sun"), 3: ("Overcast", "cloud"),
       45: ("Fog", "cloud-fog"), 48: ("Freezing fog", "cloud-fog"),
       51: ("Light drizzle", "cloud-drizzle"), 53: ("Drizzle", "cloud-drizzle"), 55: ("Dense drizzle", "cloud-drizzle"),
       56: ("Freezing drizzle", "cloud-drizzle"), 57: ("Freezing drizzle", "cloud-drizzle"),
       61: ("Light rain", "cloud-rain"), 63: ("Rain", "cloud-rain"), 65: ("Heavy rain", "cloud-rain"),
       66: ("Freezing rain", "cloud-rain"), 67: ("Freezing rain", "cloud-rain"),
       71: ("Light snow", "snowflake"), 73: ("Snow", "snowflake"), 75: ("Heavy snow", "snowflake"), 77: ("Snow grains", "snowflake"),
       80: ("Rain showers", "cloud-rain"), 81: ("Rain showers", "cloud-rain"), 82: ("Violent showers", "cloud-rain"),
       85: ("Snow showers", "snowflake"), 86: ("Snow showers", "snowflake"),
       95: ("Thunderstorm", "cloud-lightning"), 96: ("Thunderstorm, hail", "cloud-lightning"), 99: ("Thunderstorm, hail", "cloud-lightning")}

_net_lock = threading.Lock()
_file_lock = threading.Lock()
_outlook: dict = {}


def _get(url: str, params: dict) -> dict:
    with _net_lock:                                   # be gentle with the free service
        q = urllib.parse.urlencode(params)
        return json.load(urllib.request.urlopen(urllib.request.Request(f"{url}?{q}", headers=UA), timeout=8))


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)


def _round(v, n=1):
    return None if v is None else round(v, n)


def at_capture(lat: float, lon: float, when_iso: str) -> dict | None:
    """Conditions in the capture hour + rain totals before it. None when offline/unavailable."""
    when = _parse(when_iso)
    recent = datetime.now(timezone.utc) - when < timedelta(days=RECENT_DAYS)
    start, end = (when - timedelta(days=3)).date(), when.date()
    try:
        d = _get(FORECAST if recent else ARCHIVE, {
            "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "start_date": start.isoformat(),
            "end_date": end.isoformat(), "hourly": ",".join(HOURLY), "timezone": "GMT"})
    except (OSError, ValueError):
        return None
    h = d.get("hourly") or {}
    times = [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in h.get("time", [])]
    hour = when.replace(minute=0, second=0, microsecond=0)
    if hour not in times:
        return None
    i = times.index(hour)
    col = lambda k: (h.get(k) or [None] * len(times))
    if col("temperature_2m")[i] is None:                   # archive lags ~5 days behind real time
        return None
    rain = lambda hrs: round(sum(v or 0 for v in col("precipitation")[max(0, i - hrs + 1):i + 1]), 1)
    code = col("weather_code")[i]
    label, icon = WMO.get(code, ("Unknown", "cloud"))
    vis = col("visibility")[i]
    at = {"time": hour.isoformat(), "code": code, "label": label, "icon": icon,
          "temp_c": _round(col("temperature_2m")[i]), "humidity": col("relative_humidity_2m")[i],
          "precip_mm": _round(col("precipitation")[i]), "snow_cm": _round(col("snowfall")[i]),
          "cloud": col("cloud_cover")[i], "wind_kmh": _round(col("wind_speed_10m")[i], 0),
          "gust_kmh": _round(col("wind_gusts_10m")[i], 0),
          "visibility_km": None if vis is None else round(vis / 1000, 1), "is_day": bool(col("is_day")[i])}
    out = {"at": at, "rain_24h": rain(24), "rain_72h": rain(72),
           "source": "Open-Meteo forecast models" if recent else "ERA5 reanalysis (Open-Meteo archive)",
           "grid": [d.get("latitude"), d.get("longitude")], "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    out["flags"] = _flags(out)
    return out


def _flags(w: dict) -> list[str]:
    a, f = w["at"], []
    if w["rain_24h"] >= 20:
        f.append(f"Heavy rain: {w['rain_24h']:g} mm in 24 h before detection")
    elif w["rain_24h"] >= 5:
        f.append(f"Wet: {w['rain_24h']:g} mm in 24 h before detection")
    if w["rain_72h"] >= 50:
        f.append(f"Waterlogging likely: {w['rain_72h']:g} mm in 72 h")
    if a["visibility_km"] is not None and a["visibility_km"] < 1:
        f.append(f"Low visibility: {a['visibility_km']:g} km")
    if a["temp_c"] is not None and a["temp_c"] <= 0:
        f.append("Freezing")
    if (a["gust_kmh"] or 0) >= 60:
        f.append(f"Storm gusts: {a['gust_kmh']:g} km/h")
    if a["code"] in (95, 96, 99):
        f.append("Thunderstorm")
    return f


def outlook(lat: float, lon: float, category: str | None = None) -> dict | None:
    """Next 48 h for dispatch planning (rain total/probability, first rainy hour, advice)."""
    key = (round(lat, 1), round(lon, 1))
    hit = _outlook.get(key)
    if not hit or time.time() - hit[0] > OUTLOOK_TTL_S:
        try:
            d = _get(FORECAST, {"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "forecast_days": 3,
                                "hourly": "precipitation,precipitation_probability,weather_code,temperature_2m",
                                "timezone": "GMT"})
        except (OSError, ValueError):
            return None
        hit = _outlook[key] = (time.time(), d)
    h = hit[1].get("hourly") or {}
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    rows = [(datetime.fromisoformat(t).replace(tzinfo=timezone.utc), p or 0, pp or 0, c)
            for t, p, pp, c in zip(h.get("time", []), h.get("precipitation", []),
                                   h.get("precipitation_probability", []), h.get("weather_code", []))]
    rows = [r for r in rows if now <= r[0] < now + timedelta(hours=48)]
    if not rows:
        return None
    total = round(sum(r[1] for r in rows), 1)
    prob = max(r[2] for r in rows)
    first = next((r[0] for r in rows if r[1] >= 0.5 and r[2] >= 40), None)
    in_h = None if first is None else max(0, round((first - now).total_seconds() / 3600))
    worst = max((r[3] for r in rows if r[3] is not None), default=0)
    return {"rain_48h": total, "max_prob": prob, "rain_in_h": in_h, "worst": WMO.get(worst, ("Unknown", "cloud"))[0],
            "advice": _advice(category, total, in_h)}


def _advice(cat: str | None, total: float, in_h: int | None) -> str:
    if cat in SCENE_CATEGORIES or cat in ("fallen_tree", "debris", "power_line"):
        if total >= 20:
            return f"Heavy rain forecast ({total:g} mm in 48 h): expect the hazard to worsen; keep the road closed and monitor"
        return "No heavy rain forecast: good window to clear and reopen" if total < 5 else f"{total:g} mm rain forecast: clear before it arrives"
    if cat in ROAD_SURFACE:
        if in_h is not None and in_h <= 24:
            return f"Rain expected in ~{in_h} h: use cold-mix / temporary patch, schedule permanent repair after"
        return "Dry for the next day: good window for permanent repair" if total < 2 else f"{total:g} mm rain in 48 h: patch soon"
    return "Rain expected: plan crew safety" if in_h is not None else "No rain expected in 48 h"


# ---------------------------------------------------------------------- per-drive cache
def _cache(run_dir: Path) -> dict:
    f = run_dir / "weather.json"
    try:
        return json.loads(f.read_text()) if f.exists() else {}
    except ValueError:
        return {}


def cached(run_dir: Path) -> dict:
    """{hazard_id: capture weather} already on disk; never touches the network."""
    return _cache(run_dir).get("hazards", {})


def for_run(run_dir: Path, features: list) -> dict:
    """Capture weather for every hazard of a drive, fetching only what is missing.
    Hazards in the same ~5 km cell and hour share one request."""
    with _file_lock:
        data = _cache(run_dir)
        hz = data.setdefault("hazards", {})
        missing = [f for f in features if f["properties"]["id"] not in hz]
    if not missing:
        return hz
    groups: dict = {}
    for f in missing:
        p = f["properties"]
        lon, lat = p["centroid"]
        groups.setdefault((round(lat / 0.05), round(lon / 0.05), p["first_seen"][:13]), []).append(p)
    fetched = {}
    for ps in groups.values():
        lon, lat = ps[0]["centroid"]
        w = at_capture(lat, lon, ps[0]["first_seen"])
        if w is None:
            continue                                  # offline / not yet in the archive: retry next time
        for p in ps:
            fetched[p["id"]] = w
    if fetched:
        with _file_lock:
            data = _cache(run_dir)
            data.setdefault("hazards", {}).update(fetched)
            data["attribution"] = ATTRIBUTION
            (run_dir / "weather.json").write_text(json.dumps(data, indent=1))
            hz = data["hazards"]
    return hz


def compact(w: dict | None) -> dict | None:
    """Small summary for lists and cards."""
    if not w:
        return None
    a = w["at"]
    return {"label": a["label"], "icon": a["icon"], "temp_c": a["temp_c"], "rain_24h": w["rain_24h"],
            "rain_72h": w["rain_72h"], "flags": w["flags"]}
