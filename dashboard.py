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
API_TIMEOUT = 5


def load_config():
    if not os.path.exists(DASHBOARD_CONFIG_PATH):
        default = {"stations": []}
        with open(DASHBOARD_CONFIG_PATH, "w") as f:
            json.dump(default, f, indent=2)
        return default
    with open(DASHBOARD_CONFIG_PATH, "r") as f:
        return json.load(f)


def save_config(data):
    with open(DASHBOARD_CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=2)


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


def find_station(vmid):
    config = load_config()
    s = next((x for x in config["stations"] if x["vmid"] == vmid), None)
    if not s:
        raise HTTPException(status_code=404, detail="Station not found")
    return s


# --- stations ---

@app.get("/api/stations")
async def get_stations():
    config = load_config()
    stations = []

    for s in config["stations"]:
        vmid    = s["vmid"]
        api_url = s["api_url"]

        ct_status   = get_container_status(vmid)
        status_data = await api_get(api_url, "/status")
        detections  = await api_get(api_url, "/detections") or []

        hour_counts = {}
        for d in detections:
            try:
                h = datetime.strptime(d["timestamp"], "%Y-%m-%d %H:%M:%S").hour
                hour_counts[h] = hour_counts.get(h, 0) + 1
            except Exception:
                pass

        stations.append({
            "name":               s["name"],
            "vmid":               vmid,
            "color":              s.get("color", "#ffffff"),
            "api_url":            api_url,
            "container_status":   ct_status,
            "api_reachable":      status_data is not None,
            "radioscout_running": status_data.get("radioscout_running", False) if status_data else False,
            "rlapi_running":      status_data.get("rlapi_running", True) if status_data else False,
            "detections_today":   status_data.get("detections_today", 0) if status_data else 0,
            "last_detection":     status_data.get("last_detection") if status_data else None,
            "hour_counts":        hour_counts,
        })

    return stations


@app.get("/api/station/{vmid}/detections")
async def get_detections(vmid: int):
    s = find_station(vmid)
    return await api_get(s["api_url"], "/detections") or []


@app.get("/api/station/{vmid}/log")
async def get_log(vmid: int, lines: int = 1000):
    s = find_station(vmid)
    return await api_get(s["api_url"], "/log", params={"lines": lines}) or {"log": ""}


@app.get("/api/station/{vmid}/transcript")
async def get_transcript(vmid: int, lines: int = 150):
    s = find_station(vmid)
    return await api_get(s["api_url"], "/transcript", params={"lines": lines}) or {"transcript": ""}


@app.get("/api/station/{vmid}/keywords")
async def get_keywords(vmid: int):
    s = find_station(vmid)
    data = await api_get(s["api_url"], "/keywords")
    if data is None:
        raise HTTPException(status_code=404, detail="Could not reach container API")
    return data


class KeywordsUpdate(BaseModel):
    data: dict


@app.post("/api/station/{vmid}/keywords")
async def update_keywords(vmid: int, body: KeywordsUpdate):
    s = find_station(vmid)
    return await api_post(s["api_url"], "/keywords", {"data": body.data})


@app.get("/api/station/{vmid}/config")
async def get_station_config(vmid: int):
    s = find_station(vmid)
    data = await api_get(s["api_url"], "/config")
    if data is None:
        raise HTTPException(status_code=404, detail="Could not reach container API")
    return data


class StationConfigUpdate(BaseModel):
    data: dict


@app.post("/api/station/{vmid}/config")
async def update_station_config(vmid: int, body: StationConfigUpdate):
    s = find_station(vmid)
    return await api_post(s["api_url"], "/config", {"data": body.data})


@app.get("/api/station/{vmid}/schedule")
async def get_schedule(vmid: int):
    s = find_station(vmid)
    data = await api_get(s["api_url"], "/schedule")
    return data or {"slots": [], "updated_at": None, "summary": None}


class ScheduleUpdate(BaseModel):
    data: dict


@app.post("/api/station/{vmid}/schedule")
async def update_schedule(vmid: int, body: ScheduleUpdate):
    s = find_station(vmid)
    return await api_post(s["api_url"], "/schedule", {"data": body.data})


class TestPayload(BaseModel):
    text: str
    keywords: dict


@app.post("/api/station/{vmid}/test")
async def test_keywords(vmid: int, body: TestPayload):
    s = find_station(vmid)
    return await api_post(s["api_url"], "/test", {"text": body.text, "keywords": body.keywords})


