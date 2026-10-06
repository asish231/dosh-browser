import unittest

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


if __name__ == "__main__":
    unittest.main()