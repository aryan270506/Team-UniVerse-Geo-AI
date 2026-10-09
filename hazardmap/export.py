"""Write a run directory: hazards.geojson, trajectory.geojson, detections.csv, snapshots, summary."""
import csv
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import numpy as np

from .aggregate import Hazard, Observation
from .config import PipelineConfig
from .ingest import GEOD, iso


def hazard_feature(h: Hazard, hid: str, cfg: PipelineConfig, snapshot: str | None) -> dict:
    score, level = h.severity()
    first = min(h.obs, key=lambda o: o.epoch)
    gap = max(o.gps_gap_s for o in h.obs)
    lat, lon = h.position
    return {
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
            "snapshot": snapshot,
        },
    }


def best_snapshot(h: Hazard, snapshots: dict):
    return max((snapshots[k] for k in h.snapshot_keys if k in snapshots), key=lambda s: s[0], default=None)


def export_run(out: Path, hazards: list[Hazard], obs: list[Observation], snapshots: dict,
               trajectory: dict, cfg: PipelineConfig, meta: dict) -> dict:
    """`trajectory` is a GeoJSON LineString Feature; `meta` holds run-level facts for summary.json."""
    out = Path(out)
    (out / "snapshots").mkdir(parents=True, exist_ok=True)
    for old in (out / "snapshots").glob("H*.jpg"):     # stale images from a previous run
        old.unlink()

    hazards = sorted(hazards, key=lambda h: min(o.epoch for o in h.obs))
    features = []
    for i, h in enumerate(hazards, 1):
        hid = f"H{i:03d}"
        snap = best_snapshot(h, snapshots)
        if snap:
            (out / "snapshots" / f"{hid}.jpg").write_bytes(snap[1])
        features.append(hazard_feature(h, hid, cfg, f"snapshots/{hid}.jpg" if snap else None))

    fc = {"type": "FeatureCollection", "name": "road_hazards",
          "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
          "features": features}
    (out / "hazards.geojson").write_text(json.dumps(fc, indent=1))
    (out / "trajectory.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [trajectory]}))

    with open(out / "detections.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["frame", "video_t", "source", "track_id", "label", "category", "conf",
                    "det_conf", "clip_verify", "x1", "y1", "x2", "y2", "lat", "lon", "range_m"])
        for o in obs:
            d = o.det
            w.writerow([d.frame_idx, round(d.video_t, 2), d.source, d.track_id, d.label, d.category,
                        round(d.conf, 3), round(d.det_conf, 3), d.verify, *(round(v, 1) for v in d.box),
                        round(o.lat, 7), round(o.lon, 7), round(o.range_m, 1)])

    coords = np.array(trajectory["geometry"]["coordinates"] or [[np.nan, np.nan]], dtype=float)
    lons, lats = coords[:, 0], coords[:, 1]
    route_m = float(np.nansum(GEOD.inv(lons[:-1], lats[:-1], lons[1:], lats[1:])[2])) if len(coords) > 1 else 0.0
    summary = {
        **meta,
        "geolocated_detections": len(obs),
        "hazards": len(features),
        "by_category": dict(Counter(f["properties"]["category"] for f in features)),
        "by_severity": dict(Counter(f["properties"]["severity"] for f in features)),
        "device": cfg.device,
        "route_km": round(route_m / 1000, 2),
        "bbox": [float(np.nanmin(lons)), float(np.nanmin(lats)), float(np.nanmax(lons)), float(np.nanmax(lats))],
        "config": cfg_dict(cfg),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    return summary


def cfg_dict(cfg: PipelineConfig) -> dict:
    d = asdict(cfg)
    return {k: (str(v) if isinstance(v, Path) else v) for k, v in d.items()}
