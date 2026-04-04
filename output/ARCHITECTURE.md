# Fan Control Service — Architecture Document

**Status:** v0.3.1 — implemented and running
**Last Updated:** March 21, 2026

---

## Problem Statement

TrueNAS SCALE locks down package management, making it impossible to install fan control software via standard system tools (`apt`, `pip`). Existing solutions like CoolerControl assume a general-purpose Linux environment. USB fan controllers with good Linux support rely on `liquidctl` to operate — which cannot be installed on TrueNAS natively.

This project solves exactly that problem: a self-contained Docker-based fan control service for TrueNAS SCALE, targeting any fan controller supported by liquidctl, any motherboard PWM fan header exposed via hwmon, and any temperature source exposed via hwmon. It is not a general-purpose fan control solution.

---

## Scope

**In scope:**
- Any USB fan controller supported by liquidctl (Aquacomputer Quadro is the primary tested device)
- Motherboard PWM fan headers exposed via `/sys/class/hwmon` (tested: Nuvoton NCT6687 Super I/O chip)
- TrueNAS SCALE as the primary target platform
- Any Linux host where Docker runs with USB access
- Temperature sources: any sensor exposed via `/sys/class/hwmon` (coretemp, drivetemp, nvme, etc.)
- Virtual sensors: computed avg/min/max from groups of real sensors
- Fan control: all output channels on any liquidctl-supported device, plus any writable `pwmN` sysfs channel

