"""Frame sampling + hazard detection/tracking with two lightweight YOLO models."""
import time
from dataclasses import dataclass, field
from typing import Callable, Iterator

import cv2
import numpy as np
from ultralytics import YOLO

from .config import MIN_CONF, RDD_MAP, SCENE_CATEGORIES, WORLD_DISTRACTORS, WORLD_PROMPTS, PipelineConfig
from .scene import SceneGate, horizon_row


@dataclass
class Detection:
    frame_idx: int
    video_t: float          # seconds from start of video
    source: str             # "rdd" | "world"
    track_id: int | None
    label: str              # raw detector label
    category: str
    conf: float
    box: tuple[float, float, float, float]   # x1, y1, x2, y2 (pixels)
    area_frac: float        # box area / frame area
    det_conf: float = 0.0   # raw detector confidence (conf may blend in the CLIP check)
    verify: float | None = None


@dataclass
class DetectionResult:
    detections: list[Detection]
    width: int
    height: int
    frames_processed: int
    infer_seconds: float
    snapshots: dict = field(default_factory=dict)  # (source, key) -> (conf, jpeg bytes)

    @property
    def fps(self) -> float:
        return self.frames_processed / self.infer_seconds if self.infer_seconds else 0.0


def iter_frames(video_path: str, sample_fps: float) -> Iterator[tuple[int, float, np.ndarray]]:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise IOError(f"cannot open video {video_path}")
    native = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, round(native / sample_fps))
    idx = 0
    try:
        while True:
            ok = cap.grab()
            if not ok:
                break
            if idx % step == 0:
                ok, frame = cap.retrieve()
                if ok:
                    yield idx, idx / native, frame
            idx += 1
    finally:
        cap.release()


