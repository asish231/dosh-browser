//! `rate_limiter.rs` — Origin-Aware Rate Limiter (GCRA) for Dosh-Browser.
//!
//! Implements the Generic Cell Rate Algorithm (GCRA) / Leaky Bucket per hostname.
//! Protects against 429 Too Many Requests and IP bans by enforcing an emission gap
//! and maximum burst allowance for outgoing HTTP requests.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::Mutex;
use tokio::time::sleep;

/// A per-origin rate limiter using GCRA.
#[derive(Clone)]
pub struct RateLimiter {
    inner: Arc<Mutex<RateLimiterInner>>,
}

struct RateLimiterInner {
    interval: Duration,
    tau: Duration,
    interval_ms: u64,
    tau_ms: u64,
    // host -> Theoretical Arrival Time (TAT)
    hosts: HashMap<String, Instant>,
    // host -> TAT in epoch ms
    hosts_ms: HashMap<String, u64>,
    last_sweep: Instant,
    sweep_interval: Duration,
    sweep_at: usize,
}

impl RateLimiter {
    /// Create a new RateLimiter.
    /// - `interval_ms`: Minimum target delay between consecutive requests to the same origin.
    /// - `burst`: Maximum burst of requests allowed before rate-limiting forces a delay.
    pub fn new(interval_ms: u64, burst: u32) -> Self {
        let interval = Duration::from_millis(interval_ms);
        let burst_count = burst.max(1);
        let tau = interval * (burst_count - 1);
        let tau_ms = interval_ms * ((burst_count - 1) as u64);

        Self {
            inner: Arc::new(Mutex::new(RateLimiterInner {
                interval,
                tau,
                interval_ms,
                tau_ms,
                hosts: HashMap::new(),
                hosts_ms: HashMap::new(),
                last_sweep: Instant::now(),
                sweep_interval: Duration::from_secs(60),
                sweep_at: 256,
            })),
        }
    }

    /// GCRA slot reservation algorithm: returns theoretical earliest timestamp (in ms)
    /// that the request is permitted to run.
    pub async fn reserve(&self, host: &str, now_ms: u64) -> u64 {
        let clean_host = host.split(':').next().unwrap_or(host).to_lowercase();
        let mut inner = self.inner.lock().await;

        if let Some(&tat) = inner.hosts_ms.get(&clean_host) {
            let start = now_ms.max(tat.saturating_sub(inner.tau_ms));
            let next_tat = tat.max(start) + inner.interval_ms;
            inner.hosts_ms.insert(clean_host, next_tat);
            start
        } else {
            let next_tat = now_ms + inner.interval_ms;
            inner.hosts_ms.insert(clean_host, next_tat);

            if inner.hosts_ms.len() >= inner.sweep_at {
                inner.sweep_ms(now_ms);
            }
            now_ms
        }
    }

    /// Check if a request to `host` can proceed now, or how long it must wait.
    /// If it must wait and `auto_wait` is desired, use `acquire`.
    pub async fn check(&self, host: &str) -> Result<(), Duration> {
        let clean_host = host.split(':').next().unwrap_or(host).to_lowercase();
        let mut inner = self.inner.lock().await;
        let now = Instant::now();

        // Perform periodic idle sweeps
        if now.duration_since(inner.last_sweep) >= inner.sweep_interval {
            inner.sweep(now);
        }

        let tat = inner.hosts.get(&clean_host).copied().unwrap_or(now);

        // If TAT - now > tau, request violates rate limit
        if tat > now + inner.tau {
            let wait = (tat - inner.tau).duration_since(now);
            return Err(wait);
        }

        let new_tat = tat.max(now) + inner.interval;
        inner.hosts.insert(clean_host, new_tat);
        Ok(())
    }

    /// Acquire clearance to send a request to `host`.
    /// If throttled, sleeps for the required delay and returns the duration waited.
    pub async fn acquire(&self, host: &str) -> Duration {
        loop {
            match self.check(host).await {
                Ok(()) => return Duration::ZERO,
                Err(wait_dur) => {
                    sleep(wait_dur).await;
                    // Re-check after sleeping to guarantee compliance
                    let mut inner = self.inner.lock().await;
                    let now = Instant::now();
                    let tat = inner.hosts.get(host).copied().unwrap_or(now);
                    let new_tat = tat.max(now) + inner.interval;
                    inner.hosts.insert(host.to_string(), new_tat);
                    return wait_dur;
                }
            }
        }
    }
}

impl RateLimiterInner {
    fn sweep(&mut self, now: Instant) {
        let max_age = self.tau + Duration::from_secs(30);
        self.hosts.retain(|_, tat| {
            if now > *tat {
                now.duration_since(*tat) < max_age
            } else {
                true
            }
        });
        self.last_sweep = now;
    }

    fn sweep_ms(&mut self, now_ms: u64) {
        self.hosts_ms.retain(|_, &mut tat| tat > now_ms);
        if self.hosts_ms.len() >= self.sweep_at {
            self.sweep_at *= 2;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_burst_allowed_immediately() {
        // 50ms interval, burst of 3
        let rl = RateLimiter::new(50, 3);
        let host = "api.example.com";

        // First 3 requests should succeed with 0 delay (burst capacity)
        assert!(rl.check(host).await.is_ok());
        assert!(rl.check(host).await.is_ok());
        assert!(rl.check(host).await.is_ok());

        // 4th request exceeds burst capacity and must wait
        let res = rl.check(host).await;
        assert!(res.is_err());
        let wait = res.unwrap_err();
        assert!(wait.as_millis() >= 20);
    }

    #[tokio::test]
    async fn test_multi_host_isolation() {
        let rl = RateLimiter::new(50, 1);
        let host1 = "host1.com";
        let host2 = "host2.com";

        // Saturate host1
        assert!(rl.check(host1).await.is_ok());
        assert!(rl.check(host1).await.is_err());

        // Host2 should still be completely unblocked
        assert!(rl.check(host2).await.is_ok());
    }

    #[tokio::test]
    async fn test_acquire_sleeps_and_proceeds() {
        let rl = RateLimiter::new(30, 1);
        let host = "service.org";

        let start = Instant::now();
        // First immediate
        let w1 = rl.acquire(host).await;
        assert_eq!(w1, Duration::ZERO);

        // Second must sleep ~30ms
        let w2 = rl.acquire(host).await;
        assert!(w2.as_millis() >= 25);
        assert!(start.elapsed().as_millis() >= 25);
    }

    #[tokio::test]
    async fn test_reserve_gcra() {
        let rl = RateLimiter::new(100, 1);
        let host = "a.test:443";

        // Idle host starts immediately
        assert_eq!(rl.reserve(host, 1000).await, 1000);
        // Second request at same timestamp is spaced by 100ms
        assert_eq!(rl.reserve(host, 1000).await, 1100);
        // Third request at 1050ms is spaced to 1200ms
        assert_eq!(rl.reserve(host, 1050).await, 1200);

        // Different host operates independently
        assert_eq!(rl.reserve("b.test", 1000).await, 1000);
    }
}