**Out of scope:**
- Controllers not supported by liquidctl or hwmon
- General Linux fan control (CoolerControl, fancontrol, etc. solve that problem better on standard distros)
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
│  │  resolves   │   │  /api/state → grouped    │ │
│  │  virtual    │   │  /api/history → SQLite   │ │
│  │  sensors    │   │  /api/config → R/W JSON  │ │
│  │  applies    │   │  /api/apply → force loop │ │
│  │  curves     │   │  /api/devices → detect   │ │
│  │  routes to  │   │  /api/metrics → Prom.    │ │
│  │  backend:   │   │                          │ │
│  │  liquidctl  │   │                          │ │
│  │  or sysfs   │   │                          │ │
│  └──────┬──────┘   └──────────────────────────┘ │
│         │                                       │
│  ┌──────▼──────────────────────────────────────┐│
│  │              SQLite Database                ││
│  │  (temp history, fan speed history)          ││
│  └─────────────────────────────────────────────┘│
│                                                 │
│  Volumes:                                       │
│    /data/config.json  ← full config             │
│    /data/history.db   ← SQLite                  │
│  Devices:                                       │
│    /dev/bus/usb (privileged)                    │
│    /sys/class/hwmon (read-write for PWM fans)  │
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
| Fan control | hwmon sysfs (direct write) | Controls motherboard PWM fan headers via `/sys/class/hwmon/hwmonN/pwmN`; no additional dependencies |
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
      "sensor_id": "virtual/all-drives-max",
      "override_percent": null,
      "backend": "liquidctl"
    },
    {
      "fan_id": "hwmon-pwm-nct6687.2592/pwm1",
      "fan_label": "CPU Fan",
      "curve_name": "silent",
      "sensor_id": "k10temp-hwmon3/Tctl",
      "override_percent": null,
      "backend": "hwmon-pwm"
    }
  ],
  "sensor_aliases": {
    "nvme-hwmon1/Sensor 1": "NVMe Boot Drive",
    "drivetemp-wwid-naa.5000000000000001/WDC WD120XXXX": "NAS Drive 1"
  },
  "virtual_sensors": [
    {
      "id": "virtual/all-drives-max",
      "name": "All Drives Max",
      "source_sensor_ids": [
        "drivetemp-wwid-naa.5000000000000001/WDC WD120XXXX",
        "drivetemp-wwid-naa.5000000000000002/WDC WD120XXXX"
      ],
      "aggregation": "max"
    }
  ],
  "dashboard_groups": [
    {
      "id": "grp-exhaust-m4k1a",
      "name": "Exhaust",
      "type": "fan",
      "item_ids": ["fan1", "fan2"]
    },
    {
      "id": "grp-cpu-b7x2p",
      "name": "CPU",
      "type": "sensor",
      "item_ids": ["coretemp-hwmon0/Core 0", "coretemp-hwmon0/Core 1"]
    }
  ],
  "card_colors": {
    "fan1": "teal",
    "virtual/all-drives-max": "amber"
  }
}
```

**`override_percent`** — when set to an integer, the controller applies that fixed speed to the fan every loop iteration, bypassing the sensor read, curve interpolation, and safety floor entirely. The curve and sensor assignments are preserved so they can be restored by clearing the override.

**`backend`** — determines how the fan is controlled. `"liquidctl"` for USB fan controllers (Aquacomputer Quadro, etc.), `"hwmon-pwm"` for motherboard PWM fan headers controlled via sysfs. The backend dictates which code path handles speed writes, RPM reads, and device lifecycle (initialization, shutdown).

**`sensor_aliases`** — display-only map from sensor ID to a human-readable name. The raw sensor ID is used internally everywhere; aliases are applied at the UI/API layer only. Unused aliases (sensors that have disappeared) are silently ignored.

**`virtual_sensors`** — computed sensors that aggregate multiple real sensors. Each has a slug-like ID prefixed with `virtual/`, a display name, a list of source sensor IDs, and an aggregation mode (`avg`, `min`, `max`). Virtual sensors can be used as `sensor_id` in `fan_configs` just like real sensors. Nesting is not allowed — source sensors must be real hwmon sensors. If some source sensors are unavailable, the virtual sensor computes from whatever sources are present; it only fails (triggering safety floor) when all sources are missing.

**`dashboard_groups`** — ordered list of named groups for the dashboard. Each group has a `type` (`sensor` or `fan`) and a list of `item_ids` that belong to it. Groups are displayed in list order. Items not in any group appear in an "Ungrouped" section at the bottom. If no groups are defined, the dashboard falls back to showing all configured fans and their associated sensors (backward compatible).

**`card_colors`** — optional map from sensor or fan ID to a color key. Valid colors: `teal`, `blue`, `purple`, `pink`, `amber`, `orange`, `red`, `slate`. Colors render as a left-border accent on dashboard cards. Items without a color assignment have no accent border.

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

## Virtual Sensors

Virtual sensors are resolved in `controller.py` via `resolve_virtual_sensors()`, called once per loop iteration before curve interpolation.

**Resolution rules:**
- For each virtual sensor, collect temperatures from all source sensors present in the current hwmon scan
- If at least one source has a reading, compute the aggregation (avg/min/max) from available sources
- If all sources are missing, the virtual sensor produces no value — the controller treats this as a missing sensor and applies the safety floor
- Missing individual sources are logged at `debug` level; all-missing is logged at `warning` level

**Validation rules (enforced on `POST /api/config`):**
- At least 2 source sensors required
- All source sensors must be currently detected real hwmon sensors
- No referencing other virtual sensors (no nesting)
- No duplicate virtual sensor IDs
- Aggregation must be `avg`, `min`, or `max`

Virtual sensors appear in:
- `/api/devices` response (under `virtual_sensors` key, with computed temps)
- `/api/state` response (in sensor groups or ungrouped sensors, with computed temps)
- `/api/metrics` output (with `sensor="virtual"` label)
- Fan config sensor selector in the UI (under a `── Virtual Sensors ──` separator)

---

## Auto-Detection

On startup (and via `GET /api/devices`), the service detects:

**Temperature sensors** — scan `/sys/class/hwmon/hwmon*`:
- Read `name` file to identify driver
- Read available `tempN_input` files
- Read `tempN_label` if present (e.g. "Package id 0", "Core 0")
- For `drivetemp` sensors: correlate the hwmon path back to the block device via `/sys/class/block`, read the model from `device/model` and the WWID from `device/wwid`, and produce:
  - A stable sensor ID using the WWID and model only: `drivetemp-wwid-<WWID>/<model>`
  - Example: `drivetemp-wwid-naa.5000000000000001/WDC WD120XXXX`
  - The block device letter (`sda`, `sdb`, etc.) appears in the `label` field for display only — it is excluded from the ID because Linux can reassign device letters across reboots
  - Falls back to hwmon directory name if WWID is unavailable
- Return structured list: `{ id, driver, label, current_temp, alias? }`

**Why WWID for drivetemp IDs?** hwmon directory numbers (`hwmon5`, `hwmon6`...) are assigned by the kernel at boot based on driver load order and can shift if drives are added, removed, or reordered. The WWID (`/sys/class/block/<dev>/device/wwid`) is a globally unique hardware identifier that is stable across reboots and drive reordering — making it safe to use as the persistent sensor ID in `config.json`. The block device letter (`/dev/sdX`) is also unstable across reboots and is therefore excluded from the sensor ID. Non-drivetemp sensors (coretemp, nvme, quadro, etc.) use hwmon-based IDs since their kernel assignment order is deterministic for PCI/onboard devices.

**Config migration:** On startup, `load_config()` runs `migrate_drivetemp_ids()` which detects old-style drivetemp IDs containing a block device letter (e.g. `drivetemp-wwid-<WWID>/sda — <model>`) and rewrites them to the new format (`drivetemp-wwid-<WWID>/<model>`) across all config sections: `sensor_aliases`, `virtual_sensors`, `fan_configs`, `dashboard_groups`, and `card_colors`. If any IDs were migrated, the config is saved back to disk automatically. Each migrated ID is logged individually at INFO level.

**Fans (liquidctl backend)** — query liquidctl:
- Run `liquidctl list --json` to find connected devices
- Run `liquidctl --direct-access status --json` to enumerate fan channels and current RPM
- Parse `Fan N speed` entries from the status output
- Return structured list: `{ id, label, current_rpm, backend: "liquidctl" }`
- If no liquidctl devices are present, returns empty list (no error)

**Fans (hwmon-pwm backend)** — scan `/sys/class/hwmon/hwmon*`:
- For each hwmon device, skip if driver name matches a liquidctl-managed device (blocklist: `quadro`, `octo`, `d5next`, `kraken`, `smart_device`) to avoid duplicate detection
- Check for `pwmN` and `pwmN_enable` files; skip if `pwmN` is not writable
- Build a stable fan ID from the platform device path: `hwmon-pwm-<driver>.<address>/<pwmN>` (e.g. `hwmon-pwm-nct6687.2592/pwm1`). The hwmonN number is not used in the ID because it can change across reboots
- Read `fanN_input` for current RPM, `fanN_label` for driver-provided label
- Return structured list: `{ id, label, current_rpm, backend: "hwmon-pwm" }`

**Why stable IDs for hwmon-pwm fans?** The kernel assigns `hwmonN` numbers at boot based on driver load order. The platform device component (e.g. `nct6687.2592`) is derived from the device's physical bus address and is stable across reboots — same approach as the WWID scheme used for drivetemp sensors.

**Deduplication:** Some USB fan controllers (like the Aquacomputer Quadro) have both a liquidctl interface and a kernel hwmon driver (`aquacomputer_hwmon`). These expose the same fans through both paths. The hwmon-pwm scanner skips any hwmon device whose driver name is in the blocklist, ensuring each fan appears only once and is controlled by the more capable liquidctl backend.

No hardcoded sensor or fan names anywhere in the codebase.

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/state` | Grouped dashboard data: sensor_groups, fan_groups, ungrouped_sensors, ungrouped_fans — each item includes color |
| GET | `/api/history` | Time series; params: `hours` (default 24) |
| GET | `/api/config` | Full config (curves + fan assignments + settings + aliases + virtual sensors + groups + colors) |
| POST | `/api/config` | Save new config; validated against currently detected devices before write |
| GET | `/api/devices` | All detected sensors (with aliases), virtual sensors (with computed temps), and fans |
| POST | `/api/apply` | Trigger immediate controller loop iteration; does not affect loop timer |
| GET | `/api/metrics` | Prometheus text format (includes virtual sensors with `sensor="virtual"`) |
| GET | `/docs` | Auto-generated OpenAPI docs (FastAPI built-in) |

