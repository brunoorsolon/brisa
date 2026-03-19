# Fan Control Service — Architecture Document

**Status:** v0.1.0 — implemented and running
**Last Updated:** March 17, 2026

---

## Problem Statement

TrueNAS SCALE locks down package management, making it impossible to install fan control software via standard system tools (`apt`, `pip`). Existing solutions like CoolerControl assume a general-purpose Linux environment. USB fan controllers with good Linux support rely on `liquidctl` to operate — which cannot be installed on TrueNAS natively.

This project solves exactly that problem: a self-contained Docker-based fan control service for TrueNAS SCALE, targeting any fan controller supported by liquidctl and any temperature source exposed via hwmon. It is not a general-purpose fan control solution.

---

## Scope

**In scope:**
- Any USB fan controller supported by liquidctl (Aquacomputer Quadro is the primary tested device)
- TrueNAS SCALE as the primary target platform
- Any Linux host where Docker runs with USB access
- Temperature sources: any sensor exposed via `/sys/class/hwmon` (coretemp, drivetemp, nvme, etc.)
- Fan control: all output channels on any liquidctl-supported device

**Out of scope:**
- Controllers not supported by liquidctl
- General Linux fan control (CoolerControl, fancontrol, etc. solve that problem better on standard distros)
- PWM headers directly on motherboard
- Non-Docker deployments

---

## Name

**Brisa** — generic, evokes airflow, not tied to any specific hardware or platform.

---

## Architecture Overview

Single Docker container. Three logical components running together:

```
┌─────────────────────────────────────────────────┐
│                  Docker Container               │
│                                                 │
│  ┌─────────────┐   ┌──────────────────────────┐ │
│  │  Controller │   │      FastAPI Server      │ │
│  │    Loop     │   │  (Web UI + REST API)     │ │
│  │             │   │                          │ │
│  │  reads temps│   │  /          → Web UI     │ │
│  │  applies    │   │  /api/state → current    │ │
│  │  curves     │   │  /api/history → SQLite   │ │
│  │  calls      │   │  /api/config → R/W JSON  │ │
│  │  liquidctl  │   │  /api/apply → force loop │ │
│  └──────┬──────┘   │  /api/metrics → Prom.    │ │
│         │          └──────────────────────────┘ │
│         │                                       │
│  ┌──────▼──────────────────────────────────────┐│
│  │              SQLite Database                ││
│  │  (temp history, fan speed history)          ││
│  └─────────────────────────────────────────────┘│
│                                                 │
│  Volumes:                                       │
│    /data/config.json  ← curves, fan assignments │
│    /data/history.db   ← SQLite                  │
│  Devices:                                       │
│    /dev/bus/usb (privileged)                    │
│    /sys/class/hwmon (read-only)                 │
└─────────────────────────────────────────────────┘
```

The controller loop runs as an asyncio background task inside the Uvicorn process — one process, no supervisor.

---

## Technology Stack

| Component | Choice | Reason |
|-----------|--------|--------|
| Language | Python 3.12.9 | Runs liquidctl as subprocess, reads /sys natively, serves web, handles JSON/SQLite — one language for everything |
| Web framework | FastAPI 0.115.12 | Lightweight, async, automatic OpenAPI/Swagger docs at `/docs` for free, static file serving built in |
| ASGI server | Uvicorn 0.34.0 | Standard FastAPI deployment, minimal overhead |
| Data validation | Pydantic 2.11.1 | Config validation and serialization |
| Database | SQLite (stdlib) | No separate service, file-based, survives container restarts, more than adequate for time-series at minute intervals |
| Fan control | liquidctl 1.13.0 (subprocess) | Only reliable way to control USB fan controllers on Linux; subprocess is intentional — no stable Python API exists |
| Frontend | Vanilla JS + Chart.js 4.4.0 | No framework needed for this scope; Chart.js handles all graphing; keeps image small |
| Base image | python:3.12.9-slim | Minimal Debian base; pinned to exact patch version |

