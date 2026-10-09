"""GPS trajectory loading, heading estimation and per-frame pose interpolation."""
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Geod

GEOD = Geod(ellps="WGS84")

_LAT = ("lat", "latitude", "y")
_LON = ("lon", "lng", "long", "longitude", "x")
_TIME = ("time", "timestamp", "datetime", "t", "utc", "date_time", "gps_time")


@dataclass
class Pose:
    lat: float
    lon: float
    heading: float      # degrees clockwise from north
    speed: float        # m/s
    gap_s: float        # distance in time to the nearest real GPS fix
    valid: bool


def _to_epoch(series: pd.Series) -> np.ndarray:
    if np.issubdtype(series.dtype, np.number):
        v = series.to_numpy(dtype=float)
        return v / 1000.0 if np.nanmedian(v) > 1e11 else v   # ms -> s
    ts = pd.to_datetime(series, utc=True, format="mixed")
    return ts.astype("int64").to_numpy() / 1e9


def load_gps(path: str | Path) -> pd.DataFrame:
    """Load a GPX or CSV track into a DataFrame with columns t (epoch s), lat, lon."""
    path = Path(path)
    if path.suffix.lower() == ".gpx":
        import gpxpy
        gpx = gpxpy.parse(path.read_text())
        rows = [(p.time.timestamp(), p.latitude, p.longitude)
                for trk in gpx.tracks for seg in trk.segments for p in seg.points if p.time]
        rows += [(p.time.timestamp(), p.latitude, p.longitude) for p in gpx.waypoints if p.time]
        df = pd.DataFrame(rows, columns=["t", "lat", "lon"])
    else:
        raw = pd.read_csv(path)
        cols = {c.lower().strip(): c for c in raw.columns}
        pick = lambda names: next((cols[n] for n in names if n in cols), None)
        lat, lon, t = pick(_LAT), pick(_LON), pick(_TIME)
        if not (lat and lon and t):
            raise ValueError(f"CSV needs time/lat/lon columns, got {list(raw.columns)}")
        df = pd.DataFrame({"t": _to_epoch(raw[t]), "lat": raw[lat].astype(float),
                           "lon": raw[lon].astype(float)})
    df = df.dropna().sort_values("t").drop_duplicates("t").reset_index(drop=True)
    if len(df) < 2:
        raise ValueError("GPS track needs at least 2 timestamped points")
    return _add_motion(df)


def _add_motion(df: pd.DataFrame) -> pd.DataFrame:
    """Heading from bearing between fixes (smoothed), speed from distance/time."""
    lat, lon, t = df.lat.to_numpy(), df.lon.to_numpy(), df.t.to_numpy()
    az, _, dist = GEOD.inv(lon[:-1], lat[:-1], lon[1:], lat[1:])
    dt = np.diff(t)
    speed = np.divide(dist, dt, out=np.zeros_like(dist), where=dt > 0)

    # Centre each segment bearing on the fix between segments; when the vehicle is
    # (nearly) stationary the bearing is noise, so carry the last good heading.
    az = np.append(az, az[-1]) % 360
    speed = np.append(speed, speed[-1])
    moving = np.append(dist, dist[-1]) > 1.0
    heading = az.copy()
    last = next((h for h, m in zip(az, moving) if m), 0.0)
    for i in range(len(heading)):
        if moving[i]:
            last = heading[i]
        else:
            heading[i] = last

    # Circular smoothing over a short window.
    s, c = np.sin(np.radians(heading)), np.cos(np.radians(heading))
    k = 3
    s = pd.Series(s).rolling(k, center=True, min_periods=1).mean().to_numpy()
    c = pd.Series(c).rolling(k, center=True, min_periods=1).mean().to_numpy()
    df["heading"] = np.degrees(np.arctan2(s, c)) % 360
    df["speed"] = speed
    return df


class Trajectory:
    """Interpolates vehicle pose at arbitrary epoch times."""

    def __init__(self, df: pd.DataFrame, max_gap_s: float = 15.0):
        self.df = df
        self.t = df.t.to_numpy()
        self.max_gap_s = max_gap_s
        h = np.radians(df.heading.to_numpy())
        self._hs, self._hc = np.sin(h), np.cos(h)

    @property
    def t0(self) -> float:
        return float(self.t[0])

    @property
    def t1(self) -> float:
        return float(self.t[-1])

    def pose(self, t: float) -> Pose:
        if t < self.t[0] - 1 or t > self.t[-1] + 1:
            return Pose(np.nan, np.nan, np.nan, 0.0, np.inf, False)
        lat = float(np.interp(t, self.t, self.df.lat))
        lon = float(np.interp(t, self.t, self.df.lon))
        hs, hc = np.interp(t, self.t, self._hs), np.interp(t, self.t, self._hc)
        heading = float(np.degrees(np.arctan2(hs, hc)) % 360)
        speed = float(np.interp(t, self.t, self.df.speed))
        i = np.searchsorted(self.t, t)
        gap = min(abs(t - self.t[max(i - 1, 0)]), abs(self.t[min(i, len(self.t) - 1)] - t))
        return Pose(lat, lon, heading, speed, float(gap), True)

    def geojson(self) -> dict:
        return {
            "type": "Feature",
            "properties": {"kind": "trajectory", "start": iso(self.t0), "end": iso(self.t1),
                           "points": len(self.t)},
            "geometry": {"type": "LineString",
                         "coordinates": [[round(lo, 7), round(la, 7)]
                                         for la, lo in zip(self.df.lat, self.df.lon)]},
        }


