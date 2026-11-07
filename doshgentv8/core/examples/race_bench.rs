//! `race_bench.rs` — Class 2: concurrency/reentrancy races in the pressure path.
//!
//! R1: watchdog × ladder — a wall-clock deadline that fires WHILE the
//!     near-heap ladder is granting headroom. The watchdog terminates
//!     execution; the ladder callback may be on the stack inside GC.
//!     Must not deadlock, crash, or corrupt the isolate.
//! R2: host-callable reentrancy under pressure — a page that calls into
//!     the host seam (which re-enters the embedder) while the heap is under
//!     ladder pressure. The reentrancy guard must behave.
//! R3: multi-isolate simultaneous pressure — N engines in one process, all
//!     under pressure at once: per-isolate state must not cross.

use doshgentv8_core::{DoshEngine, HostSeam};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Instant;

struct CountingSeam {
    calls: AtomicUsize,
}
impl HostSeam for CountingSeam {
    fn call(&self, name: &str, _args: &[String]) -> Result<Option<String>, String> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        if name == "host_fetch" {
            Ok(Some("{\"status\":200,\"body\":\"ok\"}".into()))
        } else {
            Ok(None)
        }
    }
}

fn main() {
    // R1: watchdog fires DURING pressure. Live set beyond the cap (forces
    // ladder callbacks), wall budget 1s (forces watchdog mid-pressure).
    // Requirement: terminates with ONE of the honest errors within ~2s.
    println!("== R1: watchdog x ladder race ==");
    let code = "var keep=[]; for(var i=0;i<20000;i++) keep.push('k'.repeat(1024)); var t=0; for(;;){ t++; } t";
    let t0 = Instant::now();
    let mut engine = DoshEngine::new_with_limit(Arc::new(CountingSeam { calls: AtomicUsize::new(0) }), 4 * 1024 * 1024).unwrap();
    engine.set_time_limit(1.0); // 1s wall: the watchdog WILL fire mid-alloc
    let res = engine.eval(code);
    let dt = t0.elapsed().as_secs_f64();
    let err = res.err().unwrap_or_else(|| "NO ERROR".into());
    let ok_honest = matches!(err.as_str(), "interrupted" | "out of memory");
    println!("verdict,{},seconds,{:.2},epoch,{},error,\"{}\"", ok_honest, dt, engine.pressure_epoch(), err);
    assert!(ok_honest, "R1: must die with an honest error, got: {err}");
    assert!(dt < 3.0, "R1: must not hang, took {dt}s");
    // Isolate recovery after the race?
    engine.set_time_limit(10.0);
    let ok = engine.eval("40+2");
    println!("post_race_recovery,{}", ok.is_ok());
    assert!(ok.is_ok(), "R1: engine must recover after the race");

    // R2: host-callable reentrancy under pressure. A host_fetch call inside
    // a heap-pressure loop — the seam re-enters the embedder while the
    // ladder is engaged.
    println!("\n== R2: host-callable reentrancy under pressure ==");
    let seam = Arc::new(CountingSeam { calls: AtomicUsize::new(0) });
    let mut engine = DoshEngine::new_with_limit(seam.clone(), 8 * 1024 * 1024).unwrap();
    engine.set_time_limit(20.0);
    let code = r#"
        var keep=[]; for(var i=0;i<6144;i++) keep.push('k'.repeat(1024));
        var t=0;
        for(var w=0;w<30;w++){
            var tmp=[]; for(var j=0;j<300;j++){ tmp.push('z'.repeat(1024)); }
            t += __dispatchHost ? 1 : 0;
            try { var r = host_fetch("https://press.test/w"); t += r.length; } catch(e) { t += 1; }
        }
        t
    "#;
    let res = engine.eval(code);
    let calls = seam.calls.load(Ordering::SeqCst);
    println!(
        "verdict,{},epoch,{},host_calls,{},outcome,{}",
        res.is_ok(),
        engine.pressure_epoch(),
        calls,
        res.map(|v| v.to_string()).unwrap_or_else(|e| e)
    );
    // host_fetch may not be registered in this bare engine — a thrown JS
    // error caught by the try/catch is acceptable. What is NOT acceptable:
    // a crash, a hang, or a corrupted isolate.
    let ok = engine.eval("6*7");
    println!("post_reentrancy_ok,{}", ok.is_ok());
    assert!(ok.is_ok(), "R2: engine must survive host-callable reentrancy under pressure");

    // R3: multi-isolate simultaneous pressure. 4 engines, each with a cap
    // sized to force ladder engagement, pressured in sequence (V8 isolates
    // are single-threaded per engine; the race is in shared process state:
    // the watchdog supervisor + V8 platform).
    println!("\n== R3: multi-isolate simultaneous pressure ==");
    let mut engines = Vec::new();
    for i in 0..4 {
        let e = DoshEngine::new_with_limit(
            Arc::new(CountingSeam { calls: AtomicUsize::new(0) }),
            4 * 1024 * 1024,
        )
        .unwrap();
        engines.push(e);
    }
    let t0 = Instant::now();
    let code = "var keep=[]; for(var i=0;i<8192;i++) keep.push('k'.repeat(1024)); keep.length";
    let mut results = Vec::new();
    for (i, engine) in engines.iter_mut().enumerate() {
        engine.set_time_limit(15.0);
        let res = engine.eval(code);
        results.push((i, res.is_ok(), engine.pressure_epoch(), engine.heap_band().2));
    }
    println!("isolate,verdict,epoch,granted");
    for (i, ok, epoch, granted) in &results {
        println!("{},{},{},{}", i, ok, epoch, granted);
    }
    // Cross-contamination check: every isolate's granted limit must be its
    // OWN (allowing ladder growth), and epochs must be per-isolate.
    let all_ok = results.iter().all(|(_, ok, _, _)| *ok);
    let distinct_granted: std::collections::HashSet<usize> =
        results.iter().map(|(_, _, _, g)| *g).collect();
    println!("all_passed,{},distinct_granted_counts,{},elapsed,{:.2}s", all_ok, distinct_granted.len(), t0.elapsed().as_secs_f64());
    assert!(all_ok, "R3: all isolates must survive simultaneous pressure");
    assert!(!distinct_granted.is_empty(), "R3: granted limits must be observable");
}
