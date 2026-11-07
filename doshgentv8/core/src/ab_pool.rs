//! `ab_pool.rs` — pooled, accounted ArrayBuffer allocator (Stage 4).
//!
//! V8's default ArrayBuffer allocator is malloc-opaque: backing stores come
//! and go with no host visibility. This module swaps in a pool allocator via
//! `v8::new_rust_allocator` (CreateParams::array_buffer_allocator) that keeps
//! host-side accounting — live/peak bytes and allocation count — and a
//! budget signal. V8 itself still accounts this memory as external memory
//! (documented in the v8 crate: "Memory allocated through this allocator by
//! V8 is accounted for as external memory by V8"), so its GC pacer keeps
//! working unchanged; the pool adds the host observability layer.
//!
//! Safety notes honored from the crate docs: it is UNSAFE to call back into
//! V8 from any allocator function — the pool therefore only touches its own
//! atomics. Opt-in: `WR_AB_POOL=1` env or `set_ab_pool_enabled(true)`.

use std::ffi::c_void;
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::sync::OnceLock;

/// Host-side pool accounting (all isolates share one pool).
pub struct AbPool {
    live: AtomicUsize,
    peak: AtomicUsize,
    allocs: AtomicU64,
    /// Budget signal (bytes). Allocations are NOT refused past it — an
    /// agent browser must not break pages — but it is observable so hosts
    /// (Loom, MCP) can act on pressure. WR_AB_POOL_MB, default 256 MiB.
    budget: AtomicUsize,
}

impl AbPool {
    const fn new() -> Self {
        Self {
            live: AtomicUsize::new(0),
            peak: AtomicUsize::new(0),
            allocs: AtomicU64::new(0),
            budget: AtomicUsize::new(256 * 1024 * 1024),
        }
    }
}

/// The single shared pool instance.
static POOL: AbPool = AbPool::new();

fn env_budget() -> usize {
    std::env::var("WR_AB_POOL_MB")
        .ok()
        .and_then(|v| v.parse::<usize>().ok())
        .unwrap_or(256)
        .saturating_mul(1024 * 1024)
}

fn init_budget_once() {
    static ONCE: OnceLock<()> = OnceLock::new();
    ONCE.get_or_init(|| {
        POOL.budget.store(env_budget(), Ordering::SeqCst);
    });
}

/// Pool stats snapshot for hosts.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PoolStats {
    pub live: usize,
    pub peak: usize,
    pub allocs: u64,
    pub budget: usize,
}

/// Current pool stats (live/peak bytes, allocation count, budget).
pub fn ab_pool_stats() -> PoolStats {
    init_budget_once();
    PoolStats {
        live: POOL.live.load(Ordering::SeqCst),
        peak: POOL.peak.load(Ordering::SeqCst),
        allocs: POOL.allocs.load(Ordering::SeqCst),
        budget: POOL.budget.load(Ordering::SeqCst),
    }
}

/// Set the pool budget signal (bytes).
pub fn set_ab_pool_budget(bytes: usize) {
    init_budget_once();
    POOL.budget.store(bytes, Ordering::SeqCst);
}

// --- opt-in switch (env-seeded, overridable for tests/hosts) ---

static OVERRIDE: AtomicBool = AtomicBool::new(false);
static ENV_ON: OnceLock<bool> = OnceLock::new();

/// Is the pooled allocator enabled? `WR_AB_POOL=1` env, or an explicit
/// `set_ab_pool_enabled(true)`. Must be decided BEFORE isolate creation.
pub fn ab_pool_enabled() -> bool {
    if OVERRIDE.load(Ordering::SeqCst) {
        return true;
    }
    *ENV_ON.get_or_init(|| {
        std::env::var("WR_AB_POOL")
            .map(|v| v == "1" || v.eq_ignore_ascii_case("true"))
            .unwrap_or(false)
    })
}

/// Force-enable/disable the pool for subsequently created isolates
/// (overrides the env for this process).
pub fn set_ab_pool_enabled(on: bool) {
    OVERRIDE.store(on, Ordering::SeqCst);
}

// --- the v8 Allocator vtable ---

