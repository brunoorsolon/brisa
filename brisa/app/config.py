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

# Regex to match legacy hwmon-directory-based sensor IDs:
#   <driver>-hwmon<N>/<label>   e.g. "nct6798-hwmon10/SYSTIN"
# Captures the driver and label so the ID can be re-resolved against the
# currently detected devices even after the kernel renumbered hwmonN.
_LEGACY_HWMON_RE = re.compile(
    r'^(?P<driver>.+?)-hwmon\d+/(?P<label>.+)$'
)


def _migrate_drivetemp_id(old_id: str) -> str:
    """
    If old_id matches the old drivetemp format with /sdX, return the new
    format with model only.  Otherwise return the original ID unchanged.
    """
    m = _OLD_DRIVETEMP_RE.match(old_id)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return old_id


def _migrate_hwmon_id(
    old_id: str,
    known_ids: set[str],
    by_driver_label: dict[tuple[str, str], set[str]],
) -> str:
    """
    Rewrite a legacy hwmon-directory-based sensor ID to the current stable ID.

    Only rewrites when the (driver, label) pair maps to exactly one detected
    sensor — labels are not guaranteed unique (several NVMe drives all report
    "Composite"), and guessing could point a fan at the wrong sensor.
    """
    if old_id in known_ids:
        return old_id

    m = _LEGACY_HWMON_RE.match(old_id)
    if not m:
        return old_id

    candidates = by_driver_label.get((m.group("driver"), m.group("label")))
    if not candidates or len(candidates) != 1:
        return old_id
    return next(iter(candidates))


def _rewrite_sensor_ids(config: AppConfig, rewrite) -> int:
    """
    Apply rewrite() to every sensor ID stored in the config.
    Returns the number of IDs that changed.
    """
    count = 0

    def fix(sensor_id: str) -> str:
        nonlocal count
        new_id = rewrite(sensor_id)
        if new_id != sensor_id:
            count += 1
            logger.info("Migrated sensor ID: %s -> %s", sensor_id, new_id)
        return new_id

    config.sensor_aliases = {fix(sid): alias for sid, alias in config.sensor_aliases.items()}
    for vs in config.virtual_sensors:
        vs.source_sensor_ids = [fix(sid) for sid in vs.source_sensor_ids]
    for fc in config.fan_configs:
        fc.sensor_id = fix(fc.sensor_id)
    for grp in config.dashboard_groups:
        grp.item_ids = [fix(sid) for sid in grp.item_ids]
    config.card_colors = {fix(sid): color for sid, color in config.card_colors.items()}

    return count


def migrate_sensor_ids(config: AppConfig) -> tuple[AppConfig, int]:
    """
    Rewrite old drivetemp IDs (with a block device letter) to the WWID + model
    form, and sensor IDs that embed a volatile hwmon directory number
    (e.g. "nct6798-hwmon10/SYSTIN") to the current stable form
    (e.g. "nct6798-nct6775.656/SYSTIN").

    The current hwmon scan supplies the stable IDs, so this also repairs
    configs whose stored hwmonN was renumbered by a reboot. Sensors that are
    not currently detected are left untouched rather than guessed at.
    """
    from app.sensors import detect_sensors

    sensors = detect_sensors(include_smartctl=False)
    known_ids = {s["id"] for s in sensors}
    by_driver_label: dict[tuple[str, str], set[str]] = {}
    for s in sensors:
        by_driver_label.setdefault((s["driver"], s["label"]), set()).add(s["id"])

    def rewrite(sensor_id: str) -> str:
        return _migrate_hwmon_id(_migrate_drivetemp_id(sensor_id), known_ids, by_driver_label)

    return config, _rewrite_sensor_ids(config, rewrite)


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

    # Migrate hwmon-pwm backend field: fan IDs starting with "hwmon-pwm-"
    # must use the "hwmon-pwm" backend, not the default "liquidctl".
    backend_fixed = 0
    for fc in config.fan_configs:
        if fc.fan_id.startswith("hwmon-pwm-") and fc.backend != "hwmon-pwm":
            logger.warning("Fixing backend for '%s': %s -> hwmon-pwm", fc.fan_id, fc.backend)
            fc.backend = "hwmon-pwm"
            backend_fixed += 1
    if backend_fixed:
        save_config(config)

    # Migrate old drivetemp IDs and legacy hwmon-directory-based sensor IDs
    config, migrated = migrate_sensor_ids(config)
    if migrated > 0:
        logger.warning("Migrated %d sensor ID(s) in config", migrated)
        save_config(config)
        logger.info("Config saved after sensor ID migration")

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