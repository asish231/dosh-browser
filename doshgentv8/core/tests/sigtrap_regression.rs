//! Permanent regression tests for the V8 near-heap-limit contract traps.
//!
//! The R5 trap: returning an unchanged (or lower) limit from
//! NearHeapLimitCallback invokes V8's own OOM handler — a PROCESS-FATAL
//! abort (SIGTRAP), not an honest error. Any future edit to the ladder can
//! re-trip it. These tests make genuine exhaustion part of the normal suite
//! so the trap can never come back silently.
//!
//! They are FAST (0.2-0.4s each) and deliberately small so they run on
//! every `cargo test` — the process abort they guard against cannot be
//! caught by `#[should_panic]`; only by never happening.

use doshgentv8_core::{DoshEngine, HostSeam};
use std::sync::Arc;

struct Seam;
impl HostSeam for Seam {
    fn call(&self, _name: &str, _args: &[String]) -> Result<Option<String>, String> {
        Ok(None)
    }
}

#[test]
fn sigtrap_regression_genuine_exhaustion_dies_honestly() {
    // The exact shape that killed the first ladder revision: retained
    // growth far beyond the ceiling. Must produce "out of memory" — if the
    // callback contract is ever broken again, this test ABORTS the process
    // (SIGTRAP), which CI sees as a hard failure.
    let mut engine = DoshEngine::new_with_limit(Arc::new(Seam), 16 * 1024 * 1024).unwrap();
    engine.set_time_limit(30.0);
    let res = engine.eval("var a=[]; for(;;) a.push('y'.repeat(65536)); a.length");
    assert_eq!(res.unwrap_err(), "out of memory");
    assert!(engine.pressure_epoch() >= 2);
}

#[test]
fn sigtrap_regression_small_cap_large_live_set() {
    // A second exhaustion shape: live set far beyond a tiny cap's ceiling
    // (cap 4MB, ceiling 68MB, live 120MB). Also must die honestly.
    let mut engine = DoshEngine::new_with_limit(Arc::new(Seam), 4 * 1024 * 1024).unwrap();
    engine.set_time_limit(30.0);
    let units = 120 * 1024;
    let code = format!("var keep=[]; for(var i=0;i<{units};i++) keep.push('k'.repeat(1024)); keep.length");
    let res = engine.eval(&code);
    assert_eq!(res.unwrap_err(), "out of memory");
}

#[test]
fn sigtrap_regression_engine_recovers_after_oom() {
    // After genuine exhaustion the isolate must still be usable (the
    // callback's raised "lifeboat" limit must let V8 unwind cleanly).
    let mut engine = DoshEngine::new_with_limit(Arc::new(Seam), 16 * 1024 * 1024).unwrap();
    engine.set_time_limit(30.0);
    let res = engine.eval("var a=[]; for(;;) a.push('y'.repeat(65536)); a.length");
    assert_eq!(res.unwrap_err(), "out of memory");
    // Recovery: fresh eval on the same isolate.
    engine.set_time_limit(10.0);
    let ok = engine.eval("6*7");
    assert_eq!(ok.unwrap(), "42");
    assert_eq!(engine.pressure_epoch(), 0, "epoch must reset per eval");
}
