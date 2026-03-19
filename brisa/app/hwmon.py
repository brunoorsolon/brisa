import logging
import os
import re

logger = logging.getLogger(__name__)

HWMON_PATH = "/sys/class/hwmon"
BLOCK_PATH = "/sys/class/block"


def _read_file(path: str) -> str | None:
    """Read a sysfs file and return stripped content, or None on failure."""
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except OSError:
        return None


def _safe_wwid(wwid: str) -> str:
    """Strip whitespace and collapse internal spaces for use in an ID string."""
    return re.sub(r'\s+', '_', wwid.strip())


def _build_drivetemp_map() -> dict[str, tuple[str, str]]:
    """
    Build a mapping from resolved hwmon device path ->
        (stable_key, human_label)

    stable_key uses the drive WWID from /sys/class/block/<dev>/device/wwid,
    which is a globally unique identifier stable across reboots and drive
    reordering. Falls back to hwmon directory name if WWID is unavailable.

    human_label is e.g. "sda — WDC WD120EFGX-68".
    """
    mapping: dict[str, tuple[str, str]] = {}

    try:
        block_devs = os.listdir(BLOCK_PATH)
    except OSError as e:
        logger.warning("Cannot read %s: %s", BLOCK_PATH, e)
        return mapping

    for dev in sorted(block_devs):
        dev_path = os.path.join(BLOCK_PATH, dev)

        # Skip partitions
        if os.path.exists(os.path.join(dev_path, "partition")):
            continue

        block_real = os.path.realpath(dev_path)
        hwmon_sub = os.path.join(block_real, "device", "hwmon")

        if not os.path.isdir(hwmon_sub):
            continue

        try:
            hwmon_entries = os.listdir(hwmon_sub)
        except OSError:
            continue

        model_raw = _read_file(os.path.join(block_real, "device", "model"))
        model = model_raw.strip() if model_raw else None

        wwid_raw = _read_file(os.path.join(block_real, "device", "wwid"))
        wwid = _safe_wwid(wwid_raw) if wwid_raw else None

        label = f"{dev} \u2014 {model}" if model else dev

        for hwmon_entry in hwmon_entries:
            hwmon_real = os.path.realpath(os.path.join(hwmon_sub, hwmon_entry))
            stable_key = f"wwid-{wwid}" if wwid else hwmon_entry
            mapping[hwmon_real] = (stable_key, label)

    return mapping


def detect_sensors() -> list[dict]:
    """
    Scan /sys/class/hwmon and return all available temperature sensors.

    Returns a list of dicts:
        {
            "id": "coretemp-hwmon4/Package id 0",
            "driver": "coretemp",
            "label": "Package id 0",
            "current_temp": 38.0
        }

    For drivetemp sensors the id uses the drive WWID for stability:
        "drivetemp-wwid-naa.50014ee2c1c21634/sda — WDC WD120EFGX-68"
    Falls back to hwmon directory name if WWID is unavailable.
    """
    sensors = []
    drivetemp_map = _build_drivetemp_map()

    try:
        hwmon_dirs = sorted(os.listdir(HWMON_PATH))
    except OSError as e:
        logger.error("Cannot read %s: %s", HWMON_PATH, e)
        return sensors

    for hwmon_dir in hwmon_dirs:
        hwmon_full = os.path.join(HWMON_PATH, hwmon_dir)

        try:
            device_path = os.path.realpath(hwmon_full)
        except OSError:
            device_path = hwmon_full

        driver = _read_file(os.path.join(device_path, "name")) or hwmon_dir

        try:
            entries = os.listdir(device_path)
        except OSError as e:
            logger.warning("Cannot list %s: %s", device_path, e)
            continue

        temp_inputs = sorted(
            e for e in entries if e.startswith("temp") and e.endswith("_input")
        )

        for temp_input in temp_inputs:
            n = temp_input[len("temp"):-len("_input")]

            raw = _read_file(os.path.join(device_path, temp_input))
            if raw is None:
                continue

            try:
                current_temp = int(raw) / 1000.0
            except ValueError:
                logger.warning("Cannot parse temp value '%s' from %s", raw, temp_input)
                continue

            if driver == "drivetemp" and device_path in drivetemp_map:
                stable_key, label = drivetemp_map[device_path]
                sensor_id = f"drivetemp-{stable_key}/{label}"
            else:
                label_raw = _read_file(os.path.join(device_path, f"temp{n}_label"))
                label = label_raw if label_raw else f"temp{n}"
                sensor_id = f"{driver}-{hwmon_dir}/{label}"

            sensors.append({
                "id": sensor_id,
                "driver": driver,
                "label": label,
                "current_temp": current_temp,
            })

    logger.info("Detected %d temperature sensor(s)", len(sensors))
    return sensors


def read_temp(sensor_id: str) -> float:
    """
    Read current temperature for a given sensor_id.
    Raises ValueError if sensor_id is not found.
    """
    sensors = detect_sensors()
    for sensor in sensors:
        if sensor["id"] == sensor_id:
            return sensor["current_temp"]
    raise ValueError(f"Sensor not found: {sensor_id!r}")

