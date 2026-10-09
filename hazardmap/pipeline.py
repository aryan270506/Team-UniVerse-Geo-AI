"""End-to-end: video + GPS -> hazards.geojson (+ trajectory, snapshots, summary).

    python -m hazardmap.pipeline --video drive.mp4 --gps drive.gpx --out runs/demo
"""
import argparse
import time
from pathlib import Path
from typing import Callable

from .aggregate import cluster, geolocate
from .calibrate import calibrate
from .config import CAMERA_PRESETS, PipelineConfig, apply_preset
from .detect import HazardDetector
from .export import export_run
from .ingest import Trajectory, iso, load_gps, resolve_video_start, video_info

_detector_cache: dict = {}


def get_detector(cfg: PipelineConfig) -> HazardDetector:
    """Models are expensive to load; reuse them across runs in the server.
    Trackers keep state between calls, so reset them for each new video."""
    key = (cfg.use_world, cfg.device, str(cfg.rdd_weights))
    det = _detector_cache.get(key)
    if det is None:
        det = _detector_cache[key] = HazardDetector(cfg)
    det.cfg = cfg
    det.reset()
    return det


def run(video: str, gps: str, out_dir: str | Path, cfg: PipelineConfig | None = None,
        video_start: float | None = None,
        progress: Callable[[str, float], None] | None = None) -> dict:
    cfg = cfg or PipelineConfig()
    out = Path(out_dir)
    report = progress or (lambda stage, frac: None)
    t_start = time.perf_counter()

    report("loading GPS", 0.0)
    traj = Trajectory(load_gps(gps), cfg.max_gps_gap_s)
    info = video_info(video)
    t0, sync = resolve_video_start(info, traj, cfg.time_offset_s, video_start)

    detector = get_detector(cfg)
    calibration = None
    if cfg.auto_calibrate:
        report("calibrating", 0.0)
        calibration = calibrate(video, cfg, detector.world)
    detector.reset()

    report("detecting", 0.0)
    det = detector.process(video, lambda f: report("detecting", f), info["duration"])

    report("geolocating", 1.0)
    obs = geolocate(det, traj, t0, cfg)
    hazards = cluster(obs, cfg)
    summary = export_run(out, hazards, obs, det.snapshots, traj.geojson(), cfg, {
        "mode": "batch",
        "video": Path(video).name, "gps": Path(gps).name,
        "video_start": iso(t0), "sync_method": sync,
        "duration_s": round(info["duration"], 1),
        "resolution": [det.width, det.height],
        "frames_processed": det.frames_processed,
        "raw_detections": len(det.detections),
        "inference_fps": round(det.fps, 1),
        "wall_time_s": round(time.perf_counter() - t_start, 1),
        "calibration": calibration,
    }, media={"pose_fn": traj.pose, "t0": t0, "duration": info["duration"], "video": video})
    report("done", 1.0)
    return summary


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
    ap.add_argument("--camera", choices=list(CAMERA_PRESETS), default="phone",
                    help="camera type preset (field of view, typical tilt, bonnet area)")
    ap.add_argument("--no-auto", action="store_true", help="don't refine pitch from the horizon")
    ap.add_argument("--cam-height", type=float, help="override preset")
    ap.add_argument("--cam-pitch", type=float, help="override preset (disables auto pitch)")
    ap.add_argument("--hfov", type=float, help="override preset")
    ap.add_argument("--hood", type=float, help="override preset: fraction of frame height that is bonnet")
    ap.add_argument("--road-half-width", type=float, default=6.0,
                    help="metres either side of the vehicle where road-surface damage can be")
    a = ap.parse_args()

    cfg = PipelineConfig(sample_fps=a.fps, time_offset_s=a.offset, use_world=not a.no_world,
                         device=a.device, camera_preset=a.camera, auto_calibrate=not a.no_auto)
    apply_preset(cfg.camera, a.camera)
    for attr, val in (("height_m", a.cam_height), ("pitch_deg", a.cam_pitch), ("hfov_deg", a.hfov), ("hood_frac", a.hood)):
        if val is not None:
            setattr(cfg.camera, attr, val)
    if a.cam_pitch is not None:
        cfg.auto_calibrate = False
    cfg.road_half_width_m = a.road_half_width
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
