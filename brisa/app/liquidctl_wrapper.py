import json
import logging
import re
import subprocess

logger = logging.getLogger(__name__)


def _run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a liquidctl command and return the completed process."""
    cmd = ["liquidctl"] + args
    logger.debug("Running: %s", " ".join(cmd))
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=check,
    )


def initialize() -> None:
    """
    Run `liquidctl initialize` on all detected devices.
    Called once at startup before the first control loop iteration.
    """
    try:
        result = _run(["--direct-access", "initialize", "all"])
        logger.info("liquidctl initialize complete")
        if result.stdout:
            logger.debug("initialize stdout: %s", result.stdout.strip())
    except subprocess.CalledProcessError as e:
        logger.error("liquidctl initialize failed: %s", e.stderr.strip())
        raise RuntimeError(f"liquidctl initialize failed: {e.stderr.strip()}") from e


def list_devices() -> list[dict]:
    """
    Run `liquidctl list --json` and return detected devices.

    Returns a list of dicts:
        {
            "id": "0",
            "description": "Aquacomputer Quadro (experimental)"
        }
    """
    try:
        result = _run(["list", "--json"])
        devices_raw = json.loads(result.stdout)
        devices = [
            {
                "id": str(d.get("device_id", i)),
                "description": d.get("description", "Unknown"),
            }
            for i, d in enumerate(devices_raw)
        ]
        logger.info("Detected %d liquidctl device(s)", len(devices))
        return devices
    except subprocess.CalledProcessError as e:
        logger.error("liquidctl list failed: %s", e.stderr.strip())
        raise RuntimeError(f"liquidctl list failed: {e.stderr.strip()}") from e
    except (json.JSONDecodeError, KeyError) as e:
        logger.error("Failed to parse liquidctl list output: %s", e)
        raise RuntimeError(f"Failed to parse liquidctl list output: {e}") from e


def get_fan_status() -> list[dict]:
    """
    Run `liquidctl status --json` and return fan channels with current RPM.

    Returns a list of dicts:
        {
            "id": "fan1",
            "label": "Fan 1",
            "current_rpm": 377.0
        }
    """
    try:
        result = _run(["--direct-access", "status", "--json"])
        status_raw = json.loads(result.stdout)
    except subprocess.CalledProcessError as e:
        logger.error("liquidctl status failed: %s", e.stderr.strip())
        raise RuntimeError(f"liquidctl status failed: {e.stderr.strip()}") from e
    except json.JSONDecodeError as e:
        logger.error("Failed to parse liquidctl status output: %s", e)
        raise RuntimeError(f"Failed to parse liquidctl status output: {e}") from e

    fans = []

    for device in status_raw:
        for entry in device.get("status", []):
            key = entry.get("key", "")
            # Match "Fan N speed" entries
            match = re.match(r"^Fan (\d+) speed$", key, re.IGNORECASE)
            if match:
                n = match.group(1)
                fans.append({
                    "id": f"fan{n}",
                    "label": f"Fan {n}",
                    "current_rpm": float(entry.get("value", 0)),
                })

    logger.debug("Found %d fan channel(s) via liquidctl", len(fans))
    return fans


def set_fan_speed(fan_id: str, percent: int) -> None:
    """
    Run `liquidctl set <fan_id> speed <percent>`.
    fan_id is expected to be in the form "fan1", "fan2", etc.
    Raises RuntimeError if the command fails.
    """
    percent = max(0, min(100, percent))
    try:
        _run(["--direct-access", "set", fan_id, "speed", str(percent)])
        logger.debug("Set %s to %d%%", fan_id, percent)
    except subprocess.CalledProcessError as e:
        logger.error("Failed to set %s speed: %s", fan_id, e.stderr.strip())
        raise RuntimeError(
            f"Failed to set {fan_id} speed to {percent}%: {e.stderr.strip()}"
        ) from e
