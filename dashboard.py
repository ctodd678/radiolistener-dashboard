"""
Radio Listener Dashboard — FastAPI backend
Run with: uvicorn dashboard:app --host 0.0.0.0 --port 5000

Reads data from each station's radiolistener directory and exposes it via REST.
Also proxies start/stop commands to pct (Proxmox container tool).

Config: dashboard_config.json in the same directory as this file.
"""

import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="Radio Listener Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- load dashboard config ---
DASHBOARD_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "dashboard_config.json")

def load_dashboard_config():
    if not os.path.exists(DASHBOARD_CONFIG_PATH):
        # write a default config if none exists
        default = {
            "stations": [
                {
                    "name": "CHUM 104.5",
                    "vmid": 101,
                    "path": "/root/radiolistener",
                    "color": "#e8484a"
                }
            ]
        }
        with open(DASHBOARD_CONFIG_PATH, "w") as f:
            json.dump(default, f, indent=2)
        return default
    with open(DASHBOARD_CONFIG_PATH, "r") as f:
        return json.load(f)

# --- helpers ---

def read_file_tail(path, lines=200):
    """read last N lines of a file without loading the whole thing"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
            return "".join(all_lines[-lines:])
    except Exception:
        return ""

def read_json_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def get_container_status(vmid):
    """query proxmox pct for container status — returns 'running', 'stopped', or 'unknown'"""
    try:
        result = subprocess.run(
            ["pct", "status", str(vmid)],
            capture_output=True, text=True, timeout=5
        )
        output = result.stdout.strip().lower()
        if "running" in output:
            return "running"
        elif "stopped" in output:
            return "stopped"
        return "unknown"
    except Exception:
        return "unknown"

def is_service_active(vmid):
    """check if radioscout.service is active inside the container"""
    try:
        result = subprocess.run(
            ["pct", "exec", str(vmid), "--", "systemctl", "is-active", "radioscout.service"],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() == "active"
    except Exception:
        return False

# --- routes ---

@app.get("/api/stations")
def get_stations():
    config = load_dashboard_config()
    stations = []

    for s in config["stations"]:
        vmid   = s["vmid"]
        path   = s["path"]
        today  = datetime.now().strftime("%Y-%m-%d")

        # container + service status
        ct_status      = get_container_status(vmid)
        service_active = is_service_active(vmid) if ct_status == "running" else False

        # load batch detections
        batch_path   = os.path.join(path, "batch_detections.json")
        detections   = read_json_file(batch_path) or []
        today_dets   = [d for d in detections if d["timestamp"].startswith(today)]

        # count detections per hour for the sparkline
        hour_counts = {}
        for d in today_dets:
            try:
                h = datetime.strptime(d["timestamp"], "%Y-%m-%d %H:%M:%S").hour
                hour_counts[h] = hour_counts.get(h, 0) + 1
            except Exception:
                pass

        # last detection
        last_detection = today_dets[-1]["timestamp"] if today_dets else None

        # read last few lines of app log for status
        app_log_path = os.path.join(path, "radio_listener.log")
        log_tail     = read_file_tail(app_log_path, lines=5)

        stations.append({
            "name":            s["name"],
            "vmid":            vmid,
            "color":           s.get("color", "#ffffff"),
            "container_status": ct_status,
            "service_active":  service_active,
            "detections_today": len(today_dets),
            "last_detection":  last_detection,
            "hour_counts":     hour_counts,
            "log_tail":        log_tail,
        })

    return stations

@app.get("/api/station/{vmid}/detections")
def get_detections(vmid: int):
    config = load_dashboard_config()
    station = next((s for s in config["stations"] if s["vmid"] == vmid), None)
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")

    today      = datetime.now().strftime("%Y-%m-%d")
    batch_path = os.path.join(station["path"], "batch_detections.json")
    detections = read_json_file(batch_path) or []
    return [d for d in detections if d["timestamp"].startswith(today)]

@app.get("/api/station/{vmid}/log")
def get_log(vmid: int, lines: int = 200):
    config = load_dashboard_config()
    station = next((s for s in config["stations"] if s["vmid"] == vmid), None)
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")

    log_path = os.path.join(station["path"], "radio_listener.log")
    return {"log": read_file_tail(log_path, lines=lines)}

@app.get("/api/station/{vmid}/transcript")
def get_transcript(vmid: int, lines: int = 100):
    config = load_dashboard_config()
    station = next((s for s in config["stations"] if s["vmid"] == vmid), None)
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")

    transcript_path = os.path.join(station["path"], "radio_transcript.txt")
    return {"transcript": read_file_tail(transcript_path, lines=lines)}

@app.get("/api/station/{vmid}/keywords")
def get_keywords(vmid: int):
    config = load_dashboard_config()
    station = next((s for s in config["stations"] if s["vmid"] == vmid), None)
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")

    kw_path = os.path.join(station["path"], "keywords.json")
    data = read_json_file(kw_path)
    if not data:
        raise HTTPException(status_code=404, detail="keywords.json not found")
    return data

class KeywordsUpdate(BaseModel):
    data: dict

@app.post("/api/station/{vmid}/keywords")
def update_keywords(vmid: int, body: KeywordsUpdate):
    config = load_dashboard_config()
    station = next((s for s in config["stations"] if s["vmid"] == vmid), None)
    if not station:
        raise HTTPException(status_code=404, detail="Station not found")

    kw_path = os.path.join(station["path"], "keywords.json")
    try:
        with open(kw_path, "w", encoding="utf-8") as f:
            json.dump(body.data, f, indent=2)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/station/{vmid}/start")
def start_station(vmid: int):
    try:
        subprocess.run(["pct", "start", str(vmid)], timeout=15, check=True)
        time.sleep(2)
        subprocess.run(
            ["pct", "exec", str(vmid), "--", "systemctl", "start", "radioscout.service"],
            timeout=10, check=True
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

# serve the frontend
FRONTEND_PATH = os.path.join(os.path.dirname(__file__), "dashboard_frontend")
if os.path.exists(FRONTEND_PATH):
    app.mount("/", StaticFiles(directory=FRONTEND_PATH, html=True), name="frontend")
