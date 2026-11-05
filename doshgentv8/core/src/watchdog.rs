//! `watchdog.rs` — Process-wide supervisor thread for execution deadlines.
//!
//! Replaces per-eval OS thread spawning (`thread::spawn`) with a single
//! dedicated supervisor thread waiting on a `Condvar` until the nearest deadline.
//!
//! Arming returns a `WatchdogToken`. Dropping or calling `disarm()` flags
//! the token in O(1) without requiring locks or OS thread termination.

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Condvar, Mutex, OnceLock};
use std::time::{Duration, Instant};

/// An armed execution watchdog token.
///
/// When dropped or explicitly disarmed, the watchdog entry is neutralized in O(1).
pub struct WatchdogToken {
    id: u64,
    disarmed: Arc<AtomicBool>,
}

impl WatchdogToken {
    /// Disarm the watchdog immediately.
    #[inline]
    pub fn disarm(&self) {
        self.disarmed.store(true, Ordering::Release);
    }

    /// Check if the token has been disarmed.
    #[inline]
    pub fn is_disarmed(&self) -> bool {
        self.disarmed.load(Ordering::Acquire)
    }

    #[inline]
    pub fn id(&self) -> u64 {
        self.id
    }
}

impl Drop for WatchdogToken {
    fn drop(&mut self) {
        self.disarm();
    }
}

struct WatchdogEntry {
    _id: u64,
    deadline: Instant,
    handle: v8::IsolateHandle,
    disarmed: Arc<AtomicBool>,
}

struct SupervisorState {
    entries: Vec<WatchdogEntry>,
    shutdown: bool,
}

pub struct WatchdogSupervisor {
    state: Mutex<SupervisorState>,
    cv: Condvar,
    next_id: AtomicU64,
}

impl WatchdogSupervisor {
    fn new() -> Self {
        Self {
            state: Mutex::new(SupervisorState {
                entries: Vec::new(),
                shutdown: false,
            }),
            cv: Condvar::new(),
            next_id: AtomicU64::new(1),
        }
    }

    /// Global singleton instance. Spawns the supervisor thread on first access.
    pub fn global() -> &'static Self {
        static INSTANCE: OnceLock<WatchdogSupervisor> = OnceLock::new();
        INSTANCE.get_or_init(|| {
            let supervisor = WatchdogSupervisor::new();
            // Start the background supervisor thread
            std::thread::Builder::new()
                .name("dosh-watchdog".to_string())
                .spawn(move || {
                    let supervisor = WatchdogSupervisor::global();
                    supervisor.run_loop();
                })
                .expect("failed to spawn dosh-watchdog thread");
            supervisor
        })
    }

    /// Arm a deadline for an isolate handle.
    pub fn arm_deadline(&self, handle: v8::IsolateHandle, timeout: Duration) -> WatchdogToken {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let disarmed = Arc::new(AtomicBool::new(false));
        let deadline = Instant::now() + timeout;

        let entry = WatchdogEntry {
            _id: id,
            deadline,
            handle,
            disarmed: disarmed.clone(),
        };

        let mut state = self.state.lock().expect("watchdog lock poisoned");

        // Clean up disarmed entries lazily
        state.entries.retain(|e| !e.disarmed.load(Ordering::Relaxed));

        // Insert sorted by deadline ascending
        let pos = state
            .entries
            .binary_search_by_key(&deadline, |e| e.deadline)
            .unwrap_or_else(|idx| idx);
        state.entries.insert(pos, entry);

        // If this entry is at the front, notify supervisor to recalibrate sleep time
        if pos == 0 {
            self.cv.notify_one();
        }

        WatchdogToken { id, disarmed }
    }

    /// Supervisor background event loop.
    fn run_loop(&self) {
        loop {
            let mut state = self.state.lock().expect("watchdog lock poisoned");

            // Clean up any neutralized entries
            state.entries.retain(|e| !e.disarmed.load(Ordering::Relaxed));

            if state.shutdown {
                break;
            }

            if state.entries.is_empty() {
                // Wait indefinitely until a new deadline arrives
                state = self.cv.wait(state).expect("watchdog lock poisoned");
            } else {
                let earliest_deadline = state.entries[0].deadline;
                let now = Instant::now();

                if now >= earliest_deadline {
                    // Entry has reached its deadline
                    let entry = state.entries.remove(0);
                    // Check if not already disarmed
                    if !entry.disarmed.swap(true, Ordering::AcqRel) {
                        // Terminate V8 execution
                        entry.handle.terminate_execution();
                    }
                } else {
                    let wait_duration = earliest_deadline - now;
                    let (new_state, _) = self
                        .cv
                        .wait_timeout(state, wait_duration)
                        .expect("watchdog lock poisoned");
                    state = new_state;
                }
            }
        }
    }

    /// Number of currently active deadlines (for diagnostics/tests).
    pub fn active_count(&self) -> usize {
        let state = self.state.lock().expect("watchdog lock poisoned");
        state
            .entries
            .iter()
            .filter(|e| !e.disarmed.load(Ordering::Relaxed))
            .count()
    }
}

/// Convenience function to arm a watchdog for an isolate handle using the process-wide supervisor.
#[inline]
pub fn arm(handle: v8::IsolateHandle, timeout: Duration) -> WatchdogToken {
    WatchdogSupervisor::global().arm_deadline(handle, timeout)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_watchdog_arm_and_disarm() {
        let isolate = v8::Isolate::new(v8::CreateParams::default());
        let handle = isolate.thread_safe_handle();

        let token = arm(handle, Duration::from_millis(500));
        assert!(!token.is_disarmed());
        token.disarm();
        assert!(token.is_disarmed());
    }

    #[test]
    fn test_watchdog_drop_disarms() {
        let isolate = v8::Isolate::new(v8::CreateParams::default());
        let handle = isolate.thread_safe_handle();

        let disarmed_flag;
        {
            let token = arm(handle, Duration::from_secs(10));
            disarmed_flag = token.disarmed.clone();
            assert!(!disarmed_flag.load(Ordering::Relaxed));
        }
        // Dropped here
        assert!(disarmed_flag.load(Ordering::Relaxed));
    }
}