### /api/state response structure

```json
{
  "fan_groups": [
    {
      "id": "grp-exhaust-m4k1a",
      "name": "Exhaust",
      "items": [
        {
          "id": "fan1",
          "label": "Upper rear left",
          "current_rpm": 850.0,
          "override_percent": null,
          "last_percent": 45,
          "color": "teal"
        }
      ]
    }
  ],
  "sensor_groups": [
    {
      "id": "grp-cpu-b7x2p",
      "name": "CPU",
      "items": [
        {
          "sensor_id": "coretemp-hwmon0/Core 0",
          "alias": "CPU Core 0",
          "temp": 42.0,
          "virtual": false,
          "color": null
        }
      ]
    }
  ],
  "ungrouped_fans": [],
  "ungrouped_sensors": []
}
```

### Config validation

`POST /api/config` validates against currently detected devices before writing:
- Every `fan_config.curve_name` must exist in `curves`
- Every `fan_config.sensor_id` must be a currently detected sensor or a defined virtual sensor
- Every `fan_config.fan_id` must be in the currently detected fan list (from either backend)
- Every `fan_config.backend` must be `"liquidctl"` or `"hwmon-pwm"`
- Every curve must have at least 2 points in ascending temperature order
- Virtual sensors must have at least 2 source sensors, all real and currently detected
- Virtual sensors cannot reference other virtual sensors
- No duplicate virtual sensor IDs or dashboard group IDs
- Card colors must be from the valid set: teal, blue, purple, pink, amber, orange, red, slate
- Dashboard group types must be `sensor` or `fan`