class HazardDetector:
    def __init__(self, cfg: PipelineConfig):
        self.cfg = cfg
        self.rdd = YOLO(str(cfg.rdd_weights))
        # separate instance: the tracker state lives on the model, the near pass is untracked
        self.rdd_near = YOLO(str(cfg.rdd_weights)) if cfg.rdd_near_imgsz else None
        self.world = None
        if cfg.use_world:
            self.world = YOLO(str(cfg.world_weights))
            self.world.set_classes(list(WORLD_PROMPTS) + WORLD_DISTRACTORS)
        self.verifier = None
        if cfg.use_world and cfg.verify:
            from .verify import ClipVerifier
            self.verifier = ClipVerifier(cfg.device, cfg.verify_threshold)

    def _run(self, model: YOLO, frame, conf: float, imgsz: int):
        return model.track(frame, persist=True, conf=conf, imgsz=imgsz,
                           device=self.cfg.device, tracker="bytetrack.yaml", verbose=False)[0]

    def reset(self):
        """Forget tracker state and snapshots before a new video/stream."""
        for m in (self.rdd, self.world):
            if m is not None and getattr(m, "predictor", None) is not None:
                for tr in getattr(m.predictor, "trackers", []):
                    tr.reset()
        self.snapshots = {}
        self.scene = SceneGate(self.cfg) if (self.verifier and self.cfg.scene_check) else None

    def detect_frame(self, frame: np.ndarray, idx: int, vt: float) -> tuple[list[Detection], float]:
        """Run every model on one frame. Returns detections and seconds spent in inference.
        Keeps the best annotated snapshot per tracked object in self.snapshots."""
        if not hasattr(self, "snapshots"):
            self.reset()
        h, w = frame.shape[:2]
        dets: list[Detection] = []
        infer = 0.0

        # 1) whole-road scene check (flooding / landslide): no box proposal needed
        scene_active: dict = {}
        if self.scene is not None:
            t0 = time.perf_counter()
            scene_active, road_box = self.scene.check(frame, self.verifier)
            infer += time.perf_counter() - t0
            x1, y1, x2, y2 = road_box
            for cat, score in scene_active.items():
                d = Detection(idx, vt, "scene", None, f"scene:{cat}", cat, round(score, 3), road_box,
                              (x2 - x1) * (y2 - y1) / (w * h), round(score, 3), round(score, 3))
                dets.append(d)
                self.snapshots[snapshot_key(d)] = (d.conf, _snapshot(frame, d))
        # road surface can't be judged under water or rubble
        road_hidden = bool(scene_active)
        v_horizon = horizon_row(h, w, self.cfg)
        models = [("rdd", self.rdd, self.cfg.rdd_conf, self.cfg.rdd_imgsz)]
        if self.world is not None:
            models.append(("world", self.world, self.cfg.world_conf, self.cfg.world_imgsz))

        for source, model, conf, imgsz in models:
            t0 = time.perf_counter()
            res = self._run(model, frame, conf, imgsz)
            infer += time.perf_counter() - t0
            if res.boxes is None or (len(res.boxes) == 0 and source != "rdd"):
                continue
            rows = list(zip(res.boxes.xyxy.tolist(), res.boxes.conf.tolist(), res.boxes.cls.int().tolist(),
                            res.boxes.id.int().tolist() if res.boxes.id is not None else [None] * len(res.boxes)))
            if source == "rdd" and self.rdd_near is not None:
                t0 = time.perf_counter()
                near = self.rdd_near.predict(frame, conf=conf, imgsz=self.cfg.rdd_near_imgsz,
                                             device=self.cfg.device, verbose=False)[0]
                infer += time.perf_counter() - t0
                rows += [(b, c, k, None) for b, c, k in zip(near.boxes.xyxy.tolist(), near.boxes.conf.tolist(),
                                                           near.boxes.cls.int().tolist())
                         if all(k != k2 or _iou(b, b2) < 0.4 for b2, _, k2, _ in rows)]
            cands = []
            for box, c, cls, tid in rows:
                label = res.names[cls]
                cat = RDD_MAP.get(label) if source == "rdd" else WORLD_PROMPTS.get(label)
                if not cat or c < MIN_CONF.get(cat, 0.0):
                    continue
                x1, y1, x2, y2 = box
                area = (x2 - x1) * (y2 - y1) / (w * h)
                if source == "rdd" and ((road_hidden and c < self.cfg.road_visible_conf) or y2 < v_horizon + 0.02 * h):
                    continue        # submerged road, or a "pothole" on a hillside/wall above the horizon
                if source == "world" and area > self.cfg.world_max_area and (
                        cat not in SCENE_CATEGORIES or not self.verifier):
                    continue
                cands.append(Detection(idx, vt, source, tid, label, cat, float(c),
                                       (x1, y1, x2, y2), area, float(c)))
            if source == "world" and self.verifier and cands:
                t0 = time.perf_counter()
                ths = [self.cfg.scene_verify_threshold if d.area_frac > self.cfg.world_max_area
                       else self.cfg.verify_threshold for d in cands]
                checks = self.verifier.accept(frame, [d.box for d in cands], [d.category for d in cands], ths)
                infer += time.perf_counter() - t0
                kept = []
                for d, v in zip(cands, checks):
                    if v > 0:
                        d.verify, d.conf = round(v, 3), (d.det_conf + v) / 2
                        kept.append(d)
                cands = kept
            for d in cands:
                dets.append(d)
                key = snapshot_key(d)
                if key not in self.snapshots or d.conf > self.snapshots[key][0]:
                    self.snapshots[key] = (d.conf, _snapshot(frame, d))
        return dets, infer

    def process(self, video_path: str,
                progress: Callable[[float], None] | None = None,
                duration: float | None = None) -> DetectionResult:
        self.reset()
        dets: list[Detection] = []
        n, infer, w, h = 0, 0.0, 0, 0
        for idx, vt, frame in iter_frames(video_path, self.cfg.sample_fps):
            h, w = frame.shape[:2]
            n += 1
            found, secs = self.detect_frame(frame, idx, vt)
            dets.extend(found)
            infer += secs
            if progress and duration:
                progress(min(vt / duration, 1.0))
        return DetectionResult(dets, w, h, n, infer, self.snapshots)


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter + 1e-9)


def snapshot_key(d: Detection):
    """Identity of a physical object: tracker id, or the frame when untracked."""
    return (d.source, d.track_id if d.track_id is not None else f"f{d.frame_idx}")


def annotate(frame: np.ndarray, dets: list[Detection], max_w: int = 640) -> bytes:
    """Small JPEG of the frame with every detection drawn, for the live video panel."""
    scale = min(1.0, max_w / frame.shape[1])
    img = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else frame.copy()
    for d in dets:
        x1, y1, x2, y2 = (int(v * scale) for v in d.box)
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), 2)
        cv2.putText(img, f"{d.category} {d.conf:.2f}", (x1, max(y1 - 6, 14)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])[1].tobytes()


def _snapshot(frame: np.ndarray, d: Detection, max_w: int = 640) -> bytes:
    img = frame.copy()
    x1, y1, x2, y2 = (int(v) for v in d.box)
    cv2.rectangle(img, (x1, y1), (x2, y2), (0, 0, 255), 3)
    cv2.putText(img, f"{d.category} {d.conf:.2f}", (x1, max(y1 - 10, 20)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
    scale = max_w / img.shape[1]
    if scale < 1:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()
