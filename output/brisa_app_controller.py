import asyncio
import logging
import time

from app.database import write_reading, write_fan_reading, prune_old_rows
from app.sensors import detect_sensors
from app.liquidctl_wrapper import set_fan_speed as liquidctl_set_speed, get_fan_status
from app import hwmon_pwm

logger = logging.getLogger(__name__)

# Last applied percent per fan_id — read by /api/state
_last_applied: dict[str, int] = {}

# Track which hwmon-pwm fans have been taken over in this session
_pwm_taken_over: set[str] = set()


def interpolate(points: list[dict], temp: float) -> int:
    if not points:
        raise ValueError("Curve has no points")
    if temp <= points[0]["temp"]:
        return points[0]["percent"]
    if temp >= points[-1]["temp"]:
        return points[-1]["percent"]
    for i in range(len(points) - 1):
        lo = points[i]
        hi = points[i + 1]
        if lo["temp"] <= temp <= hi["temp"]:
            ratio = (temp - lo["temp"]) / (hi["temp"] - lo["temp"])
            return round(lo["percent"] + ratio * (hi["percent"] - lo["percent"]))
    return points[-1]["percent"]


def resolve_virtual_sensors(
    virtual_sensors: list,
    real_sensor_map: dict[str, float],
) -> dict[str, float]:
    """
    Compute virtual sensor temperatures from real sensor readings.

    Returns a dict of virtual_sensor_id -> computed_temp.
    Uses whichever source sensors are available. Only skips if ALL sources are missing.
    """
    results: dict[str, float] = {}

    for vs in virtual_sensors:
        temps = []
        for src_id in vs.source_sensor_ids:
            temp = real_sensor_map.get(src_id)
            if temp is None:
                logger.debug(
                    "Virtual sensor '%s': source sensor '%s' not found, ignoring",
                    vs.id, src_id,
                )
            else:
                temps.append(temp)

        if not temps:
            logger.warning(
                "Virtual sensor '%s': all source sensors missing, skipping",
                vs.id,
            )
            continue

        if vs.aggregation == "avg":
            results[vs.id] = sum(temps) / len(temps)
        elif vs.aggregation == "min":
            results[vs.id] = min(temps)
        elif vs.aggregation == "max":
            results[vs.id] = max(temps)
        else:
            logger.warning(
                "Virtual sensor '%s': unknown aggregation '%s'",
                vs.id, vs.aggregation,
            )

    return results


def _ensure_pwm_takeover(fan_id: str) -> bool:
    """
    Ensure a hwmon-pwm fan has been taken over (manual mode enabled).
    Only performs the takeover once per session per fan.
    Returns True if the fan is ready for control.
    """
    if fan_id in _pwm_taken_over:
        return True
    if hwmon_pwm.takeover(fan_id):
        _pwm_taken_over.add(fan_id)
        return True
    return False


def _apply_fan_speed(fan_id: str, backend: str, percent: int) -> None:
    """Route fan speed command to the correct backend."""
    if backend == "hwmon-pwm":
        if not _ensure_pwm_takeover(fan_id):
            raise RuntimeError(f"Cannot take over hwmon-pwm fan '{fan_id}'")
        hwmon_pwm.set_fan_speed(fan_id, percent)
    else:
        liquidctl_set_speed(fan_id, percent)


def _get_rpm_map(config) -> dict[str, float]:
    """
    Collect RPM readings from all backends.
    Returns a merged dict of fan_id -> current_rpm.
    """
    rpm_map: dict[str, float] = {}

    # liquidctl RPMs (if any liquidctl fans are configured)
    has_liquidctl = any(fc.backend == "liquidctl" for fc in config.fan_configs)
    if has_liquidctl:
        try:
            fan_status = get_fan_status()
            for f in fan_status:
                rpm_map[f["id"]] = f["current_rpm"]
        except RuntimeError as e:
            logger.warning("Could not fetch liquidctl fan RPMs: %s", e)

    # hwmon-pwm RPMs
    for fc in config.fan_configs:
        if fc.backend == "hwmon-pwm":
            rpm = hwmon_pwm.get_fan_rpm(fc.fan_id)
            if rpm is not None:
                rpm_map[fc.fan_id] = rpm

    return rpm_map


async def run_once(config) -> None:
    ts = int(time.time())
    curve_map = {c.name: c for c in config.curves}

    all_sensors = detect_sensors()
    real_sensor_map = {s["id"]: s["current_temp"] for s in all_sensors}

    # Resolve virtual sensors and merge into the sensor map
    virtual_temps = resolve_virtual_sensors(config.virtual_sensors, real_sensor_map)
    sensor_map = {**real_sensor_map, **virtual_temps}

    fan_results: list[dict] = []
    sensor_temps: dict[str, float] = {}

    for fan_cfg in config.fan_configs:
        # Manual override — skip sensor read and curve entirely
        if fan_cfg.override_percent is not None:
            percent = fan_cfg.override_percent
            logger.debug("Fan '%s': manual override at %d%%", fan_cfg.fan_id, percent)
        else:
            curve = curve_map.get(fan_cfg.curve_name)
            if curve is None:
                logger.warning("Fan '%s': curve '%s' not found, applying safety floor",
                               fan_cfg.fan_id, fan_cfg.curve_name)
                percent = config.settings.safety_floor_percent
            else:
                temp = sensor_map.get(fan_cfg.sensor_id)
                if temp is None:
                    logger.warning("Fan '%s': sensor '%s' not found, applying safety floor",
                                   fan_cfg.fan_id, fan_cfg.sensor_id)
                    percent = config.settings.safety_floor_percent
                else:
                    sensor_temps[fan_cfg.sensor_id] = temp
                    points = [{"temp": p.temp, "percent": p.percent} for p in curve.points]
                    percent = interpolate(points, temp)

        try:
            _apply_fan_speed(fan_cfg.fan_id, fan_cfg.backend, percent)
            fan_results.append({"fan_id": fan_cfg.fan_id, "percent": percent})
            _last_applied[fan_cfg.fan_id] = percent
            logger.debug("Set %s to %d%% via %s", fan_cfg.fan_id, percent, fan_cfg.backend)
        except RuntimeError as e:
            logger.error("Failed to set fan speed for '%s': %s", fan_cfg.fan_id, e)

    rpm_map = _get_rpm_map(config)

    for sensor_id, temp in sensor_temps.items():
        write_reading(ts, sensor_id, temp)

    for fr in fan_results:
        rpm = rpm_map.get(fr["fan_id"])
        write_fan_reading(ts, fr["fan_id"], fr["percent"], rpm)

    prune_old_rows(config.settings.history_days)


async def loop() -> None:
    from app.main import get_config
    from app.liquidctl_wrapper import initialize

    logger.info("Controller loop starting")

    try:
        initialize()
    except RuntimeError as e:
        logger.error("%s — continuing anyway", e)

    config = get_config()
    if config.fan_configs:
        logger.info("Applying initial speeds to all configured fans")
        for fan_cfg in config.fan_configs:
            initial = fan_cfg.override_percent if fan_cfg.override_percent is not None \
                      else config.settings.safety_floor_percent
            try:
                _apply_fan_speed(fan_cfg.fan_id, fan_cfg.backend, initial)
            except RuntimeError as e:
                logger.error("Failed to apply initial speed to '%s': %s", fan_cfg.fan_id, e)

    while True:
        config = get_config()
        try:
            await run_once(config)
        except Exception as e:
            logger.error("Unhandled error in controller loop: %s", e, exc_info=True)
        await asyncio.sleep(config.settings.interval_seconds)