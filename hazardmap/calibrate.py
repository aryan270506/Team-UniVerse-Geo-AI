"""Camera calibration without asking the user for angles.

The user picks a camera *type* (preset: field of view, typical tilt, typical bonnet/handlebar
area). The pitch is then refined from the video itself when the horizon can be seen reliably:
YOLO-World already knows "sky", and the bottom edge of the sky across many frames is the
horizon row. If the sky is not consistently visible (helmet cams, city canyons, tunnels) the
preset pitch is kept.
"""
import math

import cv2
import numpy as np

from .config import CALIB_CLASSES

_calib_model = None


def calib_model(weights: str):
    """YOLO-World with only the calibration vocabulary (separate from the hazard detector)."""
    global _calib_model
    if _calib_model is None:
        from ultralytics import YOLO
        _calib_model = YOLO(weights)
        _calib_model.set_classes(CALIB_CLASSES)
    return _calib_model


def sample_frames(video: str, n: int = 24) -> list[np.ndarray]:
    cap = cv2.VideoCapture(video)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    frames = []
    for i in (np.linspace(0, max(total - 1, 0), n).astype(int) if total else range(n)):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, f = cap.read()
        if ok:
            frames.append(f)
    cap.release()
    return frames


def horizon_from_sky(frames: list[np.ndarray], world_model, device: str) -> tuple[float | None, float]:
    """Median bottom edge of the widest 'sky' box, as a fraction of frame height; plus confidence."""
    if world_model is None or not frames:
        return None, 0.0
    names = world_model.names
    sky_ids = [k for k, v in names.items() if v == "sky"]
    if not sky_ids:
        return None, 0.0
    rows = []
    for f in frames:
        h, w = f.shape[:2]
        r = world_model.predict(f, conf=0.15, imgsz=640, device=device, verbose=False, classes=sky_ids)[0]
        best = None
        for b in r.boxes.xyxy.tolist():
            if (b[2] - b[0]) / w < 0.4:                    # a patch of sky between buildings is not the horizon
                continue
            if best is None or b[2] - b[0] > best[2] - best[0]:
                best = b
        if best is not None:
            rows.append(best[3] / h)
    if len(rows) < max(4, len(frames) * 0.3):
        return None, 0.0
    rows = np.array(rows)
    iqr = np.percentile(rows, 75) - np.percentile(rows, 25)
    conf = (len(rows) / len(frames)) * max(0.0, 1.0 - iqr * 5)
    return float(np.median(rows)), round(float(conf), 2)


def calibrate(video: str, cfg, world_model=None) -> dict:
    """Refine the preset pitch from the horizon when it is visible; returns what was decided."""
    frames = sample_frames(video)
    world_model = calib_model(str(cfg.world_weights)) if cfg.use_world else None
    cam = cfg.camera
    hz, conf = horizon_from_sky(frames, world_model, cfg.device)
    out = {"camera": cfg.camera_preset, "hfov_deg": cam.hfov_deg, "hood_frac": cam.hood_frac,
           "preset_pitch_deg": cam.pitch_deg, "horizon": None if hz is None else round(hz, 3),
           "horizon_confidence": conf, "source": "preset"}
    if hz is not None and conf >= 0.4 and frames:
        h, w = frames[0].shape[:2]
        fy = (w / 2) / math.tan(math.radians(cam.hfov_deg) / 2)
        cam.pitch_deg = float(np.clip(math.degrees(math.atan((h / 2 - hz * h) / fy)), -10.0, 35.0))
        out["source"] = "horizon"
    out["pitch_deg"] = round(cam.pitch_deg, 1)
    return out
