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


def validate_config(config: AppConfig, known_sensor_ids: list[str], known_fan_ids: list[str]) -> list[str]:
    """
    Validate config against currently detected devices.
    Returns a list of error strings. Empty list means valid.
    """
    errors = []
    curve_names = {c.name for c in config.curves}

    for fan_cfg in config.fan_configs:
        if fan_cfg.curve_name not in curve_names:
            errors.append(
                f"Fan '{fan_cfg.fan_id}' references unknown curve '{fan_cfg.curve_name}'"
            )
        if fan_cfg.sensor_id not in known_sensor_ids:
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

    return errors
