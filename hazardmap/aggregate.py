"""Turn per-frame detections into deduplicated, geolocated hazards."""
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
from sklearn.cluster import DBSCAN

from .config import CATEGORIES, ROAD_SURFACE, SCENE_CATEGORIES, PipelineConfig
from .detect import Detection, DetectionResult, snapshot_key
from .geoproject import project
from .ingest import GEOD, Pose, Trajectory

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


def observe(d: Detection, pose: Pose, width: int, height: int, epoch: float,
            cfg: PipelineConfig) -> Observation | None:
    """Geolocate one detection given the vehicle pose at its capture time."""
    x1, y1, x2, y2 = d.box
    hood_y = height * (1 - cfg.camera.hood_frac)
    if (y1 + y2) / 2 > hood_y:
        return None                            # reflection/dirt on our own bonnet
    if cfg.camera.hood_frac and d.category in ROAD_SURFACE and y2 > hood_y + 0.05 * height:
        return None                            # box runs into our own vehicle: not a clean road view
    p = project((x1 + x2) / 2, y2, width, height, pose, cfg.camera)
    if not p:
        return None
    if d.category in ROAD_SURFACE and abs(p["lateral_m"]) > cfg.road_half_width_m:
        return None                            # off the carriageway: verge, hillside, wall
    return Observation(d, p["lat"], p["lon"], p["range_m"], epoch, pose.gap_s)


def geolocate(result: DetectionResult, traj: Trajectory, video_t0: float,
              cfg: PipelineConfig) -> list[Observation]:
    obs = []
    for d in result.detections:
        epoch = video_t0 + d.video_t
        o = observe(d, traj.pose(epoch), result.width, result.height, epoch, cfg)
        if o:
            obs.append(o)
    return obs


def cluster(obs: list[Observation], cfg: PipelineConfig) -> list[Hazard]:
    # 1) group by tracker identity: one physical object across frames
    tracks: dict = defaultdict(list)
    for o in obs:
        tracks[(o.det.category, snapshot_key(o.det))].append(o)

    # 2) merge tracks of the same category whose estimated positions are close
    #    (tracker ID switches, both models seeing the same thing, re-detections)
    by_cat: dict = defaultdict(list)
    for (cat, key), items in tracks.items():
        by_cat[cat].append((key, Hazard(cat, items)))

    hazards = []
    for cat, group in by_cat.items():
        coords = np.radians([h.position for _, h in group])
        eps = cfg.scene_cluster_eps_m if cat in SCENE_CATEGORIES else cfg.cluster_eps_m
        labels = DBSCAN(eps=eps / EARTH_R, min_samples=1,
                        metric="haversine").fit_predict(coords)
        merged: dict = defaultdict(lambda: Hazard(cat, []))
        for (key, h), lbl in zip(group, labels):
            merged[lbl].obs.extend(h.obs)
            merged[lbl].snapshot_keys.append(key)
        hazards.extend(merged.values())

    # 3) drop flickers: need several frames unless the detector is very sure
    return [h for h in hazards if confirmed(h, cfg)]


def confirmed(h: Hazard, cfg: PipelineConfig) -> bool:
    """Enough evidence to report. Road-surface damage must persist longer than other hazards;
    whole-scene hazards (from the CLIP scene check) must persist, however confident one frame is."""
    if all(o.det.source == "scene" for o in h.obs):
        return h.frames >= cfg.scene_min_hits
    need = cfg.road_min_frames if h.category in ROAD_SURFACE else cfg.min_frames
    return h.frames >= need or h.confidence >= 0.6
