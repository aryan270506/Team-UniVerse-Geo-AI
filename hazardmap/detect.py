"""Frame sampling + hazard detection/tracking with two lightweight YOLO models."""
import time
from dataclasses import dataclass, field
from typing import Callable, Iterator

import cv2
import numpy as np
from ultralytics import YOLO

from .config import MIN_CONF, RDD_MAP, WORLD_DISTRACTORS, WORLD_PROMPTS, PipelineConfig


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

    def process(self, video_path: str,
                progress: Callable[[float], None] | None = None,
                duration: float | None = None) -> DetectionResult:
        dets: list[Detection] = []
        snapshots: dict = {}
        n, infer, w, h = 0, 0.0, 0, 0
        models = [("rdd", self.rdd, self.cfg.rdd_conf, self.cfg.rdd_imgsz)]
        if self.world is not None:
            models.append(("world", self.world, self.cfg.world_conf, self.cfg.world_imgsz))

        for idx, vt, frame in iter_frames(video_path, self.cfg.sample_fps):
            h, w = frame.shape[:2]
            n += 1
            for source, model, conf, imgsz in models:
                t0 = time.perf_counter()
                res = self._run(model, frame, conf, imgsz)
                infer += time.perf_counter() - t0
                if res.boxes is None or len(res.boxes) == 0:
                    continue
                ids = res.boxes.id.int().tolist() if res.boxes.id is not None else [None] * len(res.boxes)
                cands = []
                for box, c, cls, tid in zip(res.boxes.xyxy.tolist(), res.boxes.conf.tolist(),
                                            res.boxes.cls.int().tolist(), ids):
                    label = res.names[cls]
                    cat = RDD_MAP.get(label) if source == "rdd" else WORLD_PROMPTS.get(label)
                    if not cat or c < MIN_CONF.get(cat, 0.0):
                        continue
                    x1, y1, x2, y2 = box
                    area = (x2 - x1) * (y2 - y1) / (w * h)
                    if source == "world" and area > self.cfg.world_max_area:
                        continue
                    cands.append(Detection(idx, vt, source, tid, label, cat, float(c),
                                           (x1, y1, x2, y2), area, float(c)))
                if source == "world" and self.verifier and cands:
                    t0 = time.perf_counter()
                    checks = self.verifier.accept(frame, [d.box for d in cands], [d.category for d in cands])
                    infer += time.perf_counter() - t0
                    kept = []
                    for d, v in zip(cands, checks):
                        if v > 0:
                            d.verify, d.conf = round(v, 3), (d.det_conf + v) / 2
                            kept.append(d)
                    cands = kept
                for d in cands:
                    dets.append(d)
                    key = (source, d.track_id if d.track_id is not None else f"f{idx}")
                    if key not in snapshots or d.conf > snapshots[key][0]:
                        snapshots[key] = (d.conf, _snapshot(frame, d))
            if progress and duration:
                progress(min(vt / duration, 1.0))
        return DetectionResult(dets, w, h, n, infer, snapshots)


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
