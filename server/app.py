"""FastAPI backend: upload video+GPS, process it through the live engine, serve results.

    .venv/bin/uvicorn server.app:app --port 8000
"""
import asyncio
import json
import re
import shutil
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from hazardmap.config import RUNS_DIR, ROOT

from .cases import router as cases_router
from .closures import closures
from .live import router as live_router, start_file_session
from .workers import make_cfg, new_run_id, num as _num

app = FastAPI(title="HazardMap")
app.include_router(live_router)
app.include_router(cases_router)
RUNS_DIR.mkdir(exist_ok=True)

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


@app.get("/api/runs/{run_id}/closures")
def run_closures(run_id: str):
    """Road closures + detours for blocking hazards (routing via public OSRM, cached per run)."""
    return closures(_run_dir(run_id))


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
                  camera: str = Form("phone"), auto_calibrate: bool = Form(True),
                  cam_height: str = Form(""), cam_pitch: str = Form(""),
                  hfov: str = Form(""), hood: str = Form(""),
                  road_half_width: float = Form(6.0)):
    run_id = new_run_id(name)
    d = RUNS_DIR / run_id / "input"
    d.mkdir(parents=True)
    vpath = d / ("video" + Path(video.filename or "v.mp4").suffix.lower())
    gpath = d / ("track" + Path(gps.filename or "t.gpx").suffix.lower())
    for up, dst in ((video, vpath), (gps, gpath)):
        with open(dst, "wb") as f:
            shutil.copyfileobj(up.file, f)

    cfg = make_cfg(sample_fps, time_offset, use_world, camera, auto_calibrate,
                   _num(cam_height), _num(cam_pitch), _num(hfov), _num(hood), road_half_width)
    # Same engine as live replay, but unpaced: every frame is analysed as fast as the GPU
    # allows while the dashboard watches the drive unfold.
    lv = start_file_session(vpath, gpath, name or video.filename or run_id, cfg, None,
                            asyncio.get_running_loop())
    return lv.info()


app.mount("/runs", StaticFiles(directory=RUNS_DIR), name="runs")
app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
