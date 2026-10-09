"""Pipeline configuration: camera model, detection classes, severity weights."""
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "models"
RUNS_DIR = ROOT / "runs"


@dataclass
class CameraConfig:
    height_m: float = 1.3      # camera height above road
    pitch_deg: float = 6.0     # downward tilt of optical axis
    hfov_deg: float = 70.0     # horizontal field of view (typical phone main cam ~65-75)
    hood_frac: float = 0.0     # bottom fraction of the frame covered by the car's bonnet
    min_dist_m: float = 2.0    # clamp projected distances to a trustworthy band
    max_dist_m: float = 40.0


# Hazard taxonomy. Each detector label maps to a category that drives
# severity, colour on the map and whether it is exported as a line.
@dataclass
class Category:
    name: str
    weight: float          # base severity 0..1
    linear: bool = False   # extended hazards become LineStrings


CATEGORIES = {
    "pothole":       Category("pothole", 0.65),
    "crack":         Category("crack", 0.30, linear=True),
    "alligator_crack": Category("alligator_crack", 0.45),
    "flooding":      Category("flooding", 0.80, linear=True),
    "fallen_tree":   Category("fallen_tree", 0.90),
    "power_line":    Category("power_line", 1.00),
    "debris":        Category("debris", 0.60),
    "landslide":     Category("landslide", 1.00),
    "blockage":      Category("blockage", 0.50),
}

# Road-damage model (RDD2022 YOLOv8s) label -> category
RDD_MAP = {
    "Longitudinal Crack": "crack",
    "Transverse Crack": "crack",
    "Alligator Crack": "alligator_crack",
    "Potholes": "pothole",
}

# Open-vocabulary prompts for YOLO-World -> category
WORLD_PROMPTS = {
    "fallen tree": "fallen_tree",
    "tree trunk lying on road": "fallen_tree",
    "broken tree branch": "fallen_tree",
    "flooded road": "flooding",
    "waterlogged street": "flooding",
    "large puddle": "flooding",
    "fallen power line": "power_line",
    "fallen electric pole": "power_line",
    "debris": "debris",
    "rubble": "debris",
    "rocks on road": "debris",
    "landslide": "landslide",
    "mud": "landslide",
    "road barricade": "blockage",
    "traffic cone": "blockage",
    "construction barrier": "blockage",
}
# Everyday objects given to YOLO-World so that boxes on them get the right label
# instead of the closest hazard prompt. They are never reported.
WORLD_DISTRACTORS = ["car", "truck", "person", "traffic light", "road sign", "utility pole",
                     "building", "road", "sky"]

# Per-category minimum detector confidence (on top of the model-level threshold).
MIN_CONF = {"crack": 0.35, "alligator_crack": 0.30}


@dataclass
class PipelineConfig:
    sample_fps: float = 5.0
    rdd_conf: float = 0.20
    world_conf: float = 0.08       # low on purpose: CLIP verification filters the proposals
    use_world: bool = True
    verify: bool = True            # CLIP crop check on open-vocab detections
    verify_threshold: float = 0.45
    world_max_area: float = 0.35   # a hazard box covering most of the frame is a hallucination
    device: str = "mps"
    rdd_imgsz: int = 1280          # potholes/cracks are small; need the resolution
    world_imgsz: int = 640
    cluster_eps_m: float = 8.0     # detections closer than this merge into one hazard
    min_frames: int = 2            # a hazard must be seen in >= N sampled frames
    max_gps_gap_s: float = 15.0    # beyond this, position is flagged low-quality
    time_offset_s: float = 0.0     # video_t0 = gps_t0 + offset (when no metadata)
    camera: CameraConfig = field(default_factory=CameraConfig)
    rdd_weights: Path = MODELS_DIR / "rdd_yolov8s.pt"
    world_weights: Path = MODELS_DIR / "yolov8s-worldv2.pt"
