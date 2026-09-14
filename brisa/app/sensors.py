import json
import logging
import os
import re
import subprocess

logger = logging.getLogger(__name__)

_smartctl_available: bool | None = None

HWMON_PATH = "/sys/class/hwmon"
BLOCK_PATH = "/sys/class/block"

# Platform/onboard device names of the form "<name>.<address>", e.g.
# "nct6775.656" or "coretemp.0". The address comes from the device's
# physical location, so the name stays stable across reboots.
_STABLE_DEVICE_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9_]*\.\d+$')


def _read_file(path: str) -> str | None:
    """Read a sysfs file and return stripped content, or None on failure."""
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except OSError:
        return None


def stable_device_id(hwmon_path: str) -> str | None:
    """
    Extract a stable device identifier from the hwmon real path.

    Example:
        /sys/devices/platform/nct6775.656/hwmon/hwmon10
        -> "nct6775.656"

    Looks for a platform device component (driver.address) in the path.
    Returns None if no stable component can be identified.
    """
    for part in os.path.realpath(hwmon_path).split("/"):
        if _STABLE_DEVICE_RE.match(part):
            return part
    return None


def _safe_wwid(wwid: str) -> str:
    """Strip whitespace and collapse internal spaces for use in an ID string."""
    return re.sub(r'\s+', '_', wwid.strip())


def _build_drivetemp_map() -> dict[str, tuple[str, str, str]]:
    """
    Build a mapping from resolved hwmon device path ->
        (stable_key, human_label, model)

    stable_key uses the drive WWID from /sys/class/block/<dev>/device/wwid,
    which is a globally unique identifier stable across reboots and drive
    reordering. Falls back to hwmon directory name if WWID is unavailable.

    human_label includes the block device letter for display:
        "sda — WDC WD120EFGX-68"

    model is the drive model string without the block device letter,
    used in the sensor ID for full reboot stability.
    """
    mapping: dict[str, tuple[str, str, str]] = {}

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
            mapping[hwmon_real] = (stable_key, label, model or dev)

    return mapping


def _smartctl_read_drive(dev_path: str) -> dict | None:
    """Run smartctl on a block device and return temperature info, or None."""
    global _smartctl_available
    if _smartctl_available is False:
        return None

    try:
        result = subprocess.run(
            ["smartctl", "--json=c", "-a", dev_path],
            capture_output=True, timeout=10,
        )
    except FileNotFoundError:
        if _smartctl_available is None:
            logger.info("smartctl not installed, SAS/SCSI temperature detection disabled")
        _smartctl_available = False
        return None
    except subprocess.TimeoutExpired:
        logger.warning("smartctl timed out for %s", dev_path)
        return None

    _smartctl_available = True

    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return None

    temp = data.get("temperature", {}).get("current")
    if temp is None:
        return None

    model = data.get("model_name", "")
    serial = data.get("serial_number", "")

    wwn = data.get("wwn")
    if wwn and "naa" in wwn and "id" in wwn:
        oui = wwn.get("oui", 0)
        stable_key = f"wwid-naa.{wwn['naa']:x}{oui:06x}{wwn['id']:09x}"
    elif serial:
        stable_key = f"serial-{_safe_wwid(serial)}"
    else:
        stable_key = None

    return {
        "temp": float(temp),
        "model": model,
        "stable_key": stable_key,
    }


def _detect_smartctl_sensors() -> list[dict]:
    """
    Find drives with SMART temperature data but no hwmon temperature entry.
    Covers SAS drives that the drivetemp kernel module doesn't support.
    """
    sensors = []

    try:
        block_devs = os.listdir(BLOCK_PATH)
    except OSError:
        return sensors

    for dev in sorted(block_devs):
        if not dev.startswith("sd"):
            continue

        dev_path = os.path.join(BLOCK_PATH, dev)

        if os.path.exists(os.path.join(dev_path, "partition")):
            continue

        block_real = os.path.realpath(dev_path)
        hwmon_sub = os.path.join(block_real, "device", "hwmon")

        # Skip drives already covered by drivetemp/hwmon
        if os.path.isdir(hwmon_sub):
            try:
                if os.listdir(hwmon_sub):
                    continue
            except OSError:
                pass

        info = _smartctl_read_drive(f"/dev/{dev}")
        if info is None:
            continue

        stable_key = info["stable_key"] or dev
        model = info["model"] or dev
        label = f"{dev} — {model}" if info["model"] else dev
        sensor_id = f"smartctl-{stable_key}/{model}"

        sensors.append({
            "id": sensor_id,
            "driver": "smartctl",
            "label": label,
            "current_temp": info["temp"],
        })

    return sensors


def detect_sensors(include_smartctl: bool = True) -> list[dict]:
    """
    Scan /sys/class/hwmon and return all available temperature sensors.

    Returns a list of dicts:
        {
            "id": "coretemp-coretemp.0/Package id 0",
            "driver": "coretemp",
            "label": "Package id 0",
            "current_temp": 38.0
        }

    Sensor IDs never embed the hwmon directory number (hwmonN): the kernel
    assigns those at boot and can renumber them across reboots. Devices with a
    platform device component use it instead (e.g. "nct6798-nct6775.656/SYSTIN");
    devices without one fall back to the hwmon directory name.

    For drivetemp sensors the id uses WWID + model only (no block device letter):
        "drivetemp-wwid-naa.50014ee2c1c21634/WDC WD120EFGX-68"
    The label still includes the block device letter for display:
        "sda — WDC WD120EFGX-68"
    Falls back to hwmon directory name if WWID is unavailable.

    Set include_smartctl=False to skip the (slower) smartctl fallback scan.
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
        stable_id = stable_device_id(device_path)

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
                stable_key, label, model = drivetemp_map[device_path]
                # ID uses WWID + model only — no block device letter
                sensor_id = f"drivetemp-{stable_key}/{model}"
            else:
                label_raw = _read_file(os.path.join(device_path, f"temp{n}_label"))
                label = label_raw if label_raw else f"temp{n}"
                # Prefer the stable device component over the volatile hwmonN
                # directory, which the kernel can renumber across reboots.
                id_scope = stable_id or hwmon_dir
                sensor_id = f"{driver}-{id_scope}/{label}"

            sensors.append({
                "id": sensor_id,
                "driver": driver,
                "label": label,
                "current_temp": current_temp,
            })

    # Fallback: detect SAS/SCSI drives via smartctl (no hwmon coverage)
    if include_smartctl:
        smartctl_sensors = _detect_smartctl_sensors()
        if smartctl_sensors:
            logger.info("Detected %d additional sensor(s) via smartctl", len(smartctl_sensors))
            sensors.extend(smartctl_sensors)

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