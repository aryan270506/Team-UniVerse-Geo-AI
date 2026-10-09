"""Generate a time-stamped GPX track for a video that has no GPS log (testing / demos).

    python tools/make_gpx.py --video data/sample/branson.mp4 \
        --route "36.6437,-93.2185;36.6440,-93.2420" --osrm --out data/sample/branson.gpx

The route is densified (optionally snapped to real roads via the public OSRM demo
server), spread evenly over the video duration at 1 Hz, with GPS-like noise and
optional dropouts to exercise the interpolation path.
"""
import argparse
import json
import random
import subprocess
import urllib.request
from datetime import datetime, timedelta, timezone

from pyproj import Geod

GEOD = Geod(ellps="WGS84")


def duration(video: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "json", video], capture_output=True, text=True, check=True).stdout
    return float(json.loads(out)["format"]["duration"])


def osrm(points):
    coords = ";".join(f"{lon},{lat}" for lat, lon in points)
    url = f"https://router.project-osrm.org/route/v1/driving/{coords}?overview=full&geometries=geojson"
    req = urllib.request.Request(url, headers={"User-Agent": "hazardmap-hackathon/0.1"})
    data = json.load(urllib.request.urlopen(req, timeout=20))
    return [(lat, lon) for lon, lat in data["routes"][0]["geometry"]["coordinates"]]


def resample(points, n):
    """n points equally spaced along the polyline."""
    seg = [GEOD.inv(a[1], a[0], b[1], b[0]) for a, b in zip(points, points[1:])]
    total = sum(s[2] for s in seg)
    out, i, acc = [], 0, 0.0
    for k in range(n):
        target = total * k / (n - 1)
        while i < len(seg) - 1 and acc + seg[i][2] < target:
            acc += seg[i][2]
            i += 1
        az, _, d = seg[i]
        lon, lat, _ = GEOD.fwd(points[i][1], points[i][0], az, max(0.0, min(target - acc, d)))
        out.append((lat, lon))
    return out, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--route", required=True, help='"lat,lon;lat,lon;..." waypoints')
    ap.add_argument("--osrm", action="store_true", help="snap route to roads via OSRM")
    ap.add_argument("--start", help="ISO start time (default: video creation time or now)")
    ap.add_argument("--hz", type=float, default=1.0)
    ap.add_argument("--noise", type=float, default=2.0, help="GPS noise sigma (m)")
    ap.add_argument("--drop", type=float, default=0.05, help="fraction of fixes dropped")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    pts = [tuple(map(float, p.split(","))) for p in a.route.split(";")]
    if a.osrm:
        pts = osrm(pts)
    dur = duration(a.video)
    n = max(2, int(dur * a.hz) + 1)
    track, length = resample(pts, n)
    start = datetime.fromisoformat(a.start) if a.start else datetime.now(timezone.utc).replace(microsecond=0)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)

    rng = random.Random(42)
    rows = []
    for k, (lat, lon) in enumerate(track):
        if 0 < k < n - 1 and rng.random() < a.drop:
            continue
        lon, lat, _ = GEOD.fwd(lon, lat, rng.uniform(0, 360), abs(rng.gauss(0, a.noise)))
        t = start + timedelta(seconds=k * dur / (n - 1))
        rows.append(f'      <trkpt lat="{lat:.7f}" lon="{lon:.7f}"><time>{t.isoformat().replace("+00:00", "Z")}</time></trkpt>')

    with open(a.out, "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="hazardmap make_gpx" '
                'xmlns="http://www.topografix.com/GPX/1/1">\n  <trk><name>simulated</name><trkseg>\n')
        f.write("\n".join(rows))
        f.write("\n  </trkseg></trk>\n</gpx>\n")
    print(f"{len(rows)} fixes, {length:.0f} m over {dur:.0f} s ({length / dur * 3.6:.0f} km/h) -> {a.out}")


if __name__ == "__main__":
    main()
