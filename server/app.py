"""FastAPI backend: upload video+GPS, run the pipeline in the background, serve results.

    .venv/bin/uvicorn server.app:app --port 8000
"""
import json
import re
import shutil
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from hazardmap.config import RUNS_DIR, PipelineConfig, ROOT
from hazardmap.pipeline import run as run_pipeline

app = FastAPI(title="HazardMap")
RUNS_DIR.mkdir(exist_ok=True)
# One worker: the GPU (MPS) and the stateful trackers are not shared safely.
executor = ThreadPoolExecutor(max_workers=1)
jobs: dict[str, dict] = {}

_ID = re.compile(r"^[\w\-]+$")


def _run_dir(run_id: str) -> Path:
    if not _ID.match(run_id):
        raise HTTPException(400, "bad run id")
    d = RUNS_DIR / run_id
    if not (d / "summary.json").exists():
        raise HTTPException(404, "run not found")
    return d


@app.get("/api/runs")
def list_runs():
    out = []
    for d in sorted(RUNS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        s = d / "summary.json"
        if s.exists():
            summary = json.loads(s.read_text())
            summary.pop("config", None)
            out.append({"id": d.name, **summary})
    return out


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    d = _run_dir(run_id)
    return {
        "id": run_id,
        "summary": json.loads((d / "summary.json").read_text()),
        "hazards": json.loads((d / "hazards.geojson").read_text()),
        "trajectory": json.loads((d / "trajectory.geojson").read_text()),
    }


@app.get("/api/runs/{run_id}/download/{name}")
def download(run_id: str, name: str):
    if name not in {"hazards.geojson", "trajectory.geojson", "detections.csv", "summary.json"}:
        raise HTTPException(404)
    d = _run_dir(run_id)
    return FileResponse(d / name, filename=f"{run_id}_{name}")


@app.post("/api/process")
async def process(video: UploadFile = File(...), gps: UploadFile = File(...),
                  name: str = Form(""), sample_fps: float = Form(5.0),
                  time_offset: float = Form(0.0), use_world: bool = Form(True),
                  cam_height: float = Form(1.3), cam_pitch: float = Form(6.0),
                  hfov: float = Form(70.0)):
    slug = re.sub(r"[^\w\-]+", "-", name.strip().lower()).strip("-") or "drive"
    run_id = f"{datetime.now():%Y%m%d-%H%M%S}-{slug}"[:60]
    d = RUNS_DIR / run_id / "input"
    d.mkdir(parents=True)
    vpath = d / ("video" + Path(video.filename or "v.mp4").suffix.lower())
    gpath = d / ("track" + Path(gps.filename or "t.gpx").suffix.lower())
    for up, dst in ((video, vpath), (gps, gpath)):
        with open(dst, "wb") as f:
            shutil.copyfileobj(up.file, f)

    cfg = PipelineConfig(sample_fps=sample_fps, time_offset_s=time_offset, use_world=use_world)
    cfg.camera.height_m, cfg.camera.pitch_deg, cfg.camera.hfov_deg = cam_height, cam_pitch, hfov
    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {"id": job_id, "run_id": run_id, "state": "queued", "stage": "queued",
                    "progress": 0.0, "error": None}

    def work():
        job = jobs[job_id]
        job["state"] = "running"

        def progress(stage, frac):
            job["stage"], job["progress"] = stage, round(frac, 3)
        try:
            run_pipeline(str(vpath), str(gpath), RUNS_DIR / run_id, cfg, progress=progress)
            job["state"] = "done"
        except Exception as e:  # surface pipeline errors to the UI
            traceback.print_exc()
            job["state"], job["error"] = "error", str(e)

    executor.submit(work)
    return jobs[job_id]


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404)
    return jobs[job_id]


app.mount("/runs", StaticFiles(directory=RUNS_DIR), name="runs")
app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
