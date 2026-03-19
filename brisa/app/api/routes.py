import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from app.controller import _last_applied
from app.database import query_history
from app.hwmon import detect_sensors
from app.liquidctl_wrapper import get_fan_status
from app.models import AppConfig

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_app_config() -> AppConfig:
    from app.main import get_config
    return get_config()


def _set_app_config(config: AppConfig) -> None:
    from app.main import set_config
    set_config(config)


def _apply_aliases(sensors: list[dict], aliases: dict[str, str]) -> list[dict]:
    """Enrich sensor dicts with alias field."""
    for s in sensors:
        s["alias"] = aliases.get(s["id"])
    return sensors


@router.get("/state")
async def get_state():
    config = _get_app_config()

    all_sensors = detect_sensors()
    sensor_map = {s["id"]: s["current_temp"] for s in all_sensors}

    sensors_out = []
    seen_sensors = set()
    for fan_cfg in config.fan_configs:
        if fan_cfg.sensor_id not in seen_sensors:
            seen_sensors.add(fan_cfg.sensor_id)
            sensors_out.append({
                "sensor_id": fan_cfg.sensor_id,
                "alias": config.sensor_aliases.get(fan_cfg.sensor_id),
                "temp": sensor_map.get(fan_cfg.sensor_id),
            })

    try:
        fan_status = get_fan_status()
        rpm_map = {f["id"]: f["current_rpm"] for f in fan_status}
    except Exception as e:
        logger.warning("Could not fetch fan status: %s", e)
        rpm_map = {}

    fans_out = []
    for fan_cfg in config.fan_configs:
        fans_out.append({
            "id": fan_cfg.fan_id,
            "label": fan_cfg.fan_label,
            "current_rpm": rpm_map.get(fan_cfg.fan_id),
            "override_percent": fan_cfg.override_percent,
            "last_percent": _last_applied.get(fan_cfg.fan_id),
        })

    return {
        "sensors": sensors_out,
        "fans": fans_out,
    }


@router.get("/history")
async def get_history(hours: int = 24):
    return query_history(hours)


@router.get("/config")
async def get_config():
    return _get_app_config().model_dump()


@router.post("/config")
async def post_config(new_config: AppConfig):
    from app.config import save_config, validate_config

    try:
        sensors = detect_sensors()
        fans = get_fan_status()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Could not detect devices for validation: {e}")

    known_sensor_ids = [s["id"] for s in sensors]
    known_fan_ids = [f["id"] for f in fans]

    errors = validate_config(new_config, known_sensor_ids, known_fan_ids)
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    save_config(new_config)
    _set_app_config(new_config)
    logger.info("Config updated via API")
    return {"status": "ok"}


@router.get("/devices")
async def get_devices():
    config = _get_app_config()
    sensors = detect_sensors()
    _apply_aliases(sensors, config.sensor_aliases)
    fans = get_fan_status()
    return {
        "sensors": sensors,
        "fans": fans,
    }


@router.post("/apply")
async def apply():
    from app.controller import run_once
    config = _get_app_config()
    await run_once(config)
    return {"status": "ok"}


@router.get("/metrics", include_in_schema=False)
async def metrics():
    config = _get_app_config()
    sensors = detect_sensors()
    _apply_aliases(sensors, config.sensor_aliases)
    fans = get_fan_status()

    lines = []

    lines.append("# HELP brisa_temperature_celsius Current temperature reading")
    lines.append("# TYPE brisa_temperature_celsius gauge")
    for s in sensors:
        display = s.get("alias") or s["label"]
        display = display.replace('"', '\\"')
        driver = s["driver"].replace('"', '\\"')
        lines.append(
            f'brisa_temperature_celsius{{sensor="{driver}",label="{display}"}} {s["current_temp"]}'
        )

    lines.append("# HELP brisa_fan_rpm Current fan RPM")
    lines.append("# TYPE brisa_fan_rpm gauge")
    for f in fans:
        fan_id = f["id"].replace('"', '\\"')
        fan_label = f["label"].replace('"', '\\"')
        lines.append(
            f'brisa_fan_rpm{{fan="{fan_id}",label="{fan_label}"}} {f["current_rpm"]}'
        )

    return PlainTextResponse("\n".join(lines) + "\n")

