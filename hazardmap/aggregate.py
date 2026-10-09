"""Turn per-frame detections into deduplicated, geolocated hazards."""
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
from sklearn.cluster import DBSCAN

from .config import CATEGORIES, PipelineConfig
from .detect import Detection, DetectionResult
from .geoproject import project
from .ingest import GEOD, Trajectory

EARTH_R = 6_371_000.0


@dataclass
class Observation:
    det: Detection
    lat: float
    lon: float
    range_m: float
    epoch: float
    gps_gap_s: float


@dataclass
class Hazard:
    category: str
    obs: list[Observation]
    snapshot_keys: list = field(default_factory=list)

    # --- derived -------------------------------------------------------
    @property
    def weights(self) -> np.ndarray:
        # Close, confident observations are the most trustworthy for position.
        return np.array([o.det.conf / max(o.range_m, 1.0) for o in self.obs])

    @property
    def position(self) -> tuple[float, float]:
        w = self.weights
        lat = float(np.average([o.lat for o in self.obs], weights=w))
        lon = float(np.average([o.lon for o in self.obs], weights=w))
        return lat, lon

    @property
    def frames(self) -> int:
        return len({o.det.frame_idx for o in self.obs})

    @property
    def confidence(self) -> float:
        top = sorted((o.det.conf for o in self.obs), reverse=True)[:3]
        return float(np.mean(top))

    @property
    def extent_m(self) -> float:
        if len(self.obs) < 2:
            return 0.0
        pts = sorted(self.obs, key=lambda o: o.epoch)
        _, _, d = GEOD.inv(pts[0].lon, pts[0].lat, pts[-1].lon, pts[-1].lat)
        return float(d)

    def severity(self) -> tuple[float, str]:
        cat = CATEGORIES[self.category]
        size = min(1.0, max(o.det.area_frac for o in self.obs) * 15)
        persist = min(1.0, self.frames / 10)
        extent = min(1.0, self.extent_m / 50) if cat.linear else 0.0
        score = cat.weight * (0.5 + 0.2 * size + 0.15 * persist + 0.15 * max(extent, self.confidence))
        level = "high" if score >= 0.6 else "medium" if score >= 0.35 else "low"
        return round(score, 3), level

    def geometry(self) -> dict:
        cat = CATEGORIES[self.category]
        if cat.linear and self.extent_m >= 15 and len(self.obs) >= 3:
            pts = sorted(self.obs, key=lambda o: o.epoch)
            coords, last = [], None
            for o in pts:                      # thin to roughly one vertex every 3 m
                if last is None or GEOD.inv(last[0], last[1], o.lon, o.lat)[2] >= 3:
                    coords.append([round(o.lon, 7), round(o.lat, 7)])
                    last = (o.lon, o.lat)
            if len(coords) >= 2:
                return {"type": "LineString", "coordinates": coords}
        lat, lon = self.position
        return {"type": "Point", "coordinates": [round(lon, 7), round(lat, 7)]}


def geolocate(result: DetectionResult, traj: Trajectory, video_t0: float,
              cfg: PipelineConfig) -> list[Observation]:
    obs = []
    for d in result.detections:
        epoch = video_t0 + d.video_t
        pose = traj.pose(epoch)
        x1, y1, x2, y2 = d.box
        if (y1 + y2) / 2 > result.height * (1 - cfg.camera.hood_frac):
            continue                           # reflection/dirt on our own bonnet
        p = project((x1 + x2) / 2, y2, result.width, result.height, pose, cfg.camera)
        if p:
            obs.append(Observation(d, p["lat"], p["lon"], p["range_m"], epoch, pose.gap_s))
    return obs


def cluster(obs: list[Observation], cfg: PipelineConfig) -> list[Hazard]:
    # 1) group by tracker identity: one physical object across frames
    tracks: dict = defaultdict(list)
    for o in obs:
        key = (o.det.source, o.det.track_id if o.det.track_id is not None else f"f{o.det.frame_idx}")
        tracks[(o.det.category, key)].append(o)

    # 2) merge tracks of the same category whose estimated positions are close
    #    (tracker ID switches, both models seeing the same thing, re-detections)
    by_cat: dict = defaultdict(list)
    for (cat, key), items in tracks.items():
        by_cat[cat].append((key, Hazard(cat, items)))

    hazards = []
    for cat, group in by_cat.items():
        coords = np.radians([h.position for _, h in group])
        labels = DBSCAN(eps=cfg.cluster_eps_m / EARTH_R, min_samples=1,
                        metric="haversine").fit_predict(coords)
        merged: dict = defaultdict(lambda: Hazard(cat, []))
        for (key, h), lbl in zip(group, labels):
            merged[lbl].obs.extend(h.obs)
            merged[lbl].snapshot_keys.append(key)
        hazards.extend(merged.values())

    # 3) drop flickers: need several frames unless the detector is very sure
    return [h for h in hazards if h.frames >= cfg.min_frames or h.confidence >= 0.6]