class LiveTrajectory:
    """Trajectory built from GPS fixes arriving one at a time (phone stream).
    Inside the buffer it interpolates like Trajectory; past the newest fix it
    dead-reckons with the last heading and speed for up to max_gap_s."""

    def __init__(self, max_gap_s: float = 15.0, max_accuracy_m: float = 50.0):
        self.max_gap_s = max_gap_s
        self.max_accuracy_m = max_accuracy_m
        self.fixes: list[tuple[float, float, float]] = []
        self.rejected = 0                        # fixes dropped as too coarse (shown on the phone)
        self._traj: Trajectory | None = None

    def add_fix(self, t: float, lat: float, lon: float, accuracy: float | None = None) -> bool:
        if accuracy is not None and accuracy > self.max_accuracy_m:
            self.rejected += 1
            return False                         # too coarse to place hazards with
        if self.fixes and t <= self.fixes[-1][0]:
            return False
        self.fixes.append((t, lat, lon))
        if len(self.fixes) >= 2:
            df = pd.DataFrame(self.fixes, columns=["t", "lat", "lon"])
            self._traj = Trajectory(_add_motion(df), self.max_gap_s)
        return True

    @property
    def ready(self) -> bool:
        return self._traj is not None

    def pose(self, t: float) -> Pose:
        if self._traj is None:
            if self.fixes and abs(t - self.fixes[-1][0]) <= self.max_gap_s:
                _, lat, lon = self.fixes[-1]
                return Pose(lat, lon, np.nan, 0.0, abs(t - self.fixes[-1][0]), False)  # no heading yet
            return Pose(np.nan, np.nan, np.nan, 0.0, np.inf, False)
        tr = self._traj
        if t <= tr.t1:
            return tr.pose(t)
        dt = t - tr.t1
        if dt > self.max_gap_s:
            return Pose(np.nan, np.nan, np.nan, 0.0, dt, False)
        last = tr.df.iloc[-1]
        lon, lat, _ = GEOD.fwd(last.lon, last.lat, last.heading, last.speed * dt)
        return Pose(float(lat), float(lon), float(last.heading), float(last.speed), dt, True)

    def geojson(self) -> dict:
        coords = [[round(lo, 7), round(la, 7)] for _, la, lo in self.fixes]
        t0 = self.fixes[0][0] if self.fixes else 0.0
        t1 = self.fixes[-1][0] if self.fixes else 0.0
        return {"type": "Feature",
                "properties": {"kind": "trajectory", "start": iso(t0), "end": iso(t1), "points": len(coords)},
                "geometry": {"type": "LineString", "coordinates": coords}}


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="milliseconds")


def video_info(path: str | Path) -> dict:
    """fps, frame count, duration and (if present) creation_time from container metadata."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True).stdout
    meta = json.loads(out)
    vs = next(s for s in meta["streams"] if s["codec_type"] == "video")
    num, den = (int(x) for x in vs.get("avg_frame_rate", "30/1").split("/"))
    duration = float(meta["format"].get("duration") or vs.get("duration") or 0)
    tags = {**meta["format"].get("tags", {}), **vs.get("tags", {})}
    created = tags.get("creation_time") or tags.get("com.apple.quicktime.creationdate")
    rotation = 0
    for sd in vs.get("side_data_list", []):
        rotation = int(sd.get("rotation", rotation) or rotation)
    return {
        "fps": num / den if den else 30.0,
        "duration": duration,
        "width": int(vs["width"]), "height": int(vs["height"]),
        "created": pd.Timestamp(created).timestamp() if created else None,
        "rotation": rotation,
    }


def resolve_video_start(info: dict, traj: Trajectory, offset_s: float = 0.0,
                        explicit: float | None = None) -> tuple[float, str]:
    """Pick the epoch time of the first video frame and explain how it was chosen."""
    if explicit is not None:
        return explicit, "explicit --video-start"
    created, dur = info.get("created"), info.get("duration", 0)
    if created:
        # Most phones stamp the start; some Android builds stamp the end of recording.
        for start, how in ((created, "metadata creation_time (start)"),
                           (created - dur, "metadata creation_time (end)")):
            if traj.t0 - 5 <= start + offset_s and start + dur + offset_s <= traj.t1 + 5:
                return start + offset_s, how
    return traj.t0 + offset_s, "aligned to first GPS fix"
