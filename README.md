# TerraTrace — dashcam video → geolocated road hazards

<img src="web/img/terratrace-logo.png" alt="TerraTrace" height="40">

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

# CLI: pick the camera type; pitch is refined from the horizon automatically
.venv/bin/python -m hazardmap.pipeline --video drive.mp4 --gps drive.gpx --out runs/my-drive \
    --camera phone|dashcam|action [--road-half-width 6] [--offset 0] [--no-world]
#   overrides if needed: --cam-height --cam-pitch --hfov --hood

# Accuracy check: zero-config runs against ground truth in data/eval.json
.venv/bin/python -m tools.eval

# Dev server: --reload so code changes are always live
.venv/bin/uvicorn server.app:app --port 8000 --reload --reload-dir hazardmap --reload-dir server

# Dashboard (upload drives from the UI too)
.venv/bin/uvicorn server.app:app --port 8000   # → http://localhost:8000
```

## Hazard CRM (dashboard)

Soft tracking-dashboard design (light canvas, black icon rail, white rounded cards, pastel gradient panels, pill status chips, Poppins). Every hazard the AI confirms becomes a **case** (`TT-0001`…) that departments work through:

`NEW → VERIFIED → ASSIGNED → IN PROGRESS → RESOLVED`

- **Priority:** P1/P2/P3 is taken from the hazard's severity.
- **Department:** set from the hazard type (roads, disaster management, electricity board…).
- **Activity log:** every change is recorded with the operator name set in the sidebar.

| Page | What it is |
|---|---|
| Home | Map of every analysed drive. On load an animated tour flies to each location, grows the route from start to end, drops hazard pins as it passes them, then zooms out to everything (`GET /api/routes`; place names via cached OSM reverse geocoding) |
| Cases | Filterable table across all drives, CSV export |
| Board | Kanban; drag a card to change status |
| Drives | Survey drives with a health grade, report and GeoJSON |
| Command | Synced AI video + map + live alerts (playback or live) |
| Insights | Network health, pipeline, department load, activity |

Case state lives in `data/cases.db` (SQLite, git-ignored). Cases are created automatically from `runs/*/hazards.geojson`. API: `GET /api/cases`, `GET|PATCH /api/cases/<run>/<hazard>`, `GET /api/overview`.

Frontend: plain ES modules with no build step. Design tokens are in `web/css/tokens.css` (palette, type scale, radii, shadows, gradients, status colours); components and pages build only on those tokens.

## Mission control, alerts, report

- **◉ Mission control:** the AI-view video sits beside the map. It is rendered per run as `annotated.mp4`, with detection boxes, flood/landslide tint and a HUD (time, speed, GPS, hazard count). It is synced through `timeline.json`: as the video plays, the vehicle moves on the map, hazard pins drop and alert cards slide in (with sound). Clicking a coloured tick on the timeline jumps to that hazard.
- **Road health:** an A–F grade from hazard severity per km, hazards-by-type bars, and route segments coloured by condition.
- **Closures & detours:** blocking hazards (flood, landslide, fallen tree, power line, debris, blockage) are shown as closed. A detour that keeps clear of the hazard is requested from the public OSRM router (`/api/runs/<id>/closures`, cached).
- **Report:** a printable incident report (`report.html?run=<id>[&hazard=H001]`) with an urgency banner, map, closures, and per-hazard photo, location, recommended action and responsible department. "Save as PDF" produces the file for authorities.

## Live mode (real time)

**+ Process drive** uses the same streaming engine at full GPU speed: every frame is analysed (none are dropped), and you watch the vehicle and its hazards appear on the map with a progress bar. The saved run is identical to the CLI batch output.

Click **● Go live** in the dashboard:

- **Replay a drive**: upload MP4 + GPX. The video plays through the detectors at real-time speed (1×/2×/4×). The vehicle moves on the map and hazards appear the moment they are confirmed.
- **Phone camera**: creates a session and shows a QR code. The phone streams its rear camera (960 px JPEG, 5 fps) and GPS over a WebSocket, and the laptop map updates live.

Phones only allow camera and GPS on HTTPS pages, so phone mode needs the HTTPS server (phone and laptop on the same Wi-Fi or hotspot):

```bash
tools/make_cert.sh                      # self-signed cert for localhost + this machine's LAN IPs (re-run if the Wi-Fi IP changes)
.venv/bin/uvicorn server.app:app --host 0.0.0.0 --port 8443 --ssl-keyfile certs/key.pem --ssl-certfile certs/cert.pem --reload --reload-dir hazardmap --reload-dir server
.venv/bin/uvicorn server.redirect:app --port 8000   # optional: http://localhost:8000 forwards to https://localhost:8443
# laptop: https://localhost:8443  ·  phone: scan the QR code (accept the certificate warning once)
```

If the phone can't open the link:
- **Same network:** the phone and laptop must be on the same network. Many college/office Wi-Fi networks block device-to-device traffic; connect the laptop to the phone's hotspot instead, then re-run `tools/make_cert.sh` and restart (the IP changes).
- **Certificate warning:** accept it on the phone (Chrome: *Advanced → Proceed*; Safari: *Show details → visit this website*).
- **Permissions:** allow both camera and location when the phone asks.

How it works:
- **Frames:** if inference falls behind, older frames are dropped, so latency stays bounded.
- **Hazards:** confirmed after 2 frames and fused on the fly (same track, or same category within 8 m).
- **Saving:** when a session stops, it is re-clustered with the batch algorithm and saved as a normal run.
- **Late joiners:** a dashboard that joins mid-session gets the current trail and hazards.

Headless check (no browser or phone): `python -m tools.live_check replay|device --video … --gps …`.

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
- **Scene check**: CLIP scores the whole road area every frame for flooding and landslides, so these are found even when no detector proposes a box. A k-of-n temporal gate (3 of 5 frames) stops one-frame flickers. Potholes and cracks are not reported in frames where the road is under water or rubble, above the horizon, or overlapping the bonnet, and they need 3 frames of evidence.
- **Precision first**: open-vocab detectors hallucinate ("dry road = flooded road"), so every YOLO-World box is cropped and checked with CLIP against ordinary street-scene negatives; whole-frame boxes are rejected.

## Recording your own demo drive

1. Phone mounted at the windscreen centre, landscape, 1080p/30, horizon about 40–50 % down the frame.
2. Start a GPS logger (e.g. *GPSLogger* on Android, *GPX Tracker* on iOS) at 1 Hz **before** recording.
3. Calibrate: measure the mount height, then set pitch from the horizon row (`pitch = atan((cy − v_horizon) / fy)`), and set `--hood` if the bonnet is visible.

## Known limits

- RDD2022 confuses tar seams/skid marks with longitudinal cracks (low severity, high threshold).
- Without fine-tuning, fallen trees, debris and power lines rely on YOLO-World zero-shot recall, which is weak.
- The flat-ground assumption degrades on hills and steep camber.
