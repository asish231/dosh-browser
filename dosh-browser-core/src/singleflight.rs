//! `singleflight.rs` — Request Coalescing and Deduplication for Dosh-Browser.
//!
//! When multiple tabs or subresources (scripts, images, stylesheets, fonts) attempt
//! to fetch the identical URL concurrently, `SingleFlight` collapses them into a single
//! in-flight network transfer. All callers await the single future and receive a clone
//! of the result, eliminating duplicate bandwidth and CPU consumption.

use std::collections::HashMap;
use std::future::Future;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use tokio::sync::{broadcast, Mutex};

/// A single-flight request coalescing coordinator.
#[derive(Clone)]
pub struct SingleFlight<V, E>
where
    V: Clone + Send + Sync + 'static,
    E: Clone + Send + Sync + 'static,
{
    in_flight: Arc<Mutex<HashMap<String, broadcast::Sender<Result<V, E>>>>>,
    total_calls: Arc<AtomicU64>,
    coalesced_calls: Arc<AtomicU64>,
}

impl<V, E> Default for SingleFlight<V, E>
where
    V: Clone + Send + Sync + 'static,
    E: Clone + Send + Sync + 'static,
{
    fn default() -> Self {
        Self::new()
    }
}

impl<V, E> SingleFlight<V, E>
where
    V: Clone + Send + Sync + 'static,
    E: Clone + Send + Sync + 'static,
{
    /// Create a new SingleFlight coordinator.
    pub fn new() -> Self {
        Self {
            in_flight: Arc::new(Mutex::new(HashMap::new())),
            total_calls: Arc::new(AtomicU64::new(0)),
            coalesced_calls: Arc::new(AtomicU64::new(0)),
        }
    }

    /// Total number of requests dispatched through this coordinator.
    pub fn total_calls(&self) -> u64 {
        self.total_calls.load(Ordering::Relaxed)
    }

    /// Number of redundant requests that were coalesced into an existing in-flight transfer.
    pub fn coalesced_calls(&self) -> u64 {
        self.coalesced_calls.load(Ordering::Relaxed)
    }

    /// Execute `f` if no request for `key` is currently in flight. If a request for `key`
    /// is already running, wait for it and return its result without calling `f`.
    pub async fn execute<F, Fut>(&self, key: &str, f: F) -> Result<V, E>
    where
        F: FnOnce() -> Fut,
        Fut: Future<Output = Result<V, E>>,
    {
        self.total_calls.fetch_add(1, Ordering::Relaxed);

        let mut rx = {
            let mut map = self.in_flight.lock().await;
            if let Some(tx) = map.get(key) {
                self.coalesced_calls.fetch_add(1, Ordering::Relaxed);
                tx.subscribe()
            } else {
                let (tx, _) = broadcast::channel(1);
                map.insert(key.to_string(), tx);
                drop(map);

                // This task is the primary driver for this key
                let result = f().await;

                let mut map = self.in_flight.lock().await;
                if let Some(tx) = map.remove(key) {
                    // Notify any coalesced callers that arrived while f was running
                    let _ = tx.send(result.clone());
                }
                return result;
            }
        };

        // Awaiting the primary driver's result
        match rx.recv().await {
            Ok(res) => res,
            Err(_) => {
                // If the broadcast channel closed prematurely, re-run directly as fallback
                f().await
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::AtomicUsize;
    use std::time::Duration;
    use tokio::time::sleep;

    #[tokio::test]
    async fn test_singleflight_coalescing() {
        let sf = SingleFlight::<String, String>::new();
        let counter = Arc::new(AtomicUsize::new(0));

        let run_request = |sf: SingleFlight<String, String>, c: Arc<AtomicUsize>| async move {
            sf.execute("https://example.com/asset.js", || async {
                c.fetch_add(1, Ordering::SeqCst);
                sleep(Duration::from_millis(50)).await;
                Ok("response_body".to_string())
            })
            .await
        };

        // Launch 5 concurrent requests for the exact same key
        let mut handles = Vec::new();
        for _ in 0..5 {
            handles.push(tokio::spawn(run_request(sf.clone(), counter.clone())));
        }

        for h in handles {
            let res = h.await.unwrap();
            assert_eq!(res.unwrap(), "response_body");
        }

        // The actual worker closure must have executed EXACTLY ONCE
        assert_eq!(counter.load(Ordering::SeqCst), 1);
        assert_eq!(sf.total_calls(), 5);
        assert_eq!(sf.coalesced_calls(), 4);
    }

    #[tokio::test]
    async fn test_different_keys_run_independently() {
        let sf = SingleFlight::<u32, ()>::new();
        let counter = Arc::new(AtomicUsize::new(0));

        let h1 = {
            let sf = sf.clone();
            let c = counter.clone();
            tokio::spawn(async move {
                sf.execute("key1", || async move {
                    c.fetch_add(1, Ordering::SeqCst);
                    Ok(100)
                })
                .await
            })
        };

        let h2 = {
            let sf = sf.clone();
            let c = counter.clone();
            tokio::spawn(async move {
                sf.execute("key2", || async move {
                    c.fetch_add(1, Ordering::SeqCst);
                    Ok(200)
                })
                .await
            })
        };

        assert_eq!(h1.await.unwrap().unwrap(), 100);
        assert_eq!(h2.await.unwrap().unwrap(), 200);
        assert_eq!(counter.load(Ordering::SeqCst), 2);
    }

    #[tokio::test]
    async fn test_subsequent_request_runs_anew() {
        let sf = SingleFlight::<u32, ()>::new();
        let counter = Arc::new(AtomicUsize::new(0));

        let res1 = sf
            .execute("key", || {
                let c = counter.clone();
                async move {
                    c.fetch_add(1, Ordering::SeqCst);
                    Ok(1)
                }
            })
            .await
            .unwrap();

        assert_eq!(res1, 1);
        assert_eq!(counter.load(Ordering::SeqCst), 1);

        // Second request after first finished should run again (or be served by cache in higher layer)
        let res2 = sf
            .execute("key", || {
                let c = counter.clone();
                async move {
                    c.fetch_add(1, Ordering::SeqCst);
                    Ok(2)
                }
            })
            .await
            .unwrap();

        assert_eq!(res2, 2);
        assert_eq!(counter.load(Ordering::SeqCst), 2);
    }
}
