"""Streaming engine: frames arrive one at a time, hazards are fused incrementally and
pushed out as events; at the end the session is saved as a normal run.

Events (dicts passed to `emit`):
  pose    {lat, lon, heading, speed, valid}          every processed frame
  hazard  {feature}                                   on confirmation and on meaningful change
  frame   {jpeg: base64}                              annotated thumbnail, ~2 per second
  stats   {fps, latency_s, frames, hazards, ...}      ~1 per second
  done    {run_id, summary} | error {message}
"""
import base64
import math
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from .aggregate import Hazard, Observation, cluster, confirmed, observe
from .config import RUNS_DIR, SCENE_CATEGORIES, PipelineConfig
from .detect import Detection, annotate, snapshot_key
from .export import best_snapshot, export_run, hazard_feature
from .ingest import GEOD, Pose, iso


class LiveHazard(Hazard):
    """A hazard being built up from a stream; confirmed once seen often enough."""

    def __init__(self, category: str, hid: str):
        super().__init__(category, [])
        self.hid = hid
        self.keys: set = set()
        self.confirmed = False
        self.sig = None              # last emitted (severity, lat, lon, frames bucket)
        self.emitted_at = 0.0


class LiveSession:
    def __init__(self, cfg: PipelineConfig, run_id: str, mode: str, detector,
                 pose_fn: Callable[[float], Pose], trajectory_fn: Callable[[], dict],
                 emit: Callable[[dict], None], meta: dict | None = None, source_video: str | None = None):
        self.cfg, self.run_id, self.mode = cfg, run_id, mode
        self.det = detector
        self.pose_fn, self.trajectory_fn = pose_fn, trajectory_fn
        self.emit = emit
        self.meta = meta or {}
        self.source_video = source_video
        self.out = RUNS_DIR / run_id
        (self.out / "snapshots").mkdir(parents=True, exist_ok=True)

        self.lock = threading.Lock()
        self.hazards: list[LiveHazard] = []
        self.obs: list[Observation] = []
        self.raw = 0
        self.trail: list[list[float]] = []
        self.frames = 0
        self.dropped = 0
        self.infer_s = 0.0
        self.size = (0, 0)
        self.started = time.time()
        self.first_epoch: float | None = None
        self._first_vt: float | None = None
        self.last_epoch: float | None = None
        self._fps_ema = 0.0
        self._last_frame_t = None
        self._last_thumb = 0.0
        self._last_stats = 0.0
        self.latency = 0.0
        self.progress: float | None = None     # 0..1 when the input has a known length
        detector.reset()

    # ------------------------------------------------------------------ input
    def feed(self, frame: np.ndarray, epoch: float, idx: int, vt: float, latency_s: float = 0.0):
        h, w = frame.shape[:2]
        self.size = (w, h)
        if self.first_epoch is None:
            self.first_epoch, self._first_vt = epoch, vt
        self.last_epoch = epoch
        dets, secs = self.det.detect_frame(frame, idx, vt)
        self.infer_s += secs
        pose = self.pose_fn(epoch)

        now = time.perf_counter()
        if self._last_frame_t is not None:
            inst = 1.0 / max(now - self._last_frame_t, 1e-3)
            self._fps_ema = inst if not self._fps_ema else 0.8 * self._fps_ema + 0.2 * inst
        self._last_frame_t = now
        self.latency = latency_s

        changed = []
        with self.lock:
            self.frames += 1
            self.raw += len(dets)
            if pose.valid:
                self.trail.append([round(pose.lon, 7), round(pose.lat, 7)])
            for d in dets:
                o = observe(d, pose, w, h, epoch, self.cfg)
                if o:
                    self.obs.append(o)
                    hz = self._fuse(o)
                    if hz not in changed:
                        changed.append(hz)

        if pose.valid:
            self.emit({"type": "pose", "lat": pose.lat, "lon": pose.lon,
                       "heading": None if math.isnan(pose.heading) else round(pose.heading, 1),
                       "speed": round(pose.speed, 1), "t": iso(epoch)})
        for hz in changed:
            self._maybe_emit_hazard(hz)
        if now - self._last_thumb >= 0.5:
            self._last_thumb = now
            self.emit({"type": "frame", "jpeg": base64.b64encode(annotate(frame, dets, 480)).decode()})
        if now - self._last_stats >= 1.0:
            self._last_stats = now
            self.emit({"type": "stats", **self.stats()})

    def skipped(self):
        """Input frame dropped because inference fell behind (keeps latency bounded)."""
        self.dropped += 1

    # ------------------------------------------------------------------ fusion
    def _fuse(self, o: Observation) -> LiveHazard:
        key = snapshot_key(o.det)
        cat = o.det.category
        best, best_d = None, (self.cfg.scene_cluster_eps_m if cat in SCENE_CATEGORIES else self.cfg.cluster_eps_m)
        for hz in self.hazards:
            if hz.category != cat:
                continue
            if key in hz.keys:
                best = hz
                break
            lat, lon = hz.position
            d = GEOD.inv(lon, lat, o.lon, o.lat)[2]
            if d <= best_d:
                best, best_d = hz, d
        if best is None:
            best = LiveHazard(cat, f"L{len(self.hazards) + 1:03d}")
            self.hazards.append(best)
        best.obs.append(o)
        best.keys.add(key)
        best.snapshot_keys = list(best.keys)
        if not best.confirmed and confirmed(best, self.cfg):
            best.confirmed = True
        return best

    def _maybe_emit_hazard(self, hz: LiveHazard):
        if not hz.confirmed:
            return
        with self.lock:
            _, level = hz.severity()
            lat, lon = hz.position
            sig = (level, round(lat, 5), round(lon, 5), min(hz.frames // 5, 6))
            now = time.time()
            if sig == hz.sig or (hz.sig is not None and now - hz.emitted_at < 1.0):
                return
            first = hz.sig is None
            hz.sig, hz.emitted_at = sig, now
            snap = best_snapshot(hz, self.det.snapshots)
            rel = None
            if snap:
                rel = f"snapshots/live-{hz.hid}.jpg"
                (self.out / rel).write_bytes(snap[1])
            feat = hazard_feature(hz, hz.hid, self.cfg, rel)
        self.emit({"type": "hazard", "new": first, "feature": feat})

    # ------------------------------------------------------------------ views
    def stats(self) -> dict:
        with self.lock:
            confirmed = [h for h in self.hazards if h.confirmed]
            return {
                "run_id": self.run_id, "mode": self.mode,
                "fps": round(self._fps_ema, 1),
                "latency_s": round(self.latency, 2),
                "frames": self.frames, "dropped": self.dropped,
                "hazards": len(confirmed),
                "high": sum(1 for h in confirmed if h.severity()[1] == "high"),
                "route_km": round(_route_m(self.trail) / 1000, 2),
                "elapsed_s": round(time.time() - self.started, 1),
                "progress": None if self.progress is None else round(self.progress, 3),
            }

    def snapshot(self) -> dict:
        """Full current state for a viewer that joins mid-session."""
        with self.lock:
            feats = []
            for hz in self.hazards:
                if hz.confirmed:
                    rel = f"snapshots/live-{hz.hid}.jpg"
                    feats.append(hazard_feature(hz, hz.hid, self.cfg, rel if (self.out / rel).exists() else None))
            trail = list(self.trail)
        return {"type": "snapshot", "trail": trail, "features": feats, "stats": self.stats()}

    # ------------------------------------------------------------------ end
    def finalize(self) -> dict:
        """Batch-quality re-clustering of everything observed, saved like a normal run."""
        with self.lock:
            obs = list(self.obs)
        hazards = cluster(obs, self.cfg)
        traj = self.trajectory_fn()
        if not traj["geometry"]["coordinates"]:
            traj["geometry"]["coordinates"] = self.trail
        duration = (self.last_epoch - self.first_epoch) if self.first_epoch is not None else 0.0
        summary = export_run(self.out, hazards, obs, self.det.snapshots, traj, self.cfg, {
            "mode": self.mode,
            **self.meta,
            "video_start": iso(self.first_epoch or time.time()),
            "duration_s": round(duration, 1),
            "resolution": list(self.size),
            "frames_processed": self.frames,
            "frames_dropped": self.dropped,
            "raw_detections": self.raw,
            "inference_fps": round(self.frames / self.infer_s, 1) if self.infer_s else 0.0,
            "wall_time_s": round(time.time() - self.started, 1),
        }, media=None if self.first_epoch is None else {
            "pose_fn": self.pose_fn, "t0": self.first_epoch - (self._first_vt or 0.0),
            "duration": duration + (self._first_vt or 0.0), "video": self.source_video})
        for f in self.out.glob("snapshots/live-*.jpg"):    # superseded by H*.jpg
            f.unlink()
        return summary


def _route_m(trail: list) -> float:
    if len(trail) < 2:
        return 0.0
    a = np.array(trail, dtype=float)
    return float(np.nansum(GEOD.inv(a[:-1, 0], a[:-1, 1], a[1:, 0], a[1:, 1])[2]))
