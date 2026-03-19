<p align="center">
  <img src="brisa/app/static/logo_text.png" width="250">
</p>

Brisa is a self-contained Docker service for controlling USB fan controllers on TrueNAS SCALE (and any other Linux host where you can run Docker but can't install packages directly).

Supports any device compatible with [liquidctl](https://github.com/liquidctl/liquidctl) and any temperature source exposed via `/sys/class/hwmon`.

---

## Features

- **Docker-only** — no host installs required
- **TrueNAS SCALE** primary target, works on any Linux host with Docker
- **liquidctl-compatible** USB fan controllers (tested: Aquacomputer Quadro)
- **hwmon temperature sources** — CPU, NVMe, drives (drivetemp), network adapters, anything the kernel exposes
- **Fan curves** — configurable temperature→speed curves per fan
- **Manual override** — bypass curve control and hold a fixed speed per fan for testing
- **Sensor aliases** — assign human-readable names to sensors without changing how they're referenced internally
- **Drive identification** — drivetemp sensors automatically show device name and model (e.g. `sda — WDC WD120EFGX-68`)
- **Web UI** — dashboard, curve editor, fan config, history charts, settings
- **REST API** with auto-generated OpenAPI docs at `/docs`
- **Prometheus metrics** at `/api/metrics`
- **SQLite history** with configurable retention

---

## Disclaimer on AI Usage

I built this project to solve a specific problem in my own TrueNAS SCALE homelab (controlling fans through Docker + liquidctl).

I'm a software engineer, but not very experienced with Python, so I used AI tools to help write part of the code. Everything was reviewed, tested, and adjusted by me before being included here.

I'm sharing this in case it helps someone else with a similar setup. This note is included purely for transparency. It’s not meant as a philosophical statement on AI usage or to start any discussion around it.

---

## Requirements

- Docker with USB passthrough (`privileged: true`)
- A USB fan controller supported by liquidctl (tested: Aquacomputer Quadro)
- Temperature sensors accessible via `/sys/class/hwmon`

Hardware access is required — there is no simulation mode.

---

## Quick Start

```bash
git clone https://github.com/youruser/brisa.git
cd brisa
docker build -t brisa:latest .
docker compose up -d
```

The web UI is available at `http://<host>:9595`.

On first run, a default `config.json` is created at your `/data` volume path. No fans will be controlled until you configure curves and fan assignments through the UI.

---

## Configuration

Everything is configured through the web UI:

1. **Sensors & Fans** — see all detected hardware; set aliases for sensors
2. **Curves** — define temperature→speed curves
3. **Fan Config** — assign each fan a sensor and a curve
4. **Settings** — adjust poll interval, history retention, safety floor

The config is stored as `/data/config.json` on your mounted volume.

---

## Volume Layout

```
/data/
  config.json    ← curves, fan assignments, settings, aliases
  history.db     ← SQLite time-series database
```

---

## TrueNAS SCALE Notes

- Deploy via `docker compose` only — do not use the TrueNAS Apps UI
- `privileged: true` is required for USB access to the fan controller
- Mount `/data` to a path on your NVMe pool — SQLite does not perform well on spinning rust

Example `docker-compose.yml`:

```yaml
services:
  brisa:
    image: brisa:latest
    build: ./brisa
    container_name: brisa
    restart: unless-stopped
    privileged: true
    network_mode: bridge
    ports:
      - "9595:9595"
    volumes:
      - /docker/brisa:/data
```

---

## Safety Floor

The safety floor (`safety_floor_percent`, default 30%) is applied when a configured sensor cannot be read. It is a failure fallback, not a minimum speed policy — it does not apply to fans in manual override mode.

---

## API

Full OpenAPI docs at `http://<host>:9595/docs`.

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/state` | Current temps, fan speeds, applied %, override status |
| GET | `/api/history` | Time series (`?hours=24`) |
| GET | `/api/config` | Full config |
| POST | `/api/config` | Save new config (validated against detected devices) |
| GET | `/api/devices` | Detected sensors and fans |
| POST | `/api/apply` | Trigger immediate control loop iteration |
| GET | `/api/metrics` | Prometheus metrics |

---

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for a full description of the design, data model, controller loop, and project structure.

---

## Development

```bash
docker build -t brisa:latest .
docker compose up
```

Logs:

```bash
docker logs -f brisa
```
