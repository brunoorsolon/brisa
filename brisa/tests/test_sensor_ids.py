"""Regression tests for reboot-stable sensor IDs (issue #7).

The kernel assigns hwmon directory numbers (hwmonN) at boot and can renumber
them across reboots. Sensor IDs must not depend on them, and a config saved
with an older hwmonN must be repaired on load instead of being rejected as
referencing an unknown sensor.

Run from the brisa/ directory:

    python3 -m unittest discover -s tests -v
"""
import os
import shutil
import tempfile
import unittest

from app import config as config_mod
from app import sensors
from app.models import AppConfig, DashboardGroup, FanConfig, VirtualSensor


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


class SensorIdTests(unittest.TestCase):
    """detect_sensors()/migrate_sensor_ids() against a fake /sys tree."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "sys")
        os.makedirs(os.path.join(self.root, "class/block"))
        self._hwmon_path = sensors.HWMON_PATH
        self._block_path = sensors.BLOCK_PATH
        sensors.HWMON_PATH = os.path.join(self.root, "class/hwmon")
        sensors.BLOCK_PATH = os.path.join(self.root, "class/block")

    def tearDown(self):
        sensors.HWMON_PATH = self._hwmon_path
        sensors.BLOCK_PATH = self._block_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add_hwmon(self, hwmon_name, device_rel, driver, temp_label):
        """Create one device under <root>/<device_rel> and link it into class/hwmon."""
        dev = os.path.join(self.root, device_rel)
        _write(os.path.join(dev, "name"), driver + "\n")
        _write(os.path.join(dev, "temp1_input"), "35000\n")
        _write(os.path.join(dev, "temp1_label"), temp_label + "\n")
        link_dir = sensors.HWMON_PATH
        os.makedirs(link_dir, exist_ok=True)
        link = os.path.join(link_dir, hwmon_name)
        if os.path.lexists(link):
            os.unlink(link)
        os.symlink(os.path.relpath(dev, link_dir), link)

    def add_nct6775(self, hwmon_name):
        self.add_hwmon(hwmon_name, "devices/platform/nct6775.656/hwmon/" + hwmon_name,
                       "nct6798", "SYSTIN")

    def add_coretemp(self, hwmon_name="hwmon0"):
        self.add_hwmon(hwmon_name, "devices/platform/coretemp.0/hwmon/" + hwmon_name,
                       "coretemp", "Package id 0")

    def detected_ids(self):
        return sorted(s["id"] for s in sensors.detect_sensors(include_smartctl=False))

    def config_with(self, stale_id):
        return AppConfig(
            fan_configs=[FanConfig(fan_id="hwmon-pwm-nct6775.656/pwm1", fan_label="Case",
                                   curve_name="silent", sensor_id=stale_id,
                                   backend="hwmon-pwm")],
            sensor_aliases={stale_id: "System"},
            virtual_sensors=[VirtualSensor(id="virtual/x", name="X",
                                           source_sensor_ids=[stale_id, "coretemp-coretemp.0/Package id 0"],
                                           aggregation="max")],
            dashboard_groups=[DashboardGroup(id="g1", name="CPU", type="sensor",
                                             item_ids=[stale_id])],
            card_colors={stale_id: "teal"},
        )

    def unknown_sensor_errors(self, cfg):
        errors = config_mod.validate_config(cfg, self.detected_ids(), [])
        return [e for e in errors if "unknown sensor" in e]

    def test_sensor_id_does_not_contain_hwmon_number(self):
        self.add_nct6775("hwmon10")
        self.assertEqual(self.detected_ids(), ["nct6798-nct6775.656/SYSTIN"])

    def test_sensor_id_survives_hwmon_renumbering(self):
        self.add_nct6775("hwmon10")
        before = self.detected_ids()
        self.add_nct6775("hwmon12")  # same device, renumbered by the kernel
        os.unlink(os.path.join(sensors.HWMON_PATH, "hwmon10"))
        self.assertEqual(self.detected_ids(), before)

    def test_pci_only_device_falls_back_to_hwmon_scope(self):
        self.add_hwmon("hwmon1", "devices/pci0000:00/0000:01:00.0/nvme/nvme0/hwmon/hwmon1",
                       "nvme", "Composite")
        self.assertEqual(self.detected_ids(), ["nvme-hwmon1/Composite"])

    def test_stale_hwmon_id_in_config_is_migrated(self):
        self.add_nct6775("hwmon12")
        self.add_coretemp()
        cfg = self.config_with("nct6798-hwmon10/SYSTIN")
        self.assertTrue(self.unknown_sensor_errors(cfg), "stale config should be rejected")

        config_mod.migrate_sensor_ids(cfg)

        stable = "nct6798-nct6775.656/SYSTIN"
        self.assertEqual(cfg.fan_configs[0].sensor_id, stable)
        self.assertEqual(cfg.virtual_sensors[0].source_sensor_ids[0], stable)
        self.assertEqual(cfg.dashboard_groups[0].item_ids, [stable])
        self.assertEqual(list(cfg.sensor_aliases), [stable])
        self.assertEqual(list(cfg.card_colors), [stable])
        self.assertFalse(self.unknown_sensor_errors(cfg), "migrated config should validate")

    def test_migration_is_idempotent(self):
        self.add_nct6775("hwmon12")
        cfg = self.config_with("nct6798-hwmon10/SYSTIN")
        config_mod.migrate_sensor_ids(cfg)
        self.assertEqual(config_mod.migrate_sensor_ids(cfg)[1], 0)

    def test_ambiguous_label_is_not_guessed(self):
        self.add_hwmon("hwmon1", "devices/pci0000:00/0000:01:00.0/nvme/nvme0/hwmon/hwmon1",
                       "nvme", "Composite")
        self.add_hwmon("hwmon2", "devices/pci0000:00/0000:02:00.0/nvme/nvme1/hwmon/hwmon2",
                       "nvme", "Composite")
        cfg = self.config_with("nvme-hwmon9/Composite")
        config_mod.migrate_sensor_ids(cfg)
        self.assertEqual(cfg.fan_configs[0].sensor_id, "nvme-hwmon9/Composite")

    def test_old_drivetemp_id_is_still_migrated(self):
        cfg = self.config_with("drivetemp-wwid-naa.50014ee2c1c21634/sda \u2014 WDC WD120EFGX-68")
        config_mod.migrate_sensor_ids(cfg)
        self.assertEqual(cfg.fan_configs[0].sensor_id,
                         "drivetemp-wwid-naa.50014ee2c1c21634/WDC WD120EFGX-68")


if __name__ == "__main__":
    unittest.main()