Hard reject on any violation. The validation cache is the live device scan at request time — sensor detection is required (503 if it fails), but liquidctl and hwmon-pwm fan detection are independent and non-blocking (if either fails, the other still contributes its fans). All validation errors are logged at WARNING level (with the full error list and known sensor/fan IDs) before the 422 response is returned.

### /api/metrics format (Prometheus)

```
# HELP brisa_temperature_celsius Current temperature reading
# TYPE brisa_temperature_celsius gauge
brisa_temperature_celsius{sensor="coretemp",label="Package id 0"} 38.0
brisa_temperature_celsius{sensor="virtual",label="All Drives Max"} 45.0

# HELP brisa_fan_rpm Current fan RPM
# TYPE brisa_fan_rpm gauge
brisa_fan_rpm{fan="fan1",label="Upper rear left"} 850.0
```

Aliases are applied to labels in the metrics output when set. Virtual sensors use `sensor="virtual"`.

---

## Web UI Pages

### Dashboard
- Organized into **categories** (Fan Speeds, Temperatures) with uppercase section labels and a dividing line
- Within each category, **named groups** are displayed with a teal accent bar and group title
- Items not in any group appear under "Other" at the bottom of each category
- If no groups are defined, falls back to flat display of all configured fans and sensors
- Card accent colors rendered as a left border per card
- `VIRTUAL` badge on virtual sensor cards
- `MANUAL` badge on fan cards when override is active
- Current RPM and applied % per fan; current temperature per sensor
- Live dot + polling every 10 seconds
- "Apply Now" button → POST /api/apply

### Sensors & Fans
- All detected sensors with driver, sensor ID, current temperature
- Inline alias editing per sensor row (click ✎ on the left, type alias, Enter or Save)
- Alias shown as primary label; original sensor ID always visible below it
- **Color picker** per sensor and fan row — 8 color dots + "none"
- **Virtual Sensors** section — create, edit, delete; select aggregation mode and source sensors
- All detected fan channels with current RPM and color picker
- **Dashboard Groups** section at the bottom — create sensor or fan groups, assign items, reorder with ▲/▼

### Curves
- List of defined curves with Chart.js line preview
- Inline editable curve name
- Add / edit / delete points; chart updates on field blur (not on every keystroke)
- Add / remove points without losing scroll position
- Delete blocked if curve is assigned to any fan config
- Explicit Save / Discard flow — no auto-save on edit

### Fan Configuration
- Table of fan assignments with override status column
- Sensor column shows virtual sensor names when applicable
- Add / Edit modal: fan selector, label, sensor selector (real sensors + virtual sensors under separator), curve selector
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
  run liquidctl --direct-access initialize all (non-fatal if no devices)
  for each fan_config:
    if backend is hwmon-pwm: take over fan (save original pwmN_enable, write 1)
    if override_percent is set: apply override_percent
    else: apply safety_floor_percent
  start asyncio background task