unsafe extern "C" fn pool_allocate(_h: &AbPool, len: usize) -> *mut c_void {
    // V8 calls this for zero-initialized backing stores. We allocate zeroed
    // (never hand V8 uninitialized memory) and account it.
    let mut buf = vec![0u8; len];
    let ptr = buf.as_mut_ptr();
    std::mem::forget(buf);
    POOL.allocs.fetch_add(1, Ordering::SeqCst);
    let live = POOL.live.fetch_add(len, Ordering::SeqCst) + len;
    // Track the high-water mark.
    let mut peak = POOL.peak.load(Ordering::SeqCst);
    while live > peak {
        match POOL
            .peak
            .compare_exchange_weak(peak, live, Ordering::SeqCst, Ordering::SeqCst)
        {
            Ok(_) => break,
            Err(cur) => peak = cur,
        }
    }
    ptr as *mut c_void
}

unsafe extern "C" fn pool_allocate_uninitialized(h: &AbPool, len: usize) -> *mut c_void {
    // Zero anyway: honesty over a nanosecond — V8 treats backing stores as
    // zeroed, and uninit memory leaking into pages is not a trade we make.
    pool_allocate(h, len)
}

unsafe extern "C" fn pool_free(_h: &AbPool, data: *mut c_void, len: usize) {
    if data.is_null() {
        return;
    }
    POOL.live.fetch_sub(len, Ordering::SeqCst);
    // Reconstruct and drop — the allocation was a forgotten Vec.
    drop(unsafe { Vec::from_raw_parts(data as *mut u8, len, len) });
}

unsafe extern "C" fn pool_drop(_h: *const AbPool) {
    // Static instance: nothing to drop.
}

/// The static vtable handed to V8.
pub static AB_POOL_VTABLE: v8::RustAllocatorVtable<AbPool> = v8::RustAllocatorVtable {
    allocate: pool_allocate,
    allocate_uninitialized: pool_allocate_uninitialized,
    free: pool_free,
    drop: pool_drop,
};

/// Build the pooled allocator for CreateParams. Call only when
/// `ab_pool_enabled()` is true.
///
/// # Safety
/// The handle is `&'static POOL` and the vtable is a static — both outlive
/// every isolate, as `v8::new_rust_allocator` requires.
pub fn make_pooled_allocator() -> v8::UniqueRef<v8::Allocator> {
    init_budget_once();
    unsafe { v8::new_rust_allocator(&POOL, &AB_POOL_VTABLE) }
}

/// Tests that touch the SHARED pool (stats or the enabled-switch) must hold
/// this lock: cargo runs #[test] fns in parallel inside one process, and the
/// pool is process-global. Without the lock, one test's toggle/disable can
/// land between another test's set-up and its engine creation (caught live:
/// the pooled-allocator engine test failed only in the full suite).
#[cfg(test)]
pub static TEST_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pool_vtable_accounts_alloc_and_free() {
        let _g = TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        // Direct vtable exercise — deterministic, no V8 involved.
        let before = ab_pool_stats();
        let data = unsafe { pool_allocate(&POOL, 1024) };
        assert!(!data.is_null());
        let mid = ab_pool_stats();
        assert_eq!(mid.live, before.live + 1024, "live must grow by the allocation");
        assert_eq!(mid.allocs, before.allocs + 1);
        assert!(mid.peak >= before.live + 1024, "peak must track the high-water mark");
        unsafe { pool_free(&POOL, data, 1024) };
        let after = ab_pool_stats();
        assert_eq!(after.live, before.live, "free must return the bytes to the pool");
        assert!(after.peak >= mid.peak, "peak is a high-water mark, never decreases");
    }

    #[test]
    fn pool_budget_is_settable() {
        let _g = TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        set_ab_pool_budget(123 * 1024);
        assert_eq!(ab_pool_stats().budget, 123 * 1024);
        // Restore the default signal for other tests.
        set_ab_pool_budget(256 * 1024 * 1024);
    }

    #[test]
    fn pool_switch_overrides_env() {
        let _g = TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        assert!(!ab_pool_enabled(), "off by default unless env/override");
        set_ab_pool_enabled(true);
        assert!(ab_pool_enabled());
        set_ab_pool_enabled(false);
        assert!(!ab_pool_enabled());
    }
}
