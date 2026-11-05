import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from webresearch import device


class DeviceProbeTests(unittest.TestCase):
    def test_probe_returns_structured_profile(self):
        profile = device.probe()
        self.assertIn(profile.platform, {"Darwin", "Linux", "Windows", "unknown"})
        self.assertTrue(profile.platform)
        # No field may be empty; unknown is the sentinel.
        for field in (
            profile.os_version,
            profile.cpu_brand,
            profile.machine_model,
            profile.gpu,
            profile.secure_hardware,
            profile.virtualization_evidence,
        ):
            self.assertTrue(field)

    def test_host_profile_id_is_stable_and_derived(self):
        first = device.probe()
        second = device.probe()
        self.assertEqual(first.host_profile_id, second.host_profile_id)
        self.assertEqual(len(first.host_profile_id), 32)
        self.assertTrue(all(c in "0123456789abcdef" for c in first.host_profile_id))

    def test_is_virtual_is_bool_and_real_device_requires_not_virtual(self):
        profile = device.probe()
        self.assertIsInstance(profile.is_virtual, bool)
        if profile.is_virtual:
            self.assertFalse(profile.is_real_device)

    def test_secure_hardware_detection(self):
        profile = device.probe()
        self.assertIn(
            profile.secure_hardware, {"secure-enclave", "tpm", "none", "unknown"}
        )

    def test_to_dict_includes_real_device_flag(self):
        data = device.probe().to_dict()
        self.assertIn("is_real_device", data)
        self.assertIn("host_profile_id", data)

    def test_darwin_probe_detects_this_machine(self):
        # Only assert real values when actually running on this macOS host.
        import platform as _p

        if _p.system() != "Darwin":
            self.skipTest("not macOS")
        profile = device.probe()
        self.assertEqual(profile.platform, "Darwin")
        # Apple Silicon has a Secure Enclave and is not a VM here.
        if "Apple" in profile.cpu_brand:
            self.assertEqual(profile.secure_hardware, "secure-enclave")


class DeviceCacheTests(unittest.TestCase):
    """Disk cache: reuses the profile when the machine is unchanged, re-probes otherwise."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name) / "device-cache.json"
        self.env = mock.patch.dict(os.environ, {"WR_DEVICE_CACHE": str(self.cache)})
        self.env.start()
        self.addCleanup(self.env.stop)
        device.reset_cache()

    def _fake_probe(self) -> device.DeviceProfile:
        return device.DeviceProfile(
            platform="Darwin",
            os_version="24.0",
            cpu_brand="Apple fake",
            cpu_count=8,
            machine_model="MacBookPro fake",
            gpu="Apple GPU (fake)",
            secure_hardware="secure-enclave",
            secure_hardware_evidence="Apple SoC (fake)",
            is_virtual=False,
            virtualization_evidence="unknown",
            host_profile_id="x" * 32,
        )

    def test_second_current_reuses_disk_cache_without_reprobe(self):
        calls = {"n": 0}

        def fake_probe() -> device.DeviceProfile:
            calls["n"] += 1
            return self._fake_probe()

        with mock.patch.object(device, "probe", side_effect=fake_probe):
            first = device.current()
            device.reset_cache()  # new process: drop the in-memory cache only
            second = device.current()
        # The disk cache re-derives host_profile_id honestly, so compare the
        # facts that matter and the probe count, not the sentinel id.
        self.assertEqual(first.machine_model, second.machine_model)
        self.assertEqual(first.cpu_brand, second.cpu_brand)
        self.assertEqual(first.gpu, second.gpu)
        self.assertEqual(calls["n"], 1)  # only the first current() probes; the disk HIT reuses it
        self.assertTrue(self.cache.exists())

    def test_changed_machine_triggers_reprobe(self):
        calls = {"n": 0}

        def fake_probe() -> device.DeviceProfile:
            calls["n"] += 1
            return self._fake_probe()

        with mock.patch.object(device, "probe", side_effect=fake_probe):
            first = device.current()
            device.reset_cache()
            # Same OS but a different CPU count => machine changed => re-probe.
            with mock.patch.object(
                device, "_cheap_identity",
                return_value=("Darwin", "arm64", "24.0", 16, "Apple fake"),
            ):
                second = device.current()
        self.assertEqual(calls["n"], 2)  # identity changed => full re-probe
        self.assertEqual(first, second)

    def test_corrupt_cache_is_ignored_not_fatal(self):
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        self.cache.write_text("{ not json !!!", encoding="utf-8")
        with mock.patch.object(device, "probe", return_value=self._fake_probe()) as m:
            profile = device.current()
        m.assert_called_once()
        self.assertEqual(profile.cpu_brand, "Apple fake")

    def test_stale_version_is_ignored(self):
        import dataclasses
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        good = self._fake_probe()
        payload = {"version": 0, "identity": list(device._cheap_identity()), "profile": dataclasses.asdict(good)}
        self.cache.write_text(json.dumps(payload), encoding="utf-8")
        with mock.patch.object(device, "probe", return_value=good) as m:
            device.current()
        m.assert_called_once()

    def test_unknown_fields_in_cache_are_filtered(self):
        import dataclasses
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        good = self._fake_probe()
        blob = dataclasses.asdict(good)
        blob["made_up_field"] = "hacker"
        payload = {"version": device._CACHE_VERSION, "identity": list(device._cheap_identity()), "profile": blob}
        self.cache.write_text(json.dumps(payload), encoding="utf-8")
        with mock.patch.object(device, "probe", side_effect=AssertionError("should not re-probe")):
            profile = device.current()
        self.assertEqual(profile.cpu_brand, "Apple fake")


if __name__ == "__main__":
    unittest.main()