every interval_seconds:
  scan all hwmon sensors once (single pass)
  resolve virtual sensors from real sensor readings:
    for each virtual sensor:
      collect temps from available source sensors
      if at least one source present: compute avg/min/max
      if all sources missing: skip (no value produced)
  merge real + virtual sensor maps
  for each fan_config:
    if override_percent is set:
      apply override_percent (no sensor read, no curve, no safety floor)
    else:
      read temp from merged sensor map
      if sensor not found (real missing, or virtual with all sources missing):
        apply safety_floor_percent
        log warning
        continue
      compute percent via linear interpolation on curve points
      route to backend:
        liquidctl: call liquidctl --direct-access set <fan_id> speed <percent>
        hwmon-pwm: write round(percent * 255 / 100) to /sys/class/hwmon/hwmonN/pwmN
      cache applied percent in memory (_last_applied dict)
  collect RPMs from all backends (liquidctl status + fanN_input reads)
  write sensor readings to SQLite (deduplicated by sensor_id)
  write fan readings to SQLite (percent + RPM)
  prune old rows (> history_days)

on graceful shutdown (SIGTERM / docker stop):
  for each hwmon-pwm fan that was taken over:
    restore original pwmN_enable value (e.g. 99 for nct6687 firmware mode)
  cancel controller loop task
```

**Safety floor semantics:** the safety floor is a fallback for sensor failure only. It is never applied to manually overridden fans. For virtual sensors, it triggers only when all source sensors are unavailable.

**Single sensor scan per iteration:** `detect_sensors()` is called once per loop iteration, not once per fan. The result is shared across all fan configs in that iteration. Virtual sensor resolution also happens once, before the per-fan loop.

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
│       ├── models.py            ← Pydantic models (AppConfig, FanConfig with backend field, Curve, VirtualSensor, DashboardGroup, etc.)
│       ├── config.py            ← load/save/validate config.json, drivetemp ID migration (incl. virtual sensor + group + color validation)
│       ├── controller.py        ← loop logic, backend routing, interpolation, virtual sensor resolution, _last_applied cache
│       ├── sensors.py           ← /sys/class/hwmon temperature reader + drivetemp enrichment (renamed from hwmon.py)
│       ├── hwmon_pwm.py         ← sysfs PWM fan detection, control, takeover/release lifecycle
│       ├── liquidctl_wrapper.py ← subprocess wrapper for liquidctl
│       ├── database.py          ← SQLite init, read/write, prune
│       ├── api/
│       │   └── routes.py        ← all API endpoints (grouped state, virtual sensor dicts, card colors)
│       └── static/              ← vanilla JS + HTML pages
│           ├── style.css         ← theme, card colors, dashboard group/category styles
│           ├── app.js
│           ├── logo.png
│           ├── logo_text.png
│           ├── favicon.png
│           ├── favicon.ico
│           ├── index.html        ← Dashboard (grouped layout, card colors)
│           ├── devices.html      ← Sensors & Fans (aliases, colors, virtual sensors, dashboard groups)
│           ├── curves.html       ← Curves
│           ├── fanconfig.html    ← Fan Configuration (virtual sensor support in selector)
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
  config.json    ← curves, fan assignments, settings, aliases, virtual sensors, groups, colors
  history.db     ← SQLite database
```

### TrueNAS-specific notes

- `privileged: true` required for USB access to the fan controller and sysfs writes for hwmon-pwm fans
- Do not use TrueNAS Apps UI — deploy via `docker compose` only
- `truenas_admin` must be in docker group
- `/data` should be on an NVMe pool, not spinning rust (SQLite = small random I/O)
- hwmon-pwm fans require a Super I/O chip with a loaded kernel driver (e.g. `nct6775`, `it87`, `nct6687`); many NAS-specific boards (e.g. Topton N22) lack these chips

### Podman deployment

Podman runs rootless by default on most Linux distributions. Rootless mode uses a user namespace where `--privileged` does not grant real host root — sysfs writes will fail silently and hwmon-pwm fans will not be controllable.

