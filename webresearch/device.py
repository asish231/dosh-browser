"""Honest local hardware / device profile.

Reports what this host ACTUALLY is, using only OS-provided facts. Nothing here
is fabricated:

- On bare metal it reports the real CPU/GPU/model.
- In a VM it says ``virtual`` rather than pretending to be bare metal.
- Probes that are unavailable report ``"unknown"`` instead of guessing.

The ``host_profile_id`` is a SHA-256 over real host facts. It is a *host
profile hash*, not a manufacturer attestation and not a hardware-backed
credential -- see ``attestation-lab/ANALYSIS.md`` §5 for why the two differ.
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import subprocess
from dataclasses import asdict, dataclass, field

UNKNOWN = "unknown"


def _sysctl(name: str) -> str:
    try:
        out = subprocess.run(
            ["sysctl", "-n", name], capture_output=True, text=True, timeout=3
        )
        if out.returncode == 0:
            return out.stdout.strip() or UNKNOWN
    except Exception:
        pass
    return UNKNOWN


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except Exception:
        return ""


def _system_profiler(kind: str) -> str:
    try:
        out = subprocess.run(
            ["system_profiler", kind],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return out.stdout if out.returncode == 0 else ""
    except Exception:
        return ""


def _grep_field(text: str, label: str) -> str:
    match = re.search(rf"^\s*{re.escape(label)}:\s*(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else UNKNOWN


# ---- virtualization -------------------------------------------------------

_VM_LINUX_HINTS = (
    "vmware",
    "virtualbox",
    "qemu",
    "kvm",
    "bochs",
    "xen",
    "hyper-v",
    "parallels",
    "virtual machine",
    "innotek",
    "oracle corporation",
    "amazon ec2",
)


def _detect_vm() -> tuple[bool, str]:
    """Return (is_virtual, evidence)."""
    system = platform.system()

    if system == "Darwin":
        vmm = _sysctl("kern.hv_vmm_present")
        if vmm and vmm != UNKNOWN:
            return vmm == "1", f"kern.hv_vmm_present={vmm}"

    if system == "Linux":
        dmi_parts = [
            _read("/sys/class/dmi/id/product_name"),
            _read("/sys/class/dmi/id/sys_vendor"),
            _read("/sys/class/dmi/id/product_version"),
            _read("/sys/class/dmi/id/chassis_asset_tag"),
        ]
        blob = " ".join(p for p in dmi_parts if p).lower()
        for hint in _VM_LINUX_HINTS:
            if hint in blob:
                return True, f"dmi={blob.strip()[:120]}"
        try:
            cpuinfo = _read("/proc/cpuinfo").lower()
            if "hypervisor" in cpuinfo:
                return True, "/proc/cpuinfo hypervisor flag"
        except Exception:
            pass
        if os.path.exists("/dev/kvm"):
            return True, "/dev/kvm present"

    if system == "Windows":
        try:
            out = subprocess.run(
                ["wmic", "computersystem", "get", "model"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            blob = out.stdout.lower()
            if any(h in blob for h in ("virtual", "vmware", "qemu", "xen")):
                return True, f"wmic model={blob.strip()[:80]}"
        except Exception:
            pass

    return False, UNKNOWN


# ---- secure hardware ------------------------------------------------------


def _detect_secure_hardware() -> tuple[str, str]:
    """Return (kind, evidence). kind is one of secure-enclave/tpm/none/unknown."""
    system = platform.system()

    if system == "Darwin":
        # Every Apple Silicon / T2 Mac ships a Secure Enclave.
        chip = _sysctl("machdep.cpu.brand_string")
        if chip and chip != UNKNOWN and "apple" in chip.lower():
            return "secure-enclave", f"Apple SoC ({chip})"
        info = _system_profiler("SPHardwareDataType")
        if "Secure Enclave" in info:
            return "secure-enclave", "SPHardwareDataType reports Secure Enclave"
        return "none", UNKNOWN

    if system == "Linux":
        for path in ("/dev/tpm0", "/dev/tpmrm0"):
            if os.path.exists(path):
                return "tpm", f"{path} present"
        return "none", UNKNOWN

    if system == "Windows":
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Get-Tpm"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if out.returncode == 0 and "TpmPresent" in out.stdout and "True" in out.stdout:
                return "tpm", "Get-Tpm reports present"
        except Exception:
            pass
        return "unknown", UNKNOWN

    return "unknown", UNKNOWN


# ---- gpu ------------------------------------------------------------------


def _detect_gpu() -> str:
    system = platform.system()
    if system == "Darwin":
        text = _system_profiler("SPDisplaysDataType")
        chipset = _grep_field(text, "Chipset Model")
        if chipset != UNKNOWN:
            return f"Apple GPU ({chipset})"
        return UNKNOWN
    if system == "Linux":
        for path in (
            "/sys/class/drm/card0/device/uevent",
        ):
            blob = _read(path)
            match = re.search(r"DRIVER=(\S+)", blob)
            if match:
                return match.group(1)
        return UNKNOWN
    return UNKNOWN


# ---- profile --------------------------------------------------------------


@dataclass(slots=True)
class DeviceProfile:
    platform: str = UNKNOWN
    os_version: str = UNKNOWN
    cpu_brand: str = UNKNOWN
    cpu_count: int | None = None
    machine_model: str = UNKNOWN
    gpu: str = UNKNOWN
    secure_hardware: str = UNKNOWN
    secure_hardware_evidence: str = UNKNOWN
    is_virtual: bool = False
    virtualization_evidence: str = UNKNOWN
    host_profile_id: str = UNKNOWN
    extra: dict = field(default_factory=dict)

    @property
    def is_real_device(self) -> bool:
        """True when running on bare metal with recognized secure hardware."""
        return (not self.is_virtual) and self.secure_hardware in {
            "secure-enclave",
            "tpm",
        }

    def to_dict(self) -> dict:
        data = asdict(self)
        data["is_real_device"] = self.is_real_device
        return data


def probe() -> DeviceProfile:
    """Probe the current host. Never raises; unknown fields stay 'unknown'."""
    system = platform.system()
    is_virtual, vm_evidence = _detect_vm()
    secure_kind, secure_evidence = _detect_secure_hardware()

    cpu_brand = platform.processor() or UNKNOWN
    if system == "Darwin":
        chip = _sysctl("machdep.cpu.brand_string")
        if chip != UNKNOWN:
            cpu_brand = chip

    machine_model = UNKNOWN
    if system == "Darwin":
        info = _system_profiler("SPHardwareDataType")
        machine_model = _grep_field(info, "Model Identifier")
        if machine_model == UNKNOWN:
            machine_model = _grep_field(info, "Model Name")
    elif system == "Linux":
        machine_model = (
            _read("/sys/class/dmi/id/product_name").strip()
            or _read("/sys/class/dmi/id/sys_vendor").strip()
            or UNKNOWN
        )

    profile = DeviceProfile(
        platform=system or UNKNOWN,
        os_version=platform.release() or UNKNOWN,
        cpu_brand=cpu_brand or UNKNOWN,
        cpu_count=os.cpu_count(),
        machine_model=machine_model,
        gpu=_detect_gpu(),
        secure_hardware=secure_kind,
        secure_hardware_evidence=secure_evidence,
        is_virtual=is_virtual,
        virtualization_evidence=vm_evidence,
    )

    # Host profile hash over REAL facts only. This is NOT an attestation.
    seed = "|".join(
        [
            profile.platform,
            profile.machine_model,
            profile.cpu_brand,
            str(profile.cpu_count or ""),
            profile.gpu,
            profile.secure_hardware,
            str(profile.is_virtual),
        ]
    )
    profile.host_profile_id = hashlib.sha256(seed.encode()).hexdigest()[:32]
    return profile


_CACHED: DeviceProfile | None = None


def current() -> DeviceProfile:
    global _CACHED
    if _CACHED is None:
        _CACHED = probe()
    return _CACHED


def reset_cache() -> None:
    global _CACHED
    _CACHED = None