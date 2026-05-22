"""
scheduler/api.py — FastAPI health/status HTTP endpoints.

Mounted alongside APScheduler so Railway health checks have an HTTP target.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

_DATA_DIR   = Path(os.environ.get("COMPASS_DATA_DIR", "/data"))
HEALTH_JSON = Path(os.environ.get("HEALTH_JSON_PATH", "/data/health.json"))
CB_JSON     = _DATA_DIR / "circuit_breaker.json"


app = FastAPI(title="vesper", docs_url=None, redoc_url=None)


@app.get("/health")
def health() -> JSONResponse:
    """Railway health check endpoint."""
    return JSONResponse({"status": "ok", "ts": datetime.utcnow().isoformat() + "Z"})


@app.get("/status")
def status() -> JSONResponse:
    """Return health.json and circuit_breaker.json for dashboard consumption."""
    health_data = {}
    cb_data = {}

    if HEALTH_JSON.exists():
        try:
            health_data = json.loads(HEALTH_JSON.read_text())
        except Exception:
            pass

    if CB_JSON.exists():
        try:
            cb_data = json.loads(CB_JSON.read_text())
        except Exception:
            pass

    return JSONResponse({
        "health": health_data,
        "circuit_breaker": cb_data,
        "ts": datetime.utcnow().isoformat() + "Z",
    })