**Why not a compiled language (Go, Rust)?** liquidctl is Python-only. Wrapping it from another language adds complexity with no benefit.

**Why not Node/Bun for the backend?** Reading `/sys/class/hwmon` and shelling out to liquidctl is more natural in Python. Avoiding two runtimes in the image.

**Why subprocess for liquidctl?** No stable Python API exists for liquidctl. Subprocess is explicit and predictable. The `--direct-access` flag is passed on all control commands to suppress kernel driver fallback warnings on the Aquacomputer Quadro.

---

## Data Model

### config.json

```json
{
  "settings": {
    "interval_seconds": 60,
    "history_days": 30,
    "safety_floor_percent": 30
  },
  "curves": [
    {
      "name": "silent",
      "points": [
        {"temp": 30, "percent": 20},
        {"temp": 50, "percent": 50},
        {"temp": 70, "percent": 100}
      ]
    }
  ],
  "fan_configs": [
    {
      "fan_id": "fan1",
      "fan_label": "Upper rear left",
      "curve_name": "silent",
      "sensor_id": "drivetemp-wwid-naa.5000000000000001/sda — WDC WD120XXXX",
      "override_percent": null
    }
  ],
  "sensor_aliases": {
    "nvme-hwmon1/Sensor 1": "NVMe Boot Drive",
    "drivetemp-hwmon5/sda — WDC WD120XXXX": "NAS Drive 1"
  }
}
```

**`override_percent`** — when set to an integer, the controller applies that fixed speed to the fan every loop iteration, bypassing the sensor read, curve interpolation, and safety floor entirely. The curve and sensor assignments are preserved so they can be restored by clearing the override.

**`sensor_aliases`** — display-only map from sensor ID to a human-readable name. The raw sensor ID is used internally everywhere; aliases are applied at the UI/API layer only. Unused aliases (sensors that have disappeared) are silently ignored.

### SQLite schema

```sql
CREATE TABLE readings (
    ts        INTEGER NOT NULL,  -- unix timestamp
    sensor_id TEXT NOT NULL,
    temp      REAL NOT NULL
);

CREATE TABLE fan_readings (
    ts         INTEGER NOT NULL,  -- unix timestamp
    fan_id     TEXT NOT NULL,
    percent    INTEGER NOT NULL,
    rpm        REAL
);

CREATE INDEX idx_readings_ts ON readings(ts);
CREATE INDEX idx_fan_readings_ts ON fan_readings(ts);
```

Old rows are pruned on each loop iteration based on `history_days` setting.

---

## Auto-Detection

On startup (and via `GET /api/devices`), the service detects:

**Temperature sensors** — scan `/sys/class/hwmon/hwmon*`:
- Read `name` file to identify driver
- Read available `tempN_input` files
- Read `tempN_label` if present (e.g. "Package id 0", "Core 0")
- For `drivetemp` sensors: correlate the hwmon path back to the block device via `/sys/class/block`, read the model from `device/model` and the WWID from `device/wwid`, and produce:
  - A stable sensor ID using the WWID: `drivetemp-wwid-<WWID>/<dev> — <model>`
  - Example: `drivetemp-wwid-naa.5000000000000001/sda — WDC WD120XXXX`
  - Falls back to hwmon directory name if WWID is unavailable
- Return structured list: `{ id, driver, label, current_temp, alias? }`

**Why WWID for drivetemp IDs?** hwmon directory numbers (`hwmon5`, `hwmon6`...) are assigned by the kernel at boot based on driver load order and can shift if drives are added, removed, or reordered. The WWID (`/sys/class/block/<dev>/device/wwid`) is a globally unique hardware identifier that is stable across reboots and drive reordering — making it safe to use as the persistent sensor ID in `config.json`. Non-drivetemp sensors (coretemp, nvme, quadro, etc.) use hwmon-based IDs since their kernel assignment order is deterministic for PCI/onboard devices.

