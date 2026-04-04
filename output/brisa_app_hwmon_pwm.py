import logging
import os
import re

logger = logging.getLogger(__name__)

HWMON_PATH = "/sys/class/hwmon"

# hwmon driver names that are managed by liquidctl — skip during PWM scan
# to avoid detecting the same fan twice via different backends.
LIQUIDCTL_HWMON_DRIVERS = {"quadro", "octo", "d5next", "kraken", "smart_device"}

# Saved original pwmN_enable values for restore on shutdown.
# Key: sysfs path to pwmN_enable, Value: original contents.
_original_enable: dict[str, str] = {}


def _read_file(path: str) -> str | None:
    """Read a sysfs file and return stripped content, or None on failure."""
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except OSError:
        return None


def _write_file(path: str, value: str) -> bool:
    """Write a value to a sysfs file. Returns True on success."""
    try:
        with open(path, "w") as f:
            f.write(value)
        return True
    except OSError as e:
        logger.error("Failed to write '%s' to %s: %s", value, path, e)
        return False


def _stable_device_id(hwmon_path: str) -> str | None:
    """
    Extract a stable device identifier from the hwmon real path.

    Example:
        /sys/devices/platform/nct6687.2592/hwmon/hwmon7
        -> "nct6687.2592"

    Looks for a platform device component (driver.address) in the path.
    Returns None if no stable component can be identified.
    """
    real = os.path.realpath(hwmon_path)
    # Walk the path components looking for platform device pattern
    # e.g. "nct6687.2592", "it8688.2592", "w83627ehf.656"
    for part in real.split("/"):
        if re.match(r'^[a-zA-Z][a-zA-Z0-9_]*\.\d+$', part):
            return part
    return None


def detect_pwm_fans() -> list[dict]:
    """
    Scan /sys/class/hwmon for controllable PWM fan channels.

    Returns a list of dicts:
        {
            "id": "hwmon-pwm-nct6687.2592/pwm1",
            "label": "pwm1",
            "driver": "nct6687",
            "current_rpm": 716.0,
            "hwmon_dir": "hwmon7",
            "pwm_path": "/sys/class/hwmon/hwmon7/pwm1",
            "enable_path": "/sys/class/hwmon/hwmon7/pwm1_enable",
            "rpm_path": "/sys/class/hwmon/hwmon7/fan1_input",
        }

    Skips devices whose driver name is in LIQUIDCTL_HWMON_DRIVERS.
    Only includes channels where pwmN and pwmN_enable exist.
    """
    fans = []

    try:
        hwmon_dirs = sorted(os.listdir(HWMON_PATH))
    except OSError as e:
        logger.error("Cannot read %s: %s", HWMON_PATH, e)
        return fans

    for hwmon_dir in hwmon_dirs:
        hwmon_full = os.path.join(HWMON_PATH, hwmon_dir)
        driver = _read_file(os.path.join(hwmon_full, "name"))
        if not driver:
            continue

        # Skip liquidctl-managed devices
        if driver in LIQUIDCTL_HWMON_DRIVERS:
            logger.debug("Skipping %s (%s): liquidctl-managed device", hwmon_dir, driver)
            continue

        # Find a stable device ID for this hwmon entry
        stable_id = _stable_device_id(hwmon_full)
        if not stable_id:
            logger.debug("Skipping %s (%s): no stable device ID found", hwmon_dir, driver)
            continue

        # Scan for pwmN files in this hwmon directory
        try:
            entries = os.listdir(hwmon_full)
        except OSError:
            continue

        pwm_channels = sorted(
            e for e in entries
            if re.match(r'^pwm\d+$', e)
        )

        for pwm_name in pwm_channels:
            pwm_path = os.path.join(hwmon_full, pwm_name)
            enable_path = f"{pwm_path}_enable"

            # Must have both pwmN and pwmN_enable
            if not os.path.exists(enable_path):
                continue

            # Check if pwmN is writable (only reliable way to know)
            if not os.access(pwm_path, os.W_OK):
                logger.debug("Skipping %s/%s: not writable", hwmon_dir, pwm_name)
                continue

            # Extract channel number for RPM path
            n = pwm_name[3:]  # "pwm1" -> "1"
            rpm_path = os.path.join(hwmon_full, f"fan{n}_input")

            # Read current RPM if available
            current_rpm = None
            rpm_raw = _read_file(rpm_path)
            if rpm_raw is not None:
                try:
                    current_rpm = float(rpm_raw)
                except ValueError:
                    pass

            fan_id = f"hwmon-pwm-{stable_id}/{pwm_name}"

            # Try to read a fan label if the driver provides one
            label_path = os.path.join(hwmon_full, f"fan{n}_label")
            label = _read_file(label_path) or pwm_name

            fans.append({
                "id": fan_id,
                "label": label,
                "driver": driver,
                "current_rpm": current_rpm,
                "hwmon_dir": hwmon_dir,
                "pwm_path": pwm_path,
                "enable_path": enable_path,
                "rpm_path": rpm_path if os.path.exists(rpm_path) else None,
            })

    logger.info("Detected %d hwmon PWM fan channel(s)", len(fans))
    return fans


