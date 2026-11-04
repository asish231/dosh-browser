use sha2::{Digest, Sha256};
use serde::{Deserialize, Serialize};
use std::process::Command;

pub const UNKNOWN: &str = "unknown";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeviceProfile {
    pub platform: String,
    pub cpu_brand: String,
    pub cpu_count: usize,
    pub machine_model: String,
    pub gpu_renderer: String,
    pub gpu_vendor: String,
    pub is_virtual: bool,
    pub is_real_device: bool,
    pub host_profile_id: String,
}

impl Default for DeviceProfile {
    fn default() -> Self {
        Self {
            platform: "Darwin".to_string(),
            cpu_brand: "Apple M-Series".to_string(),
            cpu_count: 8,
            machine_model: "MacBookPro".to_string(),
            gpu_renderer: "ANGLE (Apple, Metal)".to_string(),
            gpu_vendor: "Google Inc. (Apple)".to_string(),
            is_virtual: false,
            is_real_device: true,
            host_profile_id: String::new(),
        }
    }
}

pub fn detect_device_profile() -> DeviceProfile {
    let os = std::env::consts::OS;
    let mut cpu_brand = UNKNOWN.to_string();
    let mut machine_model = UNKNOWN.to_string();
    let mut is_virtual = false;

    if os == "macos" {
        if let Ok(out) = Command::new("sysctl").args(["-n", "machdep.cpu.brand_string"]).output() {
            if out.status.success() {
                let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
                if !s.is_empty() {
                    cpu_brand = s;
                }
            }
        }
        if let Ok(out) = Command::new("sysctl").args(["-n", "hw.model"]).output() {
            if out.status.success() {
                let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
                if !s.is_empty() {
                    machine_model = s;
                }
            }
        }
        if let Ok(out) = Command::new("sysctl").args(["-n", "kern.hv_vmm_present"]).output() {
            if out.status.success() {
                let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
                is_virtual = s == "1";
            }
        }
    }

    if cpu_brand == UNKNOWN {
        cpu_brand = if cfg!(target_arch = "aarch64") {
            "Apple M-Series".to_string()
        } else {
            "x86_64 Processor".to_string()
        };
    }

    if machine_model == UNKNOWN {
        machine_model = "MacBookPro".to_string();
    }

    let cpu_count = std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(8);

    let platform_name = match os {
        "macos" => "Darwin",
        "linux" => "Linux",
        "windows" => "Windows",
        _ => "Unknown",
    };

    let mut hasher = Sha256::new();
    hasher.update(platform_name.as_bytes());
    hasher.update(cpu_brand.as_bytes());
    hasher.update(machine_model.as_bytes());
    hasher.update(&(cpu_count as u64).to_le_bytes());
    let host_profile_id = hex::encode(hasher.finalize());

    DeviceProfile {
        platform: platform_name.to_string(),
        cpu_brand,
        cpu_count,
        machine_model,
        gpu_renderer: "ANGLE (Apple, Metal)".to_string(),
        gpu_vendor: "Google Inc. (Apple)".to_string(),
        is_virtual,
        is_real_device: !is_virtual,
        host_profile_id,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_detect_device_profile() {
        let p = detect_device_profile();
        assert!(!p.platform.is_empty());
        assert!(!p.cpu_brand.is_empty());
        assert!(p.cpu_count > 0);
        assert!(!p.host_profile_id.is_empty());
    }
}

