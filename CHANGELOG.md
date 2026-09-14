# Changelog

## v1.0.2 - 2026-09-14

### Fixed

- Sensor IDs no longer embed the kernel-assigned hwmon directory number (`hwmonN`), which can change across reboots and left stored `sensor_id` references unknown. Temperature sensor IDs now use the stable platform device component (e.g. `nct6798-nct6775.656/SYSTIN`), the same scheme already used for hwmon-pwm fan IDs.
- Configs saved before this change are migrated on startup: stored sensor IDs that embed a stale `hwmonN` are re-resolved against the current hwmon scan, so a config broken by a reboot is repaired automatically instead of being rejected as referencing an unknown sensor.

## v1.0.1 - 2026-05-10

### Fixed

- Removed unresolved merge conflict markers from the FastAPI app startup file that caused the published Docker image to fail with a `SyntaxError` on boot.
- Removed unresolved merge conflict markers from the frontend sidebar version display.

## v1.0.0 - 2026-03-21

### Added

- Initial stable release of Brisa.
- Docker-based fan control service for TrueNAS SCALE and Linux hosts.
- Support for liquidctl USB fan controllers and hwmon PWM fan headers.
- Web UI, REST API, Prometheus metrics, virtual sensors, dashboard groups, card colors, and SQLite history.
