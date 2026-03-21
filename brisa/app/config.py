import json
import logging
import re
from pathlib import Path

from app.models import AppConfig

logger = logging.getLogger(__name__)

CONFIG_PATH = Path("/data/config.json")

DEFAULT_CONFIG = AppConfig()

# Regex to match old-style drivetemp IDs that contain a block device letter:
#   drivetemp-wwid-<WWID>/sdX — <model>
# Captures: (prefix including wwid), (block device letter part), (model)
_OLD_DRIVETEMP_RE = re.compile(
    r'^(drivetemp-wwid-[^/]+)/sd[a-z]+ \u2014 (.+)$'
)


def _migrate_sensor_id(old_id: str) -> str:
    """
    If old_id matches the old drivetemp format with /sdX, return the new
    format with model only.  Otherwise return the original ID unchanged.
    """
    m = _OLD_DRIVETEMP_RE.match(old_id)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return old_id


def migrate_drivetemp_ids(config: AppConfig) -> tuple[AppConfig, int]:
    """
    Rewrite any old-style drivetemp sensor IDs (containing /sdX) to the
    new stable format (WWID + model only).

    Returns (possibly-modified config, number of IDs migrated).
    """
    count = 0

    # sensor_aliases: keys are sensor IDs
    new_aliases: dict[str, str] = {}
    for sid, alias in config.sensor_aliases.items():
        new_sid = _migrate_sensor_id(sid)
        if new_sid != sid:
            count += 1
            logger.info("Migrated alias key: %s -> %s", sid, new_sid)
        new_aliases[new_sid] = alias
    config.sensor_aliases = new_aliases

    # virtual_sensors: source_sensor_ids
    for vs in config.virtual_sensors:
        new_sources = []
        for sid in vs.source_sensor_ids:
            new_sid = _migrate_sensor_id(sid)
            if new_sid != sid:
                count += 1
                logger.info("Migrated virtual sensor '%s' source: %s -> %s", vs.id, sid, new_sid)
            new_sources.append(new_sid)
        vs.source_sensor_ids = new_sources

    # fan_configs: sensor_id
    for fc in config.fan_configs:
        new_sid = _migrate_sensor_id(fc.sensor_id)
        if new_sid != fc.sensor_id:
            count += 1
            logger.info("Migrated fan config '%s' sensor: %s -> %s", fc.fan_id, fc.sensor_id, new_sid)
            fc.sensor_id = new_sid

    # dashboard_groups: item_ids
    for grp in config.dashboard_groups:
        new_items = []
        for sid in grp.item_ids:
            new_sid = _migrate_sensor_id(sid)
            if new_sid != sid:
                count += 1
                logger.info("Migrated group '%s' item: %s -> %s", grp.name, sid, new_sid)
            new_items.append(new_sid)
        grp.item_ids = new_items

    # card_colors: keys are sensor/fan IDs
    new_colors: dict[str, str] = {}
    for sid, color in config.card_colors.items():
        new_sid = _migrate_sensor_id(sid)
        if new_sid != sid:
            count += 1
            logger.info("Migrated card color key: %s -> %s", sid, new_sid)
        new_colors[new_sid] = color
    config.card_colors = new_colors

    return config, count


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
    except json.JSONDecodeError as e:
        raise ValueError(f"Config file is not valid JSON: {e}") from e
    except Exception as e:
        raise ValueError(f"Config file failed validation: {e}") from e

    # Migrate old drivetemp IDs if needed
    config, migrated = migrate_drivetemp_ids(config)
    if migrated > 0:
        logger.warning("Migrated %d old-style drivetemp sensor ID(s) in config", migrated)
        save_config(config)
        logger.info("Config saved after drivetemp ID migration")

    return config


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
        if fan_cfg.backend not in ("liquidctl", "hwmon-pwm"):
            errors.append(
                f"Fan '{fan_cfg.fan_id}' has invalid backend '{fan_cfg.backend}' (must be liquidctl or hwmon-pwm)"
            )
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