**Fans** — query liquidctl:
- Run `liquidctl list --json` to find connected devices
- Run `liquidctl --direct-access status --json` to enumerate fan channels and current RPM
- Parse `Fan N speed` entries from the status output
- Return structured list: `{ id, label, current_rpm }`

No hardcoded sensor or fan names anywhere in the codebase.

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/state` | Current temps, fan speeds, applied percentages, override status |
| GET | `/api/history` | Time series; params: `hours` (default 24) |
| GET | `/api/config` | Full config (curves + fan assignments + settings + aliases) |
| POST | `/api/config` | Save new config; validated against currently detected devices before write |
| GET | `/api/devices` | All detected sensors (with aliases) and fans |
| POST | `/api/apply` | Trigger immediate controller loop iteration; does not affect loop timer |
| GET | `/api/metrics` | Prometheus text format |
| GET | `/docs` | Auto-generated OpenAPI docs (FastAPI built-in) |

### Config validation

`POST /api/config` validates against currently detected devices before writing:
- Every `fan_config.curve_name` must exist in `curves`
- Every `fan_config.sensor_id` must be in the currently detected sensor list
- Every `fan_config.fan_id` must be in the currently detected fan list
- Every curve must have at least 2 points in ascending temperature order

Hard reject on any violation. The validation cache is the live device scan at request time.

### /api/metrics format (Prometheus)

```
# HELP brisa_temperature_celsius Current temperature reading
# TYPE brisa_temperature_celsius gauge
brisa_temperature_celsius{sensor="coretemp",label="Package id 0"} 38.0

# HELP brisa_fan_rpm Current fan RPM
# TYPE brisa_fan_rpm gauge
brisa_fan_rpm{fan="fan1",label="Upper rear left"} 850.0
```

Aliases are applied to labels in the metrics output when set.

---

## Web UI Pages

### Dashboard
- Current RPM and applied % per configured fan
- Current temperature per configured sensor (alias shown if set)
- Live dot + polling every 10 seconds
- `MANUAL` badge on fan cards when override is active
- "Apply Now" button → POST /api/apply

### Sensors & Fans
- All detected sensors with driver, sensor ID, current temperature
- Inline alias editing per sensor row (click ✎, type alias, Enter or Save)
- Alias shown as primary label; original sensor ID always visible below it
- All detected fan channels with current RPM

### Curves
- List of defined curves with Chart.js line preview
- Inline editable curve name
- Add / edit / delete points; chart updates on field blur (not on every keystroke)
- Add / remove points without losing scroll position
- Delete blocked if curve is assigned to any fan config
- Explicit Save / Discard flow — no auto-save on edit

### Fan Configuration
- Table of fan assignments with override status column
- Add / Edit modal: fan selector, label, sensor selector (shows alias if set), curve selector
- Manual override toggle: when enabled, a fixed percent input replaces curve control
- Override bypasses sensor read, curve interpolation, and safety floor entirely

### History
- Chart.js line graphs: temp over time per sensor, fan % over time per fan
- Time range selector: 1h / 6h / 24h / 7d
- Data from GET /api/history

### Settings
- Interval (seconds), history retention (days), safety floor (%)
- Warning displayed if estimated row count exceeds 5M
- Save → POST /api/config

---

## Controller Loop

```
on startup:
  load config.json
  init SQLite database
  run liquidctl --direct-access initialize all
  for each fan_config:
    if override_percent is set: apply override_percent
    else: apply safety_floor_percent
  start asyncio background task

every interval_seconds:
  scan all hwmon sensors once (single pass for all fans)
  for each fan_config:
    if override_percent is set:
      apply override_percent (no sensor read, no curve, no safety floor)
    else:
      read temp from sensor_id via hwmon scan result
      if sensor not found:
        apply safety_floor_percent
        log warning
        continue
      compute percent via linear interpolation on curve points
      clamp to max(computed, safety_floor_percent)
      call liquidctl --direct-access set <fan_id> speed <percent>
      cache applied percent in memory (_last_applied dict)
  write sensor readings to SQLite (deduplicated by sensor_id)
  write fan readings to SQLite (percent + RPM)
  prune old rows (> history_days)
