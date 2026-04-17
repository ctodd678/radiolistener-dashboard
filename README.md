# Radio Listener Dashboard

A simple web dashboard for managing your [Radio Listener](https://github.com/ctodd678/radiolistener) instances. Runs on the Proxmox host so it can talk to `pct` directly without any extra setup.

## What it does

- Shows all your stations at a glance with live/stopped status and today's detection count
- Start/stop the service on each container from the browser
- View today's detections, the app log, and the raw transcript per station
- Edit `keywords.json` for any station directly in the UI — saves to disk and Radio Listener hot-reloads it within 60 seconds

## Setup

Needs to run on the Proxmox host, not inside a container.

```bash
apt install python3-pip python3-venv -y
mkdir ~/dashboard && cd ~/dashboard
python3 -m venv venv
source venv/bin/activate
pip install fastapi uvicorn
```

Drop in `dashboard.py`, `dashboard_config.json`, and the `dashboard_frontend/` folder.

## Config

Edit `dashboard_config.json` to point at each container. The `path` is the path to the radiolistener directory inside the container's filesystem on the host — run `pct config <vmid>` to find where the rootfs is mounted.

```json
{
  "stations": [
    {
      "name": "CHUM 104.5",
      "vmid": 101,
      "path": "/path/to/ct101/rootfs/root/radiolistener",
      "color": "#e8484a"
    },
    {
      "name": "Virgin Radio",
      "vmid": 102,
      "path": "/path/to/ct102/rootfs/root/radiolistener",
      "color": "#4d9de0"
    }
  ]
}
```

## Run it

```bash
uvicorn dashboard:app --host 0.0.0.0 --port 5000
```

Or set it up as a systemd service so it starts on boot:

```ini
# /etc/systemd/system/rldashboard.service
[Unit]
Description=Radio Listener Dashboard
After=network.target

[Service]
WorkingDirectory=/root/dashboard
ExecStart=/root/dashboard/venv/bin/uvicorn dashboard:app --host 0.0.0.0 --port 5000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable rldashboard
systemctl start rldashboard
```

Then open `http://<your-n100-ip>:5000` in a browser. If you're on Tailscale you can hit it from anywhere.