For hwmon-pwm fan control with Podman, run as real root:

```bash
sudo podman run --privileged -v /sys:/sys -p 9595:9595 -v /path/to/data:/data brisa:latest
```

The `-v /sys:/sys` bind mount may be needed with Podman even in rootful mode, as Podman's default sysfs mount can be read-only. Docker does not require this — its `--privileged` flag grants full sysfs access by default.

If only using liquidctl (USB) fans, rootless Podman with `--privileged` is sufficient.

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
- Not a general-purpose fan control solution — targets TrueNAS SCALE and Docker-based deployments
- Not a full observability platform — use Prometheus + Grafana if you need that; `/api/metrics` gives you the integration point

---

## Security Considerations

### Privileged container

Brisa requires `privileged: true` to access USB devices and sysfs. A privileged container has effectively root access to the host, including:

- Full access to all host devices (`/dev/*`)
- Ability to read and write any sysfs path (not just hwmon — also power management, PCI config, etc.)
- Ability to mount filesystems and load kernel modules
- Effectively equivalent to root on the host

This is an inherent requirement for hardware fan control from within a container. There is no way to control USB fan controllers or write to sysfs PWM files without elevated privileges. The same privilege level is required by any containerized fan control solution.

**Mitigation:** Brisa has no network-facing authentication, no outbound network calls, and no code execution features. The attack surface is limited to the REST API on port 9595. For homelab deployments on a trusted local network, the risk is low. Do not expose port 9595 to the internet.

### hwmon-pwm sysfs writes

The hwmon-pwm backend writes to `/sys/class/hwmon/hwmonN/pwmN` and `/sys/class/hwmon/hwmonN/pwmN_enable`. These writes only affect fan speed and control mode for the specific PWM channel. No other sysfs paths are written to by the application. Adding hwmon-pwm support does not increase the container's privilege level — the `privileged: true` flag already grants full sysfs access regardless of whether the application uses it.

---

## Known Limitations

### Container crash behavior (hwmon-pwm)

If the container is killed without a graceful shutdown (OOM kill, `docker kill -9`, kernel panic, power loss), hwmon-pwm fans remain at their last-written PWM duty cycle and control mode (`pwmN_enable = 1`). The BIOS/firmware fan curves do not resume until:

- The system is rebooted (BIOS re-initializes all Super I/O registers), or
- Another tool writes `pwmN_enable` back to the firmware value (e.g. `99` for nct6687, `2` for most other drivers)

This does not apply to liquidctl fans — USB controllers like the Quadro have their own firmware that continues operating independently.

**Mitigation:** Use `restart: unless-stopped` in `docker-compose.yml`. On container startup, Brisa takes over configured fans (saving the original enable value) and restores them on graceful shutdown. A restart after a crash will re-take-over the fans and resume normal operation.

### Motherboard without Super I/O driver

Many NAS-specific motherboards (e.g. Topton N22 with Intel N100/N305) use minimal embedded controllers for fan management instead of a traditional Super I/O chip. These boards may have BIOS-level fan control but no Linux kernel driver to expose sysfs PWM files. On such systems, hwmon-pwm detection will find zero controllable fans. This is a kernel/hardware limitation, not a Brisa limitation.

### Non-writable PWM channels

Some hwmon devices expose `fanN_input` (RPM reading) without a corresponding writable `pwmN` file. This can occur when the kernel driver supports monitoring but not control, or when the BIOS has locked the PWM registers. Brisa only lists fans where both `pwmN` and `pwmN_enable` exist and `pwmN` is writable.

---

## Open Questions / v3 Backlog

- [ ] Hysteresis support in curves (fans only spin down below X, only spin up above Y)
- [ ] Multi-device support (multiple liquidctl controllers simultaneously)
- [ ] Auth on the web UI (basic auth option)
- [ ] NVMe and other PCI sensor hwmon numbers are stable in practice but not guaranteed — WWID-style stable IDs for those sensors would be a future improvement
- [ ] GPU fan control via amdgpu hwmon (detected but currently skipped — needs testing and safety review)
- [ ] Expand hwmon-pwm deduplication blocklist as more liquidctl-backed devices are reported