<p align="center">
  <img src="brisa/app/static/logo_text.png" width="250">
</p>

*v0.3.0*

Brisa is a self-contained Docker service for controlling fans on TrueNAS SCALE (and any other Linux host where you can run Docker but can't install packages directly).

Supports USB fan controllers via [liquidctl](https://github.com/liquidctl/liquidctl), motherboard PWM fan headers via sysfs, and any temperature source exposed via `/sys/class/hwmon`.

---

## Features

- **Docker-only** — no host installs required
- **TrueNAS SCALE** primary target, works on any Linux host with Docker
- **Two fan control backends:**
  - **liquidctl** — USB fan controllers (tested: Aquacomputer Quadro)
  - **hwmon-pwm** — motherboard PWM fan headers via sysfs (tested: Nuvoton NCT6687)
- **hwmon temperature sources** — CPU, NVMe, drives (drivetemp), network adapters, anything the kernel exposes
- **Fan curves** — configurable temperature→speed curves per fan
- **Manual override** — bypass curve control and hold a fixed speed per fan for testing
- **Virtual sensors** — create computed sensors from groups of real sensors using avg, min, or max aggregation; usable in fan configs like any real sensor
- **Sensor aliases** — assign human-readable names to sensors without changing how they're referenced internally
- **Drive identification** — drivetemp sensors automatically show device name and model (e.g. `sda — WDC WD120EFGX-68`)
- **Dashboard groups** — organize sensors and fans into named groups on the dashboard with configurable order
- **Card accent colors** — assign colors to individual sensor and fan cards from a curated palette
- **Web UI** — dashboard, curve editor, fan config, history charts, settings
- **REST API** with auto-generated OpenAPI docs at `/docs`
- **Prometheus metrics** at `/api/metrics` (includes virtual sensors)
- **SQLite history** with configurable retention

---

## Screenshots

| Dashboard | Sensors & Fans |
|:-:|:-:|
| ![Dashboard](https://imgur.com/gjAFiGj.png) | ![Sensors & Fans](https://imgur.com/R1qigY9.png) |

| Curves | History |
|:-:|:-:|
| ![Curves](https://imgur.com/kHOx54W.png) | ![History](https://imgur.com/1YeoXkg.png) |

---

## Disclaimer on AI Usage

I built this project to solve a specific problem in my own TrueNAS SCALE homelab (controlling fans through Docker + liquidctl).

I'm a software engineer, but not very experienced with Python, so I used AI tools to help write part of the code. Everything was reviewed, tested, and adjusted by me before being included here.

I'm sharing this in case it helps someone else with a similar setup. This note is included purely for transparency. It's not meant as a philosophical statement on AI usage or to start any discussion around it.

---

## Requirements

- Docker with `privileged: true`
- At least one of:
  - A USB fan controller supported by liquidctl (tested: Aquacomputer Quadro)
  - Motherboard PWM fan headers with a supported kernel driver (tested: Nuvoton NCT6687; also supports nct6775, it87, w83627ehf, and other Super I/O chips)
- Temperature sensors accessible via `/sys/class/hwmon`

Hardware access is required — there is no simulation mode.

Either backend works independently — you don't need a USB controller to use hwmon-pwm fans, and vice versa.

---

## Quick Start

```bash
git clone https://github.com/brunoorsolon/brisa.git
docker build -t brisa:latest brisa/
docker compose up -d
```

The web UI is available at `http://<host>:9595`.

On first run, a default `config.json` is created at your `/data` volume path. No fans will be controlled until you configure curves and fan assignments through the UI.

---

## Configuration

Everything is configured through the web UI:

1. **Sensors & Fans** — see all detected hardware; set aliases, card colors, create virtual sensors, and manage dashboard groups
2. **Curves** — define temperature→speed curves
3. **Fan Config** — assign each fan a sensor (real or virtual) and a curve
4. **Settings** — adjust poll interval, history retention, safety floor

The config is stored as `/data/config.json` on your mounted volume.

---

## Virtual Sensors

Virtual sensors let you create a single computed temperature from a group of real sensors. Useful for controlling fans based on the average, maximum, or minimum temperature across a set of drives, CPU cores, or any other sensors.

- **Aggregation modes:** average, minimum, maximum
- **Resilient:** if some source sensors are unavailable, the virtual sensor computes from whatever is available; only skips if all sources are missing
- **Usable everywhere:** virtual sensors appear in the fan config sensor selector and can be pinned to the dashboard just like real sensors
- **No nesting:** virtual sensors can only reference real hwmon sensors, not other virtual sensors

Virtual sensors are created and managed on the **Sensors & Fans** page.

---

## Dashboard Groups

The dashboard organizes fans and sensors into named groups displayed in order. Groups are configured on the **Sensors & Fans** page.

- **Sensor groups** and **fan groups** are separate (a group contains only sensors or only fans)
- Groups are displayed in the order you set, with ▲/▼ reordering
- Items not assigned to any group appear in an "Other" section at the bottom
- If no groups are defined, all configured fans and their associated sensors are shown (backward compatible)

---

## Card Colors

Each sensor or fan can be assigned an accent color from a curated palette: teal, blue, purple, pink, amber, orange, red, or slate. The color appears as a left border on the dashboard card. Colors are set on the **Sensors & Fans** page.

---

## Volume Layout

```
/data/
  config.json    ← curves, fan assignments, settings, aliases, virtual sensors, dashboard groups
  history.db     ← SQLite time-series database
```

---

## TrueNAS SCALE Notes

- Deploy via `docker compose` only — do not use the TrueNAS Apps UI
- `privileged: true` is required for USB access and sysfs PWM writes
- Mount `/data` to a path on your NVMe pool — SQLite does not perform well on spinning rust
- Many NAS-specific boards (e.g. Topton N22) lack a Super I/O chip with a Linux kernel driver — on these systems, hwmon-pwm fans will not be detected and only liquidctl (USB) fans are available

Example `docker-compose.yml`:

```yaml
services:
  brisa:
    image: brisa:latest
    build: brisa/
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

## Podman

Podman runs rootless by default, which means `--privileged` does not grant real host root. Sysfs writes for hwmon-pwm fans will fail silently in rootless mode.

For hwmon-pwm fan control with Podman, run as real root with `/sys` mounted:

```bash
sudo podman build -t brisa:latest brisa/
sudo podman run --privileged -v /sys:/sys -p 9595:9595 -v /path/to/data:/data brisa:latest
```

If only using liquidctl (USB) fans, rootless Podman with `--privileged` is sufficient.

---

## Security

Brisa runs with `privileged: true`, which gives the container effectively root access to the host. This is required for USB device access and sysfs PWM writes — there is no way around it for hardware fan control from a container.

What this means in practice: the container can access all host devices, write to any sysfs path, and mount filesystems. Brisa only writes to `/sys/class/hwmon/hwmonN/pwmN` and `pwmN_enable` files, but the capability is broader than what the application uses.

**Recommendations:**
- Do not expose port 9595 to the internet — Brisa has no authentication
- Use `restart: unless-stopped` to ensure fans are re-managed after a crash
- Review the container image contents if running on a sensitive system

For homelab deployments on a trusted local network, the practical risk is low.

---

## Known Limitations

**hwmon-pwm fans on container crash:** if the container is killed without a graceful shutdown (OOM, `kill -9`, power loss), hwmon-pwm fans stay at their last-written speed until the system is rebooted. On graceful shutdown (`docker stop`, `docker compose down`), Brisa restores the original firmware control mode automatically. liquidctl (USB) fans are not affected — USB controllers like the Quadro have their own firmware.

**No Super I/O driver:** boards without a supported kernel driver for their fan controller chip (common on embedded NAS boards) will show zero hwmon-pwm fans. This is a kernel limitation. Check `ls /sys/class/hwmon/` and inspect the `name` files to see if a Super I/O driver is loaded (e.g. `nct6775`, `nct6687`, `it87`).

---

## Safety Floor

The safety floor (`safety_floor_percent`, default 30%) is applied when a configured sensor cannot be read. It is a failure fallback, not a minimum speed policy — it does not apply to fans in manual override mode.

For virtual sensors, the safety floor triggers only when all source sensors are unavailable.

---

## API

Full OpenAPI docs at `http://<host>:9595/docs`.

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/state` | Grouped dashboard data: fan groups, sensor groups, ungrouped items |
| GET | `/api/history` | Time series (`?hours=24`) |
| GET | `/api/config` | Full config |
| POST | `/api/config` | Save new config (validated against detected devices) |
| GET | `/api/devices` | Detected sensors, virtual sensors, and fans |
| POST | `/api/apply` | Trigger immediate control loop iteration |
| GET | `/api/metrics` | Prometheus metrics (includes virtual sensors) |

---

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for a full description of the design, data model, controller loop, and project structure.

---

## Development

```bash
docker build -t brisa:latest brisa/
docker compose up -d
```

Optional: if you don't have a compose file, you can use the example provided, just remember to update the volume mappings to match your system.

Run this before docker compose up -d:
```bash
mv docker-compose.yml.example docker-compose.yml
```

Logs:

```bash
docker logs -f brisa
```