def _resolve_paths(fan_id: str) -> dict | None:
    """
    Resolve a stable fan_id back to current sysfs paths.

    Since hwmonN numbers can change, we scan all hwmon dirs to find
    the one matching the stable device ID in the fan_id.

    Returns dict with pwm_path, enable_path, rpm_path or None if not found.
    """
    # Parse fan_id: "hwmon-pwm-nct6687.2592/pwm1"
    match = re.match(r'^hwmon-pwm-([^/]+)/(pwm\d+)$', fan_id)
    if not match:
        logger.error("Invalid hwmon-pwm fan_id format: %s", fan_id)
        return None

    target_device_id = match.group(1)
    pwm_name = match.group(2)
    n = pwm_name[3:]

    try:
        hwmon_dirs = os.listdir(HWMON_PATH)
    except OSError:
        return None

    for hwmon_dir in hwmon_dirs:
        hwmon_full = os.path.join(HWMON_PATH, hwmon_dir)
        stable_id = _stable_device_id(hwmon_full)
        if stable_id == target_device_id:
            pwm_path = os.path.join(hwmon_full, pwm_name)
            if os.path.exists(pwm_path):
                rpm_path = os.path.join(hwmon_full, f"fan{n}_input")
                return {
                    "pwm_path": pwm_path,
                    "enable_path": f"{pwm_path}_enable",
                    "rpm_path": rpm_path if os.path.exists(rpm_path) else None,
                }

    logger.error("Cannot resolve fan_id '%s': device not found", fan_id)
    return None


def takeover(fan_id: str) -> bool:
    """
    Take manual control of a PWM fan channel.

    Saves the original pwmN_enable value, then writes '1' (manual mode).
    Returns True on success.
    """
    paths = _resolve_paths(fan_id)
    if not paths:
        return False

    enable_path = paths["enable_path"]

    # Save original enable value (only if not already saved)
    if enable_path not in _original_enable:
        original = _read_file(enable_path)
        if original is None:
            logger.error("Cannot read %s for takeover", enable_path)
            return False
        _original_enable[enable_path] = original
        logger.info("Saved original pwm_enable for %s: %s", fan_id, original)

    if not _write_file(enable_path, "1"):
        return False

    logger.info("Took manual control of %s", fan_id)
    return True


def release(fan_id: str) -> bool:
    """
    Release manual control of a PWM fan channel.

    Restores the original pwmN_enable value that was saved during takeover.
    Returns True on success.
    """
    paths = _resolve_paths(fan_id)
    if not paths:
        return False

    enable_path = paths["enable_path"]
    original = _original_enable.pop(enable_path, None)

    if original is None:
        logger.warning("No saved enable value for %s, skipping release", fan_id)
        return True

    if not _write_file(enable_path, original):
        return False

    logger.info("Released %s, restored pwm_enable to %s", fan_id, original)
    return True


def release_all() -> None:
    """
    Release all fans that were taken over.
    Called during graceful shutdown.
    """
    for enable_path, original in list(_original_enable.items()):
        if _write_file(enable_path, original):
            logger.info("Restored %s to %s", enable_path, original)
        else:
            logger.error("Failed to restore %s to %s", enable_path, original)
    _original_enable.clear()


def set_fan_speed(fan_id: str, percent: int) -> None:
    """
    Set a PWM fan to the given percent (0-100).
    Converts percent to 0-255 range for sysfs.
    Raises RuntimeError if the write fails.
    """
    percent = max(0, min(100, percent))
    pwm_value = round(percent * 255 / 100)

    paths = _resolve_paths(fan_id)
    if not paths:
        raise RuntimeError(f"Cannot resolve fan '{fan_id}' to sysfs path")

    if not _write_file(paths["pwm_path"], str(pwm_value)):
        raise RuntimeError(f"Failed to write PWM value for '{fan_id}'")

    logger.debug("Set %s to %d%% (pwm=%d)", fan_id, percent, pwm_value)


def get_fan_rpm(fan_id: str) -> float | None:
    """
    Read current RPM for a PWM fan channel.
    Returns None if RPM is not available.
    """
    paths = _resolve_paths(fan_id)
    if not paths or not paths["rpm_path"]:
        return None

    raw = _read_file(paths["rpm_path"])
    if raw is None:
        return None

    try:
        return float(raw)
    except ValueError:
        return None