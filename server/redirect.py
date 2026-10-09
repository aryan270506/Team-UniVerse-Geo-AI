"""Plain-HTTP front door that forwards everything to the HTTPS server.

Phone mode needs HTTPS (camera + GPS) and live sessions live inside one server process, so
the dashboard must use the same HTTPS server the phone talks to:

    .venv/bin/uvicorn server.redirect:app --port 8000      # http://localhost:8000 -> https://localhost:8443
"""
import os

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

HTTPS_PORT = int(os.environ.get("HAZARDMAP_HTTPS_PORT", "8443"))
app = FastAPI(title="HazardMap redirect")


@app.api_route("/{path:path}", methods=["GET", "HEAD"])
def to_https(path: str, request: Request):
    host = request.url.hostname or "localhost"
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(f"https://{host}:{HTTPS_PORT}/{path}{query}", status_code=307)
