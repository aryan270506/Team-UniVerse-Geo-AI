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

## Platform: contributors, admin, reward coins

TerraTrace has two sides, and everyone must sign in:

| | Who | Where |
|---|---|---|
| **Contributor app** | anyone who registers at `/register.html` | `/app/` (mobile-first) |
| **Admin dashboard** | admin accounts only | `/` (everything above: cases, board, drives, command, insights, plus **Contributors & coins**) |

**Create the admin first.** There is no default password and no way to become admin from the web:

```bash
.venv/bin/python -m tools.create_admin --email you@example.org --name "Control room"   # prompts for a password (12+ chars)
```

Re-running the command resets the password and signs that account out everywhere.

**Contributor app:**
- **Record:** the phone camera and GPS are captured *together* in the browser and uploaded as one drive. The recorder's start time syncs video and GPS exactly, and browser WebM is converted to MP4 on the server.
- **Go live:** stream with real-time detection (`live.html`).
- **Upload drive:** a dashcam video plus its GPX/CSV.
- **Drives:** status (queued with position, analysing, done), plus the hazards found on a map, with weather.
- **Rewards:** balance, ledger and a weekly leaderboard.
- **Account:** change password, sign out.

**Coins** (`server/rewards.py`):
- **Earning:** 1 coin per 30 s of valid footage, +5 per confirmed hazard. Valid means it has GPS, is ≥ 30 s long, covers ≥ 0.2 km, and averages over 5 km/h.
- **Limits:** at most 60 coins per drive and 300 per user per day. A re-upload of the same video (SHA-256) earns nothing.
- **Pending:** coins show as *pending* until analysis finishes.
- **Ledger:** append-only. Admins adjust or revoke coins with a reason; history is never edited.

**Security:**
- **Passwords:** scrypt hashes.
- **Sessions:** random tokens in HttpOnly, SameSite=Lax cookies (Secure over HTTPS); only the token's hash is stored.
- **CSRF:** every write needs the `X-TerraTrace: 1` header.
- **Login lockout:** after 5 failures (per email + IP) for 15 min.
- **Access:** role checks on every route and WebSocket. A contributor can only read their own drives (`/runs/<id>`, `/api/runs/<id>`).
- **Uploads:** capped at 2 GB, only known file types, 20 per day.

Data lives in `data/platform.db` (git-ignored). For tests or a second instance, `TERRATRACE_PLATFORM_DB`, `TERRATRACE_CASES_DB` and `TERRATRACE_RUNS_DIR` override the locations.

## Low-spec devices

**Phones (contributor app).** Tested with Lighthouse and Chrome emulating a budget Android phone (CPU slowed 6×, 360×640 screen, slow 4G):

| Page | Score | Ready in | Data |
|---|---|---|---|
| Login | 99 | 1.8 s | 133 KB |
| Home / Rewards | 93 | 3.3–3.4 s | ~270 KB |
| Map with a route | 81 | 4.8 s | 866 KB (mostly map tiles) |

- **Responsiveness:** the main thread stays responsive (95% of timers fire within 16 ms), and JS memory stays under 10 MB.
- **Live streaming** (real dashcam frames as the camera) adapts to the uplink. It sends 1280 p frames and steps down to 960 p and then 720 p while the upload can't keep up: about 30 fps on Wi-Fi, about 10 fps on 4G, and about 8 fps on slow 4G.
- **Recorder:** keeps the video in memory until upload. Phones reporting ≤ 2 GB RAM record at 1.5 Mbps for up to 15 min; others at 2–2.5 Mbps for up to 30 min.
- **Compression:** text responses (pages, scripts, API JSON) are gzip-compressed, which makes them 60–94% smaller.
- **Photos:** map photos are served as cached thumbnails (`?w=160` / `?w=480`).

**Server without a GPU.** The compute device is picked automatically (CUDA → Apple MPS → CPU; override with `TERRATRACE_DEVICE`), with a speed profile (`TERRATRACE_PROFILE=full|balanced|lite`). Measured on an M5 (budget laptop CPUs are roughly 3–4× slower):

| Device + profile | Analysis speed | Hazards found |
|---|---|---|
| Apple GPU, full (default with a GPU) | ~15 fps | all; accuracy check 16/16 |
| CPU, full | ~4.5 fps | all |
| CPU, balanced (default without a GPU) | ~9 fps | all; accuracy check 16/16 |
| CPU, lite | ~30 fps | road-surface damage only |

Uploads are analysed at 5 fps of footage. So a slow server just takes longer per drive, and live sessions drop frames to stay current.

## Route checker (contributor app → Map)

A Google-Maps-style screen for contributors: enter a start (or use **Your location**) and a destination, or long-press the map to drop a pin. The app then shows up to 3 driving routes and every **open** reported hazard on each one.

- **On the route:** a hazard counts if its point or stretch is within 35 m of the route line. Hazards are listed in driving order ("In 1.1 km"), with type, severity, status, when it was seen, weather and the AI photo.
- **Recommendations:** routes are badged **Safest** (no blocking hazards, lowest risk score) and **Fastest**. A route with a flood, landslide, fallen tree or similar shows "Road may be closed".
- **Navigation:** **Start in Google Maps** hands the chosen trip to Google Maps for turn-by-turn directions.
- **Sharing:** routes can be shared as `/app/#/map?from=lat,lon&to=lat,lon`.
- **Before a route is chosen:** the map shows the open hazards in view.
- **Privacy:**
  - Only open cases are shown; resolving a case removes it from everyone's map.
  - Admin fields (department, assignee, notes, authority contacts) are never sent.
  - Detection photos are served only while their case is open.
