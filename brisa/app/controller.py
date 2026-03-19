import asyncio
import logging
import time

from app.database import write_reading, write_fan_reading, prune_old_rows
from app.hwmon import detect_sensors
from app.liquidctl_wrapper import set_fan_speed, get_fan_status

logger = logging.getLogger(__name__)

# Last applied percent per fan_id — read by /api/state
_last_applied: dict[str, int] = {}


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


async def run_once(config) -> None:
    ts = int(time.time())
    curve_map = {c.name: c for c in config.curves}

    all_sensors = detect_sensors()
    sensor_map = {s["id"]: s["current_temp"] for s in all_sensors}

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
                    raw_percent = interpolate(points, temp)
                    percent = max(raw_percent, config.settings.safety_floor_percent)

        try:
            set_fan_speed(fan_cfg.fan_id, percent)
            fan_results.append({"fan_id": fan_cfg.fan_id, "percent": percent})
            _last_applied[fan_cfg.fan_id] = percent
            logger.debug("Set %s to %d%%", fan_cfg.fan_id, percent)
        except RuntimeError as e:
            logger.error("Failed to set fan speed for '%s': %s", fan_cfg.fan_id, e)

    rpm_map: dict[str, float] = {}
    try:
        fan_status = get_fan_status()
        rpm_map = {f["id"]: f["current_rpm"] for f in fan_status}
    except RuntimeError as e:
        logger.warning("Could not fetch fan RPMs for DB write: %s", e)

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
        logger.error("liquidctl initialize failed: %s — continuing anyway", e)

    config = get_config()
    if config.fan_configs:
        logger.info("Applying initial speeds to all configured fans")
        for fan_cfg in config.fan_configs:
            initial = fan_cfg.override_percent if fan_cfg.override_percent is not None \
                      else config.settings.safety_floor_percent
            try:
                set_fan_speed(fan_cfg.fan_id, initial)
            except RuntimeError as e:
                logger.error("Failed to apply initial speed to '%s': %s", fan_cfg.fan_id, e)

    while True:
        config = get_config()
        try:
            await run_once(config)
        except Exception as e:
            logger.error("Unhandled error in controller loop: %s", e, exc_info=True)
        await asyncio.sleep(config.settings.interval_seconds)

