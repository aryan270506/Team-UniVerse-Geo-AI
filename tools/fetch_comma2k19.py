"""Download a real dashcam drive (video + the car's own u-blox GPS) from comma2k19 (MIT licence).

    python tools/fetch_comma2k19.py --drive "2018-07-30--13-44-30" --segments 6 7 8 \
        --out data/real/i280_2018-07-30

Each chunk on Hugging Face is a ~9 GB zip; only the needed members are fetched with HTTP Range
requests (~40 MB of video per one-minute segment). Writes <out>.mp4 (H.264, creation_time = UTC
of the first frame) and <out>.gpx (every GPS fix with its satellite UTC time), so the pipeline
aligns them from metadata without any manual offset.
"""
import argparse
import io
import subprocess
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

CHUNK_URL = "https://huggingface.co/datasets/commaai/comma2k19/resolve/main/raw_data/Chunk_{}.zip"
DONGLE = "b0c9d2329ad1606b"
FPS = 20  # comma EON road camera


class HttpFile(io.RawIOBase):
    """Seekable read-only file over HTTP Range requests."""

    def __init__(self, url):
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD")) as r:
            self.url, self.size = r.geturl(), int(r.headers["Content-Length"])
        self.pos = 0

    def seekable(self): return True
    def readable(self): return True
    def tell(self): return self.pos

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else self.pos + off if whence == 1 else self.size + off
        return self.pos

    def readinto(self, b):
        n = min(len(b), self.size - self.pos)
        if n <= 0:
            return 0
        req = urllib.request.Request(self.url, headers={"Range": f"bytes={self.pos}-{self.pos + n - 1}"})
        with urllib.request.urlopen(req) as r:
            data = r.read()
        b[:len(data)] = data
        self.pos += len(data)
        return len(data)


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunk", type=int, default=1)
    ap.add_argument("--drive", required=True, help='e.g. "2018-07-30--13-44-30"')
    ap.add_argument("--segments", type=int, nargs="+", required=True, help="consecutive one-minute segments")
    ap.add_argument("--out", required=True, help="output path without extension")
    args = ap.parse_args()

    z = zipfile.ZipFile(io.BufferedReader(HttpFile(CHUNK_URL.format(args.chunk)), buffer_size=4 << 20))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    npy = lambda name: np.load(io.BytesIO(z.read(name)))

    fixes, frame_times = [], []
    with tempfile.TemporaryDirectory() as tmp:
        parts = []
        for seg in args.segments:
            base = f"Chunk_{args.chunk}/{DONGLE}|{args.drive}/{seg}/"
            print(f"segment {seg}: video + GPS")
            part = Path(tmp) / f"{seg}.hevc"
            with z.open(base + "video.hevc") as src, open(part, "wb") as dst:
                while block := src.read(1 << 20):
                    dst.write(block)
            parts.append(part)
            frame_times.append(npy(base + "global_pose/frame_times"))
            t = npy(base + "processed_log/GNSS/live_gnss_ublox/t")
            v = npy(base + "processed_log/GNSS/live_gnss_ublox/value")  # lat, lon, speed, utc_ms, alt, bearing
            fixes.append(np.column_stack([t, v]))

        frames = np.concatenate(frame_times)
        gps = np.concatenate(fixes)
        gps = gps[np.isfinite(gps[:, 1:3]).all(1) & (gps[:, 4] > 1e12)]
        # Boot clock -> UTC: each fix carries both, so the offset is measured, not assumed.
        offset = float(np.median(gps[:, 4] / 1000 - gps[:, 0]))
        video_start = frames[0] + offset
        gaps = np.diff(frames).max()
        if gaps > 0.5:
            print(f"warning: {gaps:.2f}s gap between frames; segments may not be consecutive")

        # MP4 creation_time only keeps whole seconds: drop the frames before the next full second
        # so the stamp is exact (otherwise every hazard would be placed ~speed x fraction metres off).
        skip = int(round((np.ceil(video_start) - video_start) * FPS))
        video_start += skip / FPS
        frames = frames[skip:]

        concat = Path(tmp) / "list.txt"
        concat.write_text("".join(f"file '{p}'\n" for p in parts))
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-r", str(FPS), "-f", "concat", "-safe", "0", "-i", str(concat),
                        "-vf", f"trim=start_frame={skip},setpts=PTS-STARTPTS", "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", "-metadata", f"creation_time={iso(round(video_start))}",
                        str(out.with_suffix(".mp4"))], check=True)

    utc = np.unique(gps[:, 4])  # duplicate fixes share a UTC stamp
    pts = [gps[gps[:, 4] == u][0] for u in utc]
    trk = "\n".join(f'      <trkpt lat="{p[1]:.7f}" lon="{p[2]:.7f}"><ele>{p[5]:.1f}</ele>'
                    f'<time>{iso(p[4] / 1000)}</time><speed>{p[3]:.2f}</speed><course>{p[6]:.1f}</course></trkpt>'
                    for p in pts)
    out.with_suffix(".gpx").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="comma2k19 u-blox GNSS (via tools/fetch_comma2k19.py)" xmlns="http://www.topografix.com/GPX/1/1">
  <metadata><name>comma2k19 {args.drive} segments {' '.join(map(str, args.segments))}</name>
    <link href="https://github.com/commaai/comma2k19"><text>comma2k19 (MIT licence)</text></link></metadata>
  <trk><name>{args.drive}</name><trkseg>
{trk}
  </trkseg></trk>
</gpx>
""")
    dur = len(frames) / FPS
    print(f"wrote {out.with_suffix('.mp4')} ({dur:.0f}s, starts {iso(video_start)})")
    print(f"wrote {out.with_suffix('.gpx')} ({len(pts)} real fixes, {iso(pts[0][4] / 1000)} -> {iso(pts[-1][4] / 1000)})")


if __name__ == "__main__":
    main()
