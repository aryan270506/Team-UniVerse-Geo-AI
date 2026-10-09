# Indian road demo: NH3 Manali–Leh highway near Rohtang Pass (Himachal Pradesh)

| File | What |
|---|---|
| `rohtang_nh3_60s.mp4` | 60 s helmet-camera ride through a stream flowing across the highway (640×360, 29.97 fps) |
| `rohtang_nh3_60s.gpx` | 1 Hz GPS track, 53 fixes, ~120 m along NH3 about 900 m below Rohtang Pass, with an 8 s signal dropout |

**The GPS is simulated.** The source video has no GPS log. The track follows the real NH3 road geometry (OSRM routing), with ~3 m noise and a deliberate dropout to exercise interpolation. The exact spot on NH3 is approximate.

## Settings
Pick **Camera type: Helmet / action cam** (CLI `--camera action`). Everything else stays on defaults. The pitch is refined automatically when the horizon is visible.

```bash
.venv/bin/python -m hazardmap.pipeline --video data/india/rohtang_nh3_60s.mp4 --gps data/india/rohtang_nh3_60s.gpx \
    --out runs/india-rohtang-nh3 --camera action
```
Expected result: 1 high-severity **flooding** hazard (a ~117 m line along the road) and 1 **landslide** (the rubble stretch being cleared), with no potholes or cracks.

## Attribution (required, CC BY 3.0)
Video clip trimmed (10:40–11:40) from "Ladakh Road Trip july 2017 – River crossing – Rohtang Pass Manali India Deadliest Road Drive" by **KSOFTECH**, 20 June 2017, licensed under [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/), via [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Ladakh_Road_Trip_july_2017_-_River_crossing_-_Rohtang_Pass_Manali_India_Deadliest_Road_Drive.webm). Changes: trimmed to 60 s, re-encoded to H.264, audio removed.
