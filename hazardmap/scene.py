"""Whole-road scene check for hazards that have no object shape (flooding, landslide).

Object detectors need to *propose* a box before anything can be verified, and they rarely
propose "flooding" for brown water flowing over a mountain road. Here CLIP scores the road
region of every frame directly; a temporal gate (k of the last n frames) suppresses one-frame
flickers. Output is ordinary Detection objects, so tracking, geolocation, clustering, line
export and live mode treat them like any other hazard.
"""
import math
from collections import deque

import numpy as np

from .config import PipelineConfig, focal_px


def road_rows(height: int, width: int, cfg: PipelineConfig) -> tuple[int, int]:
    """Image rows spanning the visible road: just below the horizon down to the bonnet."""
    cam = cfg.camera
    horizon = height / 2 - focal_px(width, height, cam) * math.tan(math.radians(cam.pitch_deg))
    top = int(np.clip(horizon + 0.03 * height, 0.05 * height, 0.7 * height))
    bottom = int(height * (1 - cam.hood_frac))
    if bottom - top < 0.15 * height:
        top = max(0, bottom - int(0.3 * height))
    return top, bottom


def horizon_row(height: int, width: int, cfg: PipelineConfig) -> float:
    cam = cfg.camera
    return height / 2 - focal_px(width, height, cam) * math.tan(math.radians(cam.pitch_deg))


class SceneGate:
    def __init__(self, cfg: PipelineConfig):
        self.cfg = cfg
        self.hist = {c: deque(maxlen=cfg.scene_window) for c in cfg.scene_thresholds}

    def reset(self):
        for d in self.hist.values():
            d.clear()

    def check(self, frame: np.ndarray, verifier) -> tuple[dict, tuple]:
        """Returns ({category: score} for categories whose gate is open this frame, road box)."""
        h, w = frame.shape[:2]
        top, bottom = road_rows(h, w, self.cfg)
        box = (0.0, float(top), float(w), float(bottom))
        scores = verifier.scores(frame, [box], pad=0.0)[0]
        active = {}
        for cat, th in self.cfg.scene_thresholds.items():
            hit = scores.get(cat, 0.0) >= th
            self.hist[cat].append((hit, scores.get(cat, 0.0)))
            hits = [s for ok, s in self.hist[cat] if ok]
            if hit and len(hits) >= self.cfg.scene_min_hits:
                active[cat] = float(np.mean(hits))
        return active, box
