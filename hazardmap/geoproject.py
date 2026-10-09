"""Project an image pixel on the road surface to a latitude/longitude.

Flat-ground pinhole model: the camera sits `height_m` above the road and is tilted
down by `pitch_deg`. A ray through pixel (u, v) is intersected with the ground plane,
giving a forward and lateral offset in metres relative to the vehicle. That offset is
rotated by the vehicle heading and applied with a geodesic forward step.
"""
import math

from .config import CameraConfig
from .ingest import GEOD, Pose


def pixel_to_ground(u: float, v: float, width: int, height: int,
                    cam: CameraConfig) -> tuple[float, float] | None:
    """Return (forward_m, lateral_m) on the road plane, or None above the horizon."""
    fx = (width / 2) / math.tan(math.radians(cam.hfov_deg) / 2)
    fy = fx                                   # square pixels
    xc = (u - width / 2) / fx
    yc = (v - height / 2) / fy
    p = math.radians(cam.pitch_deg)
    # Camera ray (x right, y down, z forward) rotated into a level frame.
    down = yc * math.cos(p) + math.sin(p)
    fwd = -yc * math.sin(p) + math.cos(p)
    if down <= 1e-3:
        return None                          # ray never meets the road
    s = cam.height_m / down
    return fwd * s, xc * s


def project(u: float, v: float, width: int, height: int, pose: Pose,
            cam: CameraConfig) -> dict | None:
    """Pixel (bottom-centre of a detection) -> geolocated point with range/bearing."""
    if not pose.valid:
        return None
    ground = pixel_to_ground(u, v, width, height, cam)
    if ground is None:
        return None
    fwd, lat_off = ground
    dist = math.hypot(fwd, lat_off)
    if dist > cam.max_dist_m:
        return None          # too far for a flat-ground estimate; it'll be seen closer later
    dist = max(dist, cam.min_dist_m)
    bearing = (pose.heading + math.degrees(math.atan2(lat_off, fwd))) % 360
    lon, lat, _ = GEOD.fwd(pose.lon, pose.lat, bearing, dist)
    return {"lat": lat, "lon": lon, "range_m": dist, "bearing": bearing, "lateral_m": lat_off}
