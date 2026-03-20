import json
import logging
from pathlib import Path

from app.models import AppConfig

logger = logging.getLogger(__name__)

CONFIG_PATH = Path("/data/config.json")

DEFAULT_CONFIG = AppConfig()


def load_config() -> AppConfig:
    """
    Load config from CONFIG_PATH.
    If the file doesn't exist, write defaults and return them.
    Raises ValueError if the file exists but is invalid.
    """
    if not CONFIG_PATH.exists():
        logger.info("No config file found at %s, writing defaults", CONFIG_PATH)
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.model_copy(deep=True)

    try:
        raw = CONFIG_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        config = AppConfig.model_validate(data)
        logger.info("Loaded config from %s", CONFIG_PATH)
        return config
    except json.JSONDecodeError as e:
        raise ValueError(f"Config file is not valid JSON: {e}") from e
    except Exception as e:
        raise ValueError(f"Config file failed validation: {e}") from e


def save_config(config: AppConfig) -> None:
    """
    Write config to CONFIG_PATH as pretty-printed JSON.
    Writes atomically via a temp file to avoid corruption on crash.
    """
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = CONFIG_PATH.with_suffix(".json.tmp")
    try:
        tmp_path.write_text(
            json.dumps(config.model_dump(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp_path.replace(CONFIG_PATH)
        logger.info("Saved config to %s", CONFIG_PATH)
    except OSError as e:
        logger.error("Failed to save config: %s", e)
        raise


# Curated card color keys — must match frontend CARD_COLORS map
VALID_CARD_COLORS = {"teal", "blue", "purple", "pink", "amber", "orange", "red", "slate"}


def validate_config(config: AppConfig, known_sensor_ids: list[str], known_fan_ids: list[str]) -> list[str]:
    """
    Validate config against currently detected devices.
    Returns a list of error strings. Empty list means valid.
    """
    errors = []
    curve_names = {c.name for c in config.curves}

    # Build the set of all valid sensor IDs: real + virtual
    virtual_sensor_ids = {vs.id for vs in config.virtual_sensors}
    all_sensor_ids = set(known_sensor_ids) | virtual_sensor_ids

    # Validate virtual sensors
    for vs in config.virtual_sensors:
        if not vs.id:
            errors.append("Virtual sensor has empty ID")
        if vs.aggregation not in ("avg", "min", "max"):
            errors.append(
                f"Virtual sensor '{vs.id}' has invalid aggregation '{vs.aggregation}' (must be avg, min, or max)"
            )
        if len(vs.source_sensor_ids) < 2:
            errors.append(
                f"Virtual sensor '{vs.id}' must reference at least 2 source sensors"
            )
        for src_id in vs.source_sensor_ids:
            if src_id not in known_sensor_ids:
                errors.append(
                    f"Virtual sensor '{vs.id}' references unknown sensor '{src_id}'"
                )
            if src_id in virtual_sensor_ids:
                errors.append(
                    f"Virtual sensor '{vs.id}' cannot reference another virtual sensor '{src_id}'"
                )

    # Check for duplicate virtual sensor IDs
    seen_vs_ids = set()
    for vs in config.virtual_sensors:
        if vs.id in seen_vs_ids:
            errors.append(f"Duplicate virtual sensor ID '{vs.id}'")
        seen_vs_ids.add(vs.id)

    # Validate fan configs — sensor_id can now be a virtual sensor
    for fan_cfg in config.fan_configs:
        if fan_cfg.curve_name not in curve_names:
            errors.append(
                f"Fan '{fan_cfg.fan_id}' references unknown curve '{fan_cfg.curve_name}'"
            )
        if fan_cfg.sensor_id not in all_sensor_ids:
            errors.append(
                f"Fan '{fan_cfg.fan_id}' references unknown sensor '{fan_cfg.sensor_id}'"
            )
        if fan_cfg.fan_id not in known_fan_ids:
            errors.append(
                f"Fan config references unknown fan '{fan_cfg.fan_id}'"
            )

    for curve in config.curves:
        if len(curve.points) < 2:
            errors.append(
                f"Curve '{curve.name}' must have at least 2 points"
            )
        else:
            temps = [p.temp for p in curve.points]
            if temps != sorted(temps):
                errors.append(
                    f"Curve '{curve.name}' points must be in ascending temperature order"
                )

    # Validate dashboard groups
    seen_group_ids = set()
    for grp in config.dashboard_groups:
        if grp.id in seen_group_ids:
            errors.append(f"Duplicate dashboard group ID '{grp.id}'")
        seen_group_ids.add(grp.id)
        if grp.type not in ("sensor", "fan"):
            errors.append(
                f"Dashboard group '{grp.name}' has invalid type '{grp.type}' (must be sensor or fan)"
            )

    # Validate card colors
    for item_id, color in config.card_colors.items():
        if color not in VALID_CARD_COLORS:
            errors.append(
                f"Card color '{color}' for '{item_id}' is not valid (must be one of {VALID_CARD_COLORS})"
            )

    return errors