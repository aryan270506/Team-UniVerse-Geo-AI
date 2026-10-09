# HazardMap — dashcam video → geolocated road hazards

Edge-to-GIS pipeline: dashcam/phone **MP4 + GPX/CSV** → hazard detection → pixel-to-lat/lon
projection → deduplicated **GeoJSON** (points + lines) → **Leaflet** dashboard.

```
video.mp4 ─┐
           ├─ ingest      GPX/CSV parse, heading from GPS bearing, per-frame pose interpolation, clock sync
track.gpx ─┘
             detect      YOLOv8s-RDD2022 @1280 (pothole, cracks) + YOLO-World @640 (trees, flood,
                         power lines, debris, blockage) + ByteTrack; CLIP crop check on open-vocab boxes
             geoproject  flat-ground pinhole model: bbox bottom-centre → (forward, lateral) m → geodesic offset
             aggregate   per-track weighted position (conf / range) → DBSCAN 8 m merge → severity
             export      hazards.geojson · trajectory.geojson · detections.csv · snapshots/ · summary.json
```

## Run

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
# models/: rdd_yolov8s.pt (oracl4/RoadDamageDetection), yolov8s-worldv2.pt (auto-downloads)

# CLI
.venv/bin/python -m hazardmap.pipeline --video drive.mp4 --gps drive.gpx --out runs/my-drive \
    --cam-height 1.3 --cam-pitch 6 --hfov 70 [--hood 0.15] [--offset 0] [--no-world]

# Dashboard (upload drives from the UI too)
.venv/bin/uvicorn server.app:app --port 8000   # → http://localhost:8000
```

No GPS log for a video? `tools/make_gpx.py --video v.mp4 --route "lat,lon;lat,lon" --osrm --out v.gpx`
creates a simulated, road-snapped track (testing only).

## Output schema (`hazards.geojson`, RFC 7946, WGS84)

Each feature is a `Point`, or a `LineString` for extended hazards (crack runs, flooding ≥ 15 m), with:
`id, category, labels, severity (low|medium|high), severity_score, confidence, detections, frames,
sources, first_seen, last_seen, video_time_s, min_range_m, extent_m, centroid, gps_gap_s,
gps_quality, snapshot`.

## Key design choices

- **Heading** comes from the bearing between consecutive GPS fixes (circularly smoothed, held when stationary).
- **Clock sync**: explicit `--video-start` > video `creation_time` metadata (tries both start/end conventions) > first GPS fix, plus `--offset`.
- **Projection**: camera height/pitch/HFOV. Detections beyond 40 m are dropped (the vehicle sees them closer later).
- **Dedup**: tracker identity first, then spatial DBSCAN per category. Close, confident observations dominate the position.
- **Precision first**: open-vocab detectors hallucinate ("dry road = flooded road"), so every YOLO-World box is cropped and checked with CLIP against ordinary street-scene negatives; whole-frame boxes are rejected.

## Recording your own demo drive

1. Phone mounted at the windscreen centre, landscape, 1080p/30, horizon about 40–50 % down the frame.
2. Start a GPS logger (e.g. *GPSLogger* on Android, *GPX Tracker* on iOS) at 1 Hz **before** recording.
3. Calibrate: measure the mount height, then set pitch from the horizon row (`pitch = atan((cy − v_horizon) / fy)`), and set `--hood` if the bonnet is visible.

## Known limits

- RDD2022 confuses tar seams/skid marks with longitudinal cracks (low severity, high threshold).
- Without fine-tuning, fallen trees, debris and power lines rely on YOLO-World zero-shot recall, which is weak.
- The flat-ground assumption degrades on hills and steep camber.
