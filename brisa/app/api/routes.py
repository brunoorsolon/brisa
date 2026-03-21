import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from app.controller import _last_applied, resolve_virtual_sensors
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


def _build_virtual_sensor_dicts(config: AppConfig, real_sensor_map: dict[str, float]) -> list[dict]:
    """Build sensor-like dicts for virtual sensors with computed temps."""
    virtual_temps = resolve_virtual_sensors(config.virtual_sensors, real_sensor_map)
    result = []
    for vs in config.virtual_sensors:
        result.append({
            "id": vs.id,
            "driver": "virtual",
            "label": vs.name,
            "current_temp": virtual_temps.get(vs.id),
            "alias": config.sensor_aliases.get(vs.id),
            "virtual": True,
            "aggregation": vs.aggregation,
            "source_sensor_ids": vs.source_sensor_ids,
        })
    return result


@router.get("/state")
async def get_state():
    config = _get_app_config()

    all_sensors = detect_sensors()
    real_sensor_map = {s["id"]: s["current_temp"] for s in all_sensors}

    # Resolve virtual sensors
    virtual_temps = resolve_virtual_sensors(config.virtual_sensors, real_sensor_map)
    sensor_map = {**real_sensor_map, **virtual_temps}

    vs_map = {vs.id: vs for vs in config.virtual_sensors}

    def build_sensor_dict(sid):
        vs = vs_map.get(sid)
        return {
            "sensor_id": sid,
            "alias": config.sensor_aliases.get(sid) or (vs.name if vs else None),
            "temp": sensor_map.get(sid),
            "virtual": sid in vs_map,
            "color": config.card_colors.get(sid),
        }

    try:
        fan_status = get_fan_status()
        rpm_map = {f["id"]: f["current_rpm"] for f in fan_status}
    except Exception as e:
        logger.warning("Could not fetch fan status: %s", e)
        rpm_map = {}

    fan_cfg_map = {fc.fan_id: fc for fc in config.fan_configs}

    def build_fan_dict(fan_id):
        fc = fan_cfg_map.get(fan_id)
        return {
            "id": fan_id,
            "label": fc.fan_label if fc else fan_id,
            "current_rpm": rpm_map.get(fan_id),
            "override_percent": fc.override_percent if fc else None,
            "last_percent": _last_applied.get(fan_id),
            "color": config.card_colors.get(fan_id),
        }

    # Build grouped output
    # Track which items are in groups so we can find ungrouped ones
    grouped_sensor_ids = set()
    grouped_fan_ids = set()

    sensor_groups = []
    fan_groups = []

    for grp in config.dashboard_groups:
        if grp.type == "sensor":
            items = []
            for sid in grp.item_ids:
                grouped_sensor_ids.add(sid)
                items.append(build_sensor_dict(sid))
            sensor_groups.append({
                "id": grp.id,
                "name": grp.name,
                "items": items,
            })
        elif grp.type == "fan":
            items = []
            for fid in grp.item_ids:
                grouped_fan_ids.add(fid)
                items.append(build_fan_dict(fid))
            fan_groups.append({
                "id": grp.id,
                "name": grp.name,
                "items": items,
            })

    # Ungrouped: fan-config sensors and configured fans not in any group
    ungrouped_sensors = []
    seen = set()
    for fc in config.fan_configs:
        if fc.sensor_id not in grouped_sensor_ids and fc.sensor_id not in seen:
            seen.add(fc.sensor_id)
            ungrouped_sensors.append(build_sensor_dict(fc.sensor_id))

    ungrouped_fans = []
    for fc in config.fan_configs:
        if fc.fan_id not in grouped_fan_ids and fc.fan_id not in seen:
            seen.add(fc.fan_id)
            ungrouped_fans.append(build_fan_dict(fc.fan_id))

    return {
        "sensor_groups": sensor_groups,
        "fan_groups": fan_groups,
        "ungrouped_sensors": ungrouped_sensors,
        "ungrouped_fans": ungrouped_fans,
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
        logger.error("POST /config: device detection failed: %s", e)
        raise HTTPException(status_code=503, detail=f"Could not detect devices for validation: {e}")

    known_sensor_ids = [s["id"] for s in sensors]
    known_fan_ids = [f["id"] for f in fans]

    errors = validate_config(new_config, known_sensor_ids, known_fan_ids)
    if errors:
        logger.warning("POST /config: validation failed with %d error(s):", len(errors))
        for err in errors:
            logger.warning("  - %s", err)
        logger.warning("POST /config: known sensor IDs: %s", known_sensor_ids)
        logger.warning("POST /config: known fan IDs: %s", known_fan_ids)
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

    # Append virtual sensors
    real_sensor_map = {s["id"]: s["current_temp"] for s in sensors}
    virtual_sensor_dicts = _build_virtual_sensor_dicts(config, real_sensor_map)

    fans = get_fan_status()
    return {
        "sensors": sensors,
        "virtual_sensors": virtual_sensor_dicts,
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

    real_sensor_map = {s["id"]: s["current_temp"] for s in sensors}

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

    # Virtual sensors in metrics
    virtual_sensor_dicts = _build_virtual_sensor_dicts(config, real_sensor_map)
    for vs in virtual_sensor_dicts:
        if vs["current_temp"] is not None:
            display = (vs.get("alias") or vs["label"]).replace('"', '\\"')
            lines.append(
                f'brisa_temperature_celsius{{sensor="virtual",label="{display}"}} {vs["current_temp"]}'
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