- **Services:** all free and keyless, called from the server with caching and a 60 searches per minute per-user limit. Photon (OSM) for search, OSRM for routes, Nominatim for "what's here".

API: `GET /api/map/search?q=`, `GET /api/map/reverse?lat=&lon=`, `GET /api/map/hazards?bbox=w,s,e,n`, `POST /api/map/route` `{"origin": [lon, lat], "destination": [lon, lat]}`, `GET /api/map/photo/<run>/<hazard>`.

## Rewards marketplace (Amazon.in vouchers)

Contributors spend coins on Amazon.in gift vouchers in the app's **Rewards** tab, at **10 coins = ₹1**: ₹100 = 1,000, ₹250 = 2,500, ₹500 = 5,000 and ₹1,000 = 10,000 coins. Vouchers are always available:
- **Codes in stock:** the code is shown instantly, with a copy button and a link to add it on Amazon.in.
- **No stock:** the request is accepted as **Processing**. The coins are held, and the user is promised the code within 48 h. It is filled automatically when you add codes for that voucher, or by hand with **Fill with code**. **Reject & refund** returns the coins.

Past vouchers stay under **My vouchers**.

**Stocking codes (admin → Rewards marketplace):**
- Buy Amazon.in gift cards in bulk, then click **Add codes** and paste them or load the supplier's CSV (`code[,pin][,expiry YYYY-MM-DD]`). Duplicates, expired codes and malformed lines are skipped and reported.
- Codes are issued earliest-expiry first.
- The rail badge shows the number of requests waiting for a code, or "!" when any denomination is below 5 codes.
- Prices and on-sale status are editable per item.

**Safety:**
- **Atomic redemption:** the balance check, picking a code, marking it issued and the −coins ledger entry all happen in one transaction. A balance can't be double-spent, and a code is never issued twice.
- **Limits:** up to 3 vouchers per user per day and ₹2,000 per month. Admins can't redeem.
- **Encrypted codes:** codes are encrypted at rest (Fernet; key in `TERRATRACE_VOUCHER_KEY` or `data/voucher.key`, created on first use and git-ignored). **Back up the key with the database:** without it, stored codes can't be read.
- **Who can see a code:** the full code is shown only to the user who redeemed it. Admins see masked codes and how often each was opened.
- **Void & refund:** if a code doesn't work, **Void & refund** retires the code and returns the coins as a ledger entry.

API: `GET /api/me/market`, `POST /api/me/market/<item>/redeem` `{"confirm": true}`, `GET /api/me/redemptions[/<id>]`; admin `GET /api/admin/market`, `POST /api/admin/market/<item>/codes`, `PATCH /api/admin/market/<item>`, `POST /api/admin/redemptions/<id>/fulfil` `{code, pin, expiry}`, `POST /api/admin/redemptions/<id>/void`.

## Responsible authority (NHAI offices)

Every case is assigned to the responsible road authority automatically, alongside its type-based department. The office list is NHAI's own 'State Wise RO, PIUs and CMU List' (176 offices, dated March 2014) in `data/authorities/india_road_authority_dataset.csv`.

- **Matching:** the nearest NHAI field office (PIU / CMU / site office) to the hazard, by straight-line distance. Its state's Regional Office is listed as escalation. Hazards more than 300 km from any office (e.g. drives outside India) show "No NHAI office nearby".
- **Where it shows:**
  - case page: contacts with tap-to-call, *Email authority* and *Copy details*, and an override dropdown that is logged in the activity feed
  - cases table and CSV export, board cards and search
  - Insights
  - live alerts
  - the printed report, including its "Forwarded to" line
- **Office locations:** the CSV has no coordinates, so they are geocoded once from each address's PIN code (or city) and cached in `data/authorities.json`:

```bash
.venv/bin/python -m tools.geocode_authorities   # re-run after editing the CSV; already-located offices are skipped
```

API: `GET /api/authorities?lat=&lon=` (nearest offices), `GET /api/runs/<id>/authorities` (per hazard), `PATCH /api/cases/<run>/<hazard>` with `{"authority": "<office_code>"}`.

## Weather

Every case carries the weather at its location and capture time, from [Open-Meteo](https://open-meteo.com) (free, no API key; data CC BY 4.0).

- **Which source:** captures from the last ~85 days use Open-Meteo's forecast models (best-match, high resolution). Older ones use the ERA5 reanalysis archive, which goes back to 1940, so 2012 or 2017 drives work too.
- **What is fetched:**
  - conditions in the capture hour: label and icon, temperature, rain, snow, wind and gusts, humidity, cloud, visibility (recent data only), day or night
  - rain in the 24 h and 72 h before the capture
  - flags: heavy rain, waterlogging likely, low visibility, freezing, storm gusts, thunderstorm
  - for open cases, a 48 h outlook with repair or closure advice, e.g. "Rain expected in ~6 h: use cold-mix / temporary patch"
- **Where it shows:**
  - case page card
  - cases table and CSV export, board cards
  - live alerts
  - the "Email authority" and "Copy details" text
  - the printed report
- **Caching:** capture weather never changes, so it is fetched once in the background and stored in `runs/<id>/weather.json`. Hazards within about 5 km and the same hour share one request. The outlook is cached in memory for 30 min. When the service is unreachable the app keeps working without weather and retries after 10 min.

API: `GET /api/cases/<run>/<hazard>/weather` (capture weather and outlook), `GET /api/runs/<id>/weather` (per hazard), `GET /api/weather?lat=&lon=[&t=]` (any point; defaults to now).

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