```

**Safety floor semantics:** the safety floor is a fallback for sensor failure only. It is never applied to manually overridden fans.

**Single sensor scan per iteration:** `detect_sensors()` is called once per loop iteration, not once per fan. The result is shared across all fan configs in that iteration.

Interpolation is linear between each adjacent pair of curve points. Below the first point, the first point's percent is used. Above the last point, the last point's percent is used.

---

## Project Structure

```
brisa/
├── ARCHITECTURE.md
├── README.md
├── LICENSE
├── docker-compose.yml
├── brisa/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py              ← FastAPI app, lifespan, global config/loop state
│       ├── models.py            ← Pydantic models (AppConfig, FanConfig, Curve, etc.)
│       ├── config.py            ← load/save/validate config.json
│       ├── controller.py        ← loop logic, interpolation, _last_applied cache
│       ├── hwmon.py             ← /sys/class/hwmon reader + drivetemp enrichment
│       ├── liquidctl_wrapper.py ← subprocess wrapper for liquidctl
│       ├── database.py          ← SQLite init, read/write, prune
│       ├── api/
│       │   └── routes.py        ← all API endpoints
│       └── static/              ← vanilla JS + HTML pages
│           ├── style.css
│           ├── app.js
│           ├── logo.png
│           ├── logo_text.png
│           ├── favicon.png
│           ├── favicon.ico
│           ├── index.html        ← Dashboard
│           ├── devices.html      ← Sensors & Fans
│           ├── curves.html       ← Curves
│           ├── fanconfig.html    ← Fan Configuration
│           ├── history.html      ← History
│           └── settings.html     ← Settings
└── tests/
```

---

## Deployment

### docker-compose.yml

```yaml
services:
  brisa:
    image: brisa:latest
    container_name: brisa
    restart: unless-stopped
    privileged: true
    network_mode: bridge
    ports:
      - "9595:9595"
    volumes:
      - /docker/brisa:/data
```

### Volume layout

```
/data/
  config.json    ← curves, fan assignments, settings, aliases (created with defaults if missing)
  history.db     ← SQLite database
```

### TrueNAS-specific notes

- `privileged: true` required for USB access to the fan controller
- Do not use TrueNAS Apps UI — deploy via `docker compose` only
- `truenas_admin` must be in docker group
- `/data` should be on an NVMe pool, not spinning rust (SQLite = small random I/O)

---

## Image Details

The Dockerfile uses a multi-stage build. Build tools (`make`, `gcc`, `libc-dev`) are only present in the builder stage and are not included in the final image.

| Layer | Expected size |
|-------|--------------|
| python:3.12.9-slim base | ~130MB |
| libusb + udev (runtime only) | ~5MB |
| liquidctl + deps (incl. pillow) | ~45MB |
| FastAPI + uvicorn + pydantic | ~15MB |
| App code + static files | ~5MB |
| **Total** | **~195MB** |

---

## What This Is Not

- Not a replacement for CoolerControl, fancontrol, or nbfc
- Not a general-purpose fan control solution
- Not designed for non-USB fan controllers (motherboard PWM headers are out of scope)
- Not a full observability platform — use Prometheus + Grafana if you need that; `/api/metrics` gives you the integration point

---

## Open Questions / v2 Backlog

- [ ] Hysteresis support in curves (fans only spin down below X, only spin up above Y)
- [ ] Multi-device support (multiple liquidctl controllers simultaneously)
- [ ] Auth on the web UI (basic auth option)
- [ ] NVMe and other PCI sensor hwmon numbers are stable in practice but not guaranteed — WWID-style stable IDs for those sensors would be a future improvement