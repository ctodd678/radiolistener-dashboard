"""
Radio Listener Dashboard — FastAPI backend
Runs on the Proxmox host. Talks to the rlapi service running inside each container.

Run with: uvicorn dashboard:app --host 0.0.0.0 --port 5000
Or let systemd handle it via rldashboard.service.
"""

import json
import os
import subprocess
import time
from datetime import datetime

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="Radio Listener Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DASHBOARD_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "dashboard_config.json")
API_TIMEOUT = 5  # seconds


def load_config():
    if not os.path.exists(DASHBOARD_CONFIG_PATH):
        default = {
            "stations": [
                {
                    "name": "CHUM 104.5",
                    "vmid": 105,
                    "api_url": "http://10.0.0.105:5001",
                    "color": "#e8484a"
                }
            ]
        }
        with open(DASHBOARD_CONFIG_PATH, "w") as f:
            json.dump(default, f, indent=2)
        return default
    with open(DASHBOARD_CONFIG_PATH, "r") as f:
        return json.load(f)


def get_container_status(vmid):
    try:
        result = subprocess.run(
            ["pct", "status", str(vmid)],
            capture_output=True, text=True, timeout=5
        )
        out = result.stdout.strip().lower()
        if "running" in out:
            return "running"
        elif "stopped" in out:
            return "stopped"
        return "unknown"
    except Exception:
        return "unknown"


async def api_get(url, path, **kwargs):
    """GET from a container API endpoint, returns None on failure"""
    try:
        async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
            r = await client.get(f"{url}{path}", **kwargs)
            r.raise_for_status()
            return r.json()
    except Exception:
        return None


async def api_post(url, path, json_body):
    try:
        async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
            r = await client.post(f"{url}{path}", json=json_body)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- routes ---

@app.get("/api/stations")
async def get_stations():
    config = load_config()
    stations = []

    for s in config["stations"]:
        vmid    = s["vmid"]
        api_url = s["api_url"]
        color   = s.get("color", "#ffffff")

        ct_status = get_container_status(vmid)

        # hit the container API for status
        status_data = await api_get(api_url, "/status")
        detections  = await api_get(api_url, "/detections") or []

        # build hour counts for sparkline
        hour_counts = {}
        for d in detections:
            try:
                h = datetime.strptime(d["timestamp"], "%Y-%m-%d %H:%M:%S").hour
                hour_counts[h] = hour_counts.get(h, 0) + 1
            except Exception:
                pass

        # last few log lines for the card
        log_data = await api_get(api_url, "/log", params={"lines": 5})

        stations.append({
            "name":             s["name"],
            "vmid":             vmid,
            "color":            color,
            "api_url":          api_url,
            "container_status": ct_status,
            "api_reachable":    status_data is not None,
            "app_running":      status_data.get("app_running", False) if status_data else False,
            "detections_today": status_data.get("detections_today", 0) if status_data else 0,
            "last_detection":   status_data.get("last_detection") if status_data else None,
            "hour_counts":      hour_counts,
            "log_tail":         log_data.get("log", "") if log_data else "",
        })

    return stations


@app.get("/api/station/{vmid}/detections")
async def get_detections(vmid: int):
    config = load_config()
    s = next((x for x in config["stations"] if x["vmid"] == vmid), None)
    if not s:
        raise HTTPException(status_code=404, detail="Station not found")
    data = await api_get(s["api_url"], "/detections")
    return data or []


@app.get("/api/station/{vmid}/log")
async def get_log(vmid: int, lines: int = 300):
    config = load_config()
    s = next((x for x in config["stations"] if x["vmid"] == vmid), None)
    if not s:
        raise HTTPException(status_code=404, detail="Station not found")
    data = await api_get(s["api_url"], "/log", params={"lines": lines})
    return data or {"log": ""}


@app.get("/api/station/{vmid}/transcript")
async def get_transcript(vmid: int, lines: int = 150):
    config = load_config()
    s = next((x for x in config["stations"] if x["vmid"] == vmid), None)
    if not s:
        raise HTTPException(status_code=404, detail="Station not found")
    data = await api_get(s["api_url"], "/transcript", params={"lines": lines})
    return data or {"transcript": ""}


@app.get("/api/station/{vmid}/keywords")
async def get_keywords(vmid: int):
    config = load_config()
    s = next((x for x in config["stations"] if x["vmid"] == vmid), None)
    if not s:
        raise HTTPException(status_code=404, detail="Station not found")
    data = await api_get(s["api_url"], "/keywords")
    if data is None:
        raise HTTPException(status_code=404, detail="Could not reach container API")
    return data


class KeywordsUpdate(BaseModel):
    data: dict


@app.post("/api/station/{vmid}/keywords")
async def update_keywords(vmid: int, body: KeywordsUpdate):
    config = load_config()
    s = next((x for x in config["stations"] if x["vmid"] == vmid), None)
    if not s:
        raise HTTPException(status_code=404, detail="Station not found")
    return await api_post(s["api_url"], "/keywords", {"data": body.data})


@app.post("/api/station/{vmid}/start")
def start_station(vmid: int):
    try:
        subprocess.run(["pct", "start", str(vmid)], timeout=15, check=True)
        time.sleep(2)
        subprocess.run(
            ["pct", "exec", str(vmid), "--", "systemctl", "start", "radioscout.service"],
            timeout=10
        )
        subprocess.run(
            ["pct", "exec", str(vmid), "--", "systemctl", "start", "rlapi.service"],
            timeout=10
        )
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/station/{vmid}/stop")
def stop_station(vmid: int):
    try:
        subprocess.run(
            ["pct", "exec", str(vmid), "--", "systemctl", "stop", "radioscout.service"],
            timeout=10
        )
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# serve frontend
FRONTEND_PATH = os.path.join(os.path.dirname(__file__), "dashboard_frontend")
if os.path.exists(FRONTEND_PATH):
    app.mount("/", StaticFiles(directory=FRONTEND_PATH, html=True), name="frontend")