"""End-to-end: video + GPS -> hazards.geojson (+ trajectory, snapshots, summary).

    python -m hazardmap.pipeline --video drive.mp4 --gps drive.gpx --out runs/demo
"""
import argparse
import csv
import json
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Callable

import numpy as np

from .aggregate import cluster, geolocate
from .config import PipelineConfig
from .detect import HazardDetector
from .ingest import Trajectory, iso, load_gps, resolve_video_start, video_info

_detector_cache: dict = {}


def get_detector(cfg: PipelineConfig) -> HazardDetector:
    """Models are expensive to load; reuse them across runs in the server.
    Trackers keep state between calls, so reset them for each new video."""
    key = (cfg.use_world, cfg.device, str(cfg.rdd_weights))
    det = _detector_cache.get(key)
    if det is None:
        det = _detector_cache[key] = HazardDetector(cfg)
    else:
        det.cfg = cfg
        for m in (det.rdd, det.world):
            if m is not None and getattr(m, "predictor", None) is not None:
                for tr in getattr(m.predictor, "trackers", []):
                    tr.reset()
    return det


def run(video: str, gps: str, out_dir: str | Path, cfg: PipelineConfig | None = None,
        video_start: float | None = None,
        progress: Callable[[str, float], None] | None = None) -> dict:
    cfg = cfg or PipelineConfig()
    out = Path(out_dir)
    (out / "snapshots").mkdir(parents=True, exist_ok=True)
    for old in (out / "snapshots").glob("H*.jpg"):     # stale images from a previous run
        old.unlink()
    report = progress or (lambda stage, frac: None)
    t_start = time.perf_counter()

    report("loading GPS", 0.0)
    traj = Trajectory(load_gps(gps), cfg.max_gps_gap_s)
    info = video_info(video)
    t0, sync = resolve_video_start(info, traj, cfg.time_offset_s, video_start)

    report("detecting", 0.0)
    det = get_detector(cfg).process(video, lambda f: report("detecting", f), info["duration"])

    report("geolocating", 1.0)
    obs = geolocate(det, traj, t0, cfg)
    hazards = cluster(obs, cfg)
    hazards.sort(key=lambda h: min(o.epoch for o in h.obs))

    features = []
    for i, h in enumerate(hazards, 1):
        hid = f"H{i:03d}"
        score, level = h.severity()
        snap = max((det.snapshots[k] for k in h.snapshot_keys if k in det.snapshots),
                   key=lambda s: s[0], default=None)
        if snap:
            (out / "snapshots" / f"{hid}.jpg").write_bytes(snap[1])
        first = min(h.obs, key=lambda o: o.epoch)
        gap = max(o.gps_gap_s for o in h.obs)
        lat, lon = h.position
        features.append({
            "type": "Feature",
            "id": hid,
            "geometry": h.geometry(),
            "properties": {
                "id": hid,
                "category": h.category,
                "labels": sorted({o.det.label for o in h.obs}),
                "severity": level,
                "severity_score": score,
                "confidence": round(h.confidence, 3),
                "detections": len(h.obs),
                "frames": h.frames,
                "sources": sorted({o.det.source for o in h.obs}),
                "first_seen": iso(first.epoch),
                "last_seen": iso(max(o.epoch for o in h.obs)),
                "video_time_s": round(first.det.video_t, 2),
                "min_range_m": round(min(o.range_m for o in h.obs), 1),
                "extent_m": round(h.extent_m, 1),
                "centroid": [round(lon, 7), round(lat, 7)],
                "gps_gap_s": round(gap, 1),
                "gps_quality": "good" if gap <= cfg.max_gps_gap_s else "interpolated",
                "snapshot": f"snapshots/{hid}.jpg" if snap else None,
            },
        })

    fc = {"type": "FeatureCollection", "name": "road_hazards",
          "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
          "features": features}
    (out / "hazards.geojson").write_text(json.dumps(fc, indent=1))
    (out / "trajectory.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "features": [traj.geojson()]}))

    with open(out / "detections.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["frame", "video_t", "source", "track_id", "label", "category", "conf",
                    "det_conf", "clip_verify", "x1", "y1", "x2", "y2", "lat", "lon", "range_m"])
        for o in obs:
            d = o.det
            w.writerow([d.frame_idx, round(d.video_t, 2), d.source, d.track_id, d.label, d.category,
                        round(d.conf, 3), round(d.det_conf, 3), d.verify, *(round(v, 1) for v in d.box),
                        round(o.lat, 7), round(o.lon, 7), round(o.range_m, 1)])

    counts = Counter(f["properties"]["category"] for f in features)
    sev = Counter(f["properties"]["severity"] for f in features)
    lats, lons = traj.df.lat.to_numpy(), traj.df.lon.to_numpy()
    summary = {
        "video": Path(video).name, "gps": Path(gps).name,
        "video_start": iso(t0), "sync_method": sync,
        "duration_s": round(info["duration"], 1),
        "resolution": [det.width, det.height],
        "frames_processed": det.frames_processed,
        "raw_detections": len(det.detections),
        "geolocated_detections": len(obs),
        "hazards": len(features),
        "by_category": dict(counts), "by_severity": dict(sev),
        "inference_fps": round(det.fps, 1),
        "wall_time_s": round(time.perf_counter() - t_start, 1),
        "device": cfg.device,
        "route_km": round(float(np.nansum(_seg_lengths(lats, lons))) / 1000, 2),
        "bbox": [float(lons.min()), float(lats.min()), float(lons.max()), float(lats.max())],
        "config": _cfg_dict(cfg),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    report("done", 1.0)
    return summary


def _seg_lengths(lats, lons):
    from .ingest import GEOD
    return GEOD.inv(lons[:-1], lats[:-1], lons[1:], lats[1:])[2]


def _cfg_dict(cfg: PipelineConfig) -> dict:
    d = asdict(cfg)
    return {k: (str(v) if isinstance(v, Path) else v) for k, v in d.items()}


def main():
    ap = argparse.ArgumentParser(description="Road hazard mapping from dashcam video + GPS")
    ap.add_argument("--video", required=True)
    ap.add_argument("--gps", required=True, help="GPX or CSV (time,lat,lon)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--fps", type=float, default=5.0, help="frames per second to analyse")
    ap.add_argument("--offset", type=float, default=0.0, help="seconds to add to video start time")
    ap.add_argument("--video-start", help="ISO time of first frame (overrides metadata)")
    ap.add_argument("--no-world", action="store_true", help="road-damage model only")
    ap.add_argument("--device", default="mps")
    ap.add_argument("--cam-height", type=float, default=1.3)
    ap.add_argument("--cam-pitch", type=float, default=6.0)
    ap.add_argument("--hfov", type=float, default=70.0)
    ap.add_argument("--hood", type=float, default=0.0, help="fraction of frame height that is bonnet")
    a = ap.parse_args()

    cfg = PipelineConfig(sample_fps=a.fps, time_offset_s=a.offset, use_world=not a.no_world,
                         device=a.device)
    cfg.camera.height_m, cfg.camera.pitch_deg, cfg.camera.hfov_deg = a.cam_height, a.cam_pitch, a.hfov
    cfg.camera.hood_frac = a.hood
    start = None
    if a.video_start:
        import pandas as pd
        start = pd.Timestamp(a.video_start).timestamp()

    last = [""]

    def progress(stage, frac):
        msg = f"\r{stage:<12} {frac * 100:5.1f}%"
        if stage != last[0]:
            msg = "\n" + msg.lstrip("\r")
            last[0] = stage
        print(msg, end="", flush=True)

    s = run(a.video, a.gps, a.out, cfg, start, progress)
    print(f"\n\n{s['hazards']} hazards  {s['by_category']}  severity {s['by_severity']}")
    print(f"{s['frames_processed']} frames @ {s['inference_fps']} fps inference, "
          f"wall {s['wall_time_s']} s  (sync: {s['sync_method']})")
    print(f"-> {Path(a.out) / 'hazards.geojson'}")


if __name__ == "__main__":
    main()
