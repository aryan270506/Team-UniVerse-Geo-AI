"""Shared background worker: the GPU (MPS) and the stateful trackers are not shared safely,
so batch jobs and live sessions all run on this single thread, one at a time."""
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from hazardmap.config import CAMERA_PRESETS, PipelineConfig, apply_preset

executor = ThreadPoolExecutor(max_workers=1)


def new_run_id(name: str, prefix: str = "") -> str:
    slug = re.sub(r"[^\w\-]+", "-", name.strip().lower()).strip("-") or "drive"
    return f"{datetime.now():%Y%m%d-%H%M%S}-{prefix}{slug}"[:60]


def make_cfg(sample_fps=5.0, time_offset=0.0, use_world=True, camera="phone", auto_calibrate=True,
             cam_height=None, cam_pitch=None, hfov=None, hood=None, road_half_width=6.0) -> PipelineConfig:
    """Camera preset first; any explicitly given number overrides it (an explicit pitch also
    turns off horizon auto-calibration)."""
    camera = camera if camera in CAMERA_PRESETS else "phone"
    cfg = PipelineConfig(sample_fps=sample_fps, time_offset_s=time_offset, use_world=use_world,
                         camera_preset=camera, auto_calibrate=auto_calibrate and cam_pitch is None)
    apply_preset(cfg.camera, camera)
    for attr, val in (("height_m", cam_height), ("pitch_deg", cam_pitch), ("hfov_deg", hfov), ("hood_frac", hood)):
        if val is not None:
            setattr(cfg.camera, attr, val)
    cfg.road_half_width_m = road_half_width
    return cfg


def num(v: str | None) -> float | None:
    """Optional numeric form field: blank means 'use the camera preset'."""
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None