# --- dashboard config ---

@app.get("/api/dashboard/config")
def get_dashboard_config():
    return load_config()


class DashboardConfigUpdate(BaseModel):
    data: dict


@app.post("/api/dashboard/config")
def update_dashboard_config(body: DashboardConfigUpdate):
    try:
        save_config(body.data)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- start / stop ---

@app.post("/api/station/{vmid}/start")
def start_station(vmid: int):
    try:
        # try to start the container — ignore error if it's already running
        result = subprocess.run(
            ["pct", "start", str(vmid)],
            timeout=15, capture_output=True, text=True
        )
        if result.returncode != 0 and "already running" not in result.stderr.lower():
            # only raise if it's not an "already running" error
            raise Exception(result.stderr.strip())
        time.sleep(2)
        subprocess.run(["pct", "exec", str(vmid), "--", "systemctl", "start", "radioscout.service"], timeout=10)
        subprocess.run(["pct", "exec", str(vmid), "--", "systemctl", "start", "rlapi.service"], timeout=10)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/station/{vmid}/stop")
def stop_station(vmid: int):
    try:
        subprocess.run(["pct", "exec", str(vmid), "--", "systemctl", "stop", "radioscout.service"], timeout=10)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/station/{vmid}/restart-service")
def restart_service(vmid: int):
    try:
        subprocess.run(["pct", "exec", str(vmid), "--", "systemctl", "restart", "radioscout.service"], timeout=10)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



# --- archive ---

@app.get("/api/station/{vmid}/archive/list")
async def get_archive_list(vmid: int):
    s = find_station(vmid)
    return await api_get(s["api_url"], "/archive/list") or []


@app.get("/api/station/{vmid}/archive/{date}/transcript")
async def get_archive_transcript(vmid: int, date: str):
    s = find_station(vmid)
    data = await api_get(s["api_url"], f"/archive/{date}/transcript")
    if data is None:
        raise HTTPException(status_code=404, detail="Not found")
    return data


@app.get("/api/station/{vmid}/archive/{date}/log")
async def get_archive_log(vmid: int, date: str):
    s = find_station(vmid)
    data = await api_get(s["api_url"], f"/archive/{date}/log")
    if data is None:
        raise HTTPException(status_code=404, detail="Not found")
    return data


@app.get("/api/station/{vmid}/archive/{date}/detections")
async def get_archive_detections(vmid: int, date: str):
    s = find_station(vmid)
    return await api_get(s["api_url"], f"/archive/{date}/detections") or []


@app.get("/api/station/{vmid}/archive/{date}/schedule")
async def get_archive_schedule(vmid: int, date: str):
    s = find_station(vmid)
    data = await api_get(s["api_url"], f"/archive/{date}/schedule")
    if data is None:
        raise HTTPException(status_code=404, detail="Not found")
    return data

# --- virgin radio auto-submitter ---

class VirginSubmitRequest(BaseModel):
    keyword: str | None = None
    force: bool = False
    date: str | None = None


@app.post("/api/station/{vmid}/virgin/submit")
async def virgin_submit(vmid: int, body: VirginSubmitRequest = VirginSubmitRequest()):
    s = find_station(vmid)
    async with httpx.AsyncClient(timeout=130) as client:
        r = await client.post(f"{s['api_url']}/virgin/submit", json=body.model_dump())
        if not r.is_success:
            raise HTTPException(status_code=r.status_code, detail=r.json().get("detail", r.text))
        return r.json()


class VirginSubmissionsUpdate(BaseModel):
    data: dict


@app.post("/api/station/{vmid}/virgin/submissions")
async def update_virgin_submissions(vmid: int, body: VirginSubmissionsUpdate):
    s = find_station(vmid)
    return await api_post(s["api_url"], "/virgin/submissions", {"data": body.data})


@app.get("/api/station/{vmid}/virgin/status")
async def virgin_status(vmid: int):
    s = find_station(vmid)
    return await api_get(s["api_url"], "/virgin/status") or {}


# serve frontend
FRONTEND_PATH = os.path.join(os.path.dirname(__file__), "dashboard_frontend")
if os.path.exists(FRONTEND_PATH):
    app.mount("/", StaticFiles(directory=FRONTEND_PATH, html=True), name="frontend")