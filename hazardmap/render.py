"""Render the 'AI view' of a drive: the original video at full frame rate with every reported
hazard drawn on it, scene hazards (flooding, landslide) tinted over the road, and a HUD.
Paired with timeline.json (video time -> GPS pose) so the dashboard can play the video in
sync with the map.
"""
import math
import subprocess
from bisect import bisect_right
from typing import Callable

import cv2
import numpy as np

from .detect import Detection
from .ingest import Pose

SEV_BGR = {"high": (77, 72, 229), "medium": (36, 165, 245), "low": (88, 167, 70)}
SCENE_TINT = {"flooding": (230, 150, 40), "landslide": (40, 90, 160)}     # BGR: blue water, brown earth
LABEL = {"pothole": "POTHOLE", "crack": "CRACK", "alligator_crack": "ALLIGATOR CRACK", "flooding": "FLOODING",
         "fallen_tree": "FALLEN TREE", "power_line": "POWER LINE DOWN", "debris": "DEBRIS",
         "landslide": "LANDSLIDE", "blockage": "BLOCKAGE"}
FONT = cv2.FONT_HERSHEY_DUPLEX


def timeline(pose_fn: Callable[[float], Pose], t0: float, duration: float, hz: float = 4.0) -> list:
    out = []
    for k in range(int(duration * hz) + 1):
        vt = k / hz
        p = pose_fn(t0 + vt)
        if p.valid:
            out.append([round(vt, 2), round(p.lat, 7), round(p.lon, 7),
                        None if math.isnan(p.heading) else round(p.heading, 1), round(p.speed, 2)])
    return out


def _chip(img, text, x, y, color, scale=0.55):
    (tw, th), base = cv2.getTextSize(text, FONT, scale, 1)
    y = max(y, th + 8)
    cv2.rectangle(img, (x, y - th - 8), (x + tw + 10, y + base - 2), color, -1)
    cv2.putText(img, text, (x + 5, y - 4), FONT, scale, (255, 255, 255), 1, cv2.LINE_AA)


def _draw(img, d: Detection, sev: str, scale: float):
    color = SEV_BGR.get(sev, (200, 200, 200))
    x1, y1, x2, y2 = (int(v * scale) for v in d.box)
    if d.source == "scene":
        overlay = img.copy()
        cv2.rectangle(overlay, (x1, y1), (x2, y2), SCENE_TINT.get(d.category, color), -1)
        cv2.addWeighted(overlay, 0.28, img, 0.72, 0, img)
        cv2.rectangle(img, (x1 + 2, y1), (x2 - 2, y2), color, 2)
        _chip(img, f"{LABEL.get(d.category, d.category)} {d.conf:.0%}", x1 + 8, y1 + 30, color, 0.75)
        return
    cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
    l = max(8, min(x2 - x1, y2 - y1) // 4)                     # corner accents
    for cx, cy, dx, dy in ((x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1)):
        cv2.line(img, (cx, cy), (cx + dx * l, cy), color, 4)
        cv2.line(img, (cx, cy), (cx, cy + dy * l), color, 4)
    _chip(img, f"{LABEL.get(d.category, d.category)} {d.conf:.0%}", x1, y1 - 4, color)


def _hud(img, vt: float, pose: Pose | None, found: int, high: int):
    h, w = img.shape[:2]
    bar = img.copy()
    cv2.rectangle(bar, (0, h - 34), (w, h), (16, 13, 11), -1)
    cv2.addWeighted(bar, 0.72, img, 0.28, 0, img)
    cv2.circle(img, (16, h - 17), 6, (77, 72, 229), -1)
    parts = [f"AI SCAN  {int(vt // 60):02d}:{vt % 60:04.1f}"]
    if pose is not None and pose.valid:
        parts.append(f"{pose.speed * 3.6:4.0f} km/h")
        parts.append(f"{pose.lat:.5f}, {pose.lon:.5f}")
    parts.append(f"hazards {found}" + (f"  ({high} high)" if high else ""))
    cv2.putText(img, "   |   ".join(parts), (30, h - 11), FONT, 0.48, (235, 235, 235), 1, cv2.LINE_AA)


def render_annotated(video: str, out_path: str, shown: list[tuple[Detection, str]], first_seen: list[tuple[float, str]],
                     pose_fn: Callable[[float], Pose], t0: float, sample_fps: float, max_w: int = 960) -> bool:
    """shown: (detection, severity) for every detection that belongs to a reported hazard.
    first_seen: (video_time, severity) of each hazard, for the running HUD counter."""
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        return False
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    out_fps = min(fps, 30.0)
    step = max(1, round(fps / out_fps))
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    scale = min(1.0, max_w / W)
    w, h = int(W * scale) // 2 * 2, int(H * scale) // 2 * 2

    by_frame: dict[int, list] = {}
    for d, sev in shown:
        by_frame.setdefault(d.frame_idx, []).append((d, sev))
    keys = sorted(by_frame)
    hold = int(round(fps / sample_fps * 1.5))                  # keep boxes up between analysed frames
    first = sorted(first_seen)
    first_t = [t for t, _ in first]

    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
                             "-r", f"{out_fps / 1:.3f}", "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "25",
                             "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path], stdin=subprocess.PIPE)
    idx = 0
    try:
        while True:
            ok = cap.grab()
            if not ok:
                break
            if idx % step:
                idx += 1
                continue
            ok, frame = cap.retrieve()
            if not ok:
                break
            img = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA) if (w, h) != (W, H) else frame.copy()
            k = bisect_right(keys, idx) - 1
            if k >= 0 and idx - keys[k] <= hold:
                for d, sev in sorted(by_frame[keys[k]], key=lambda x: x[0].source != "scene"):
                    _draw(img, d, sev, w / W)
            vt = idx / fps
            n = bisect_right(first_t, vt)
            _hud(img, vt, pose_fn(t0 + vt), n, sum(1 for _, s in first[:n] if s == "high"))
            proc.stdin.write(img.tobytes())
            idx += 1
    finally:
        cap.release()
        proc.stdin.close()
        proc.wait()
    return proc.returncode == 0
