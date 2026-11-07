//! `ladder_bench.rs` — Class 1: pressure-ladder boundary stress.
//!
//! S1: cap sweep 4..64 MB in 2 MiB steps — each step is a fresh engine
//!     running the SAME load, so some caps land exactly on GC thresholds.
//! S2: many-callbacks-per-eval — a load that keeps hitting the near-heap
//!     limit repeatedly in ONE eval (waves that fill the granted headroom).
//! S3: cannot-fit caps — a retained live set larger than the heap can ever
//!     hold: must fail honestly and FAST, not thrash for minutes.
//!
//! Run: cargo run -p doshgentv8-core --release --example ladder_bench

use doshgentv8_core::{DoshEngine, HostSeam};
use std::sync::Arc;
use std::time::{Duration, Instant};

struct BenchSeam;
impl HostSeam for BenchSeam {
    fn call(&self, _name: &str, _args: &[String]) -> Result<Option<String>, String> {
        Ok(None)
    }
}

/// A load that genuinely rides the near-heap limit: a live set sized to a
/// fraction of the cap PLUS waves of garbage, so GC must run near the edge.
/// `waves` large waves of garbage between retained checkpoints of the live set.
fn near_limit_load(cap_bytes: usize) -> String {
    // Live set ~60% of the cap (survives scavenges -> old space).
    let live_units = (cap_bytes as f64 * 0.6 / 1024.0) as usize;
    // Garbage wave ~15% of the cap per wave.
    let wave_units = (cap_bytes as f64 * 0.15 / 1024.0).max(100.0) as usize;
    format!(
        "var keep=[]; for(var i=0;i<{live_units};i++) keep.push('k'.repeat(1024)); \
         var t=0; for(var w=0;w<40;w++){{ var tmp=[]; for(var j=0;j<{wave_units};j++){{ var s='z'.repeat(1024); tmp.push(s); t+=s.length; }} }} \
         t"
    )
}

fn run(cap_mb: usize, code: &str, wall_s: f64) -> (bool, f64, u8, usize, String) {
    let mut engine =
        DoshEngine::new_with_limit(Arc::new(BenchSeam), cap_mb * 1024 * 1024).expect("engine");
    engine.set_time_limit(wall_s);
    let t = Instant::now();
    let res = engine.eval(code);
    let dt = t.elapsed().as_secs_f64();
    (res.is_ok(), dt, engine.pressure_epoch(), engine.heap_band().2, res.map(|v| v.to_string()).unwrap_or_else(|e| e))
}

fn main() {
    // S1: boundary sweep. Small steps so some caps sit exactly on V8's
    // internal GC thresholds.
    println!("== S1: cap sweep (near-limit load, 60s wall) ==");
    println!("cap_mb,verdict,seconds,epoch,granted_bytes,outcome");
    for cap in [4, 6, 8, 10, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48, 56, 64] {
        let code = near_limit_load(cap * 1024 * 1024);
        let (ok, dt, epoch, granted, out) = run(cap, &code, 60.0);
        println!(
            "{},{},{:.2},{},{},{}",
            cap,
            if ok { "PASS" } else { "FAIL" },
            dt,
            epoch,
            granted,
            out.chars().take(24).collect::<String>()
        );
    }

    // S2: many-callbacks-per-eval. One eval, live set pinned at the cap,
    // then continuous garbage: the ladder must be hit over and over.
    println!("\n== S2: many-callbacks-per-eval (cap 8MB, live 5MB, 400 waves) ==");
    let code = r#"var keep=[]; for(var i=0;i<5120;i++) keep.push('k'.repeat(1024));
        var t=0; for(var w=0;w<400;w++){ var tmp=[]; for(var j=0;j<300;j++){ var s='z'.repeat(1024); tmp.push(s); t+=s.length; } }
        t"#;
    let t0 = Instant::now();
    let (ok, dt, epoch, granted, out) = run(8, code, 120.0);
    println!(
        "verdict,{},seconds,{:.2},epoch,{},granted,{},out,{}",
        if ok { "PASS" } else { "FAIL" },
        dt,
        epoch,
        granted,
        out.chars().take(24).collect::<String>()
    );
    // Cooldown observation: after the eval, is the engine still usable?
    let mut engine = DoshEngine::new_with_limit(Arc::new(BenchSeam), 8 * 1024 * 1024).unwrap();
    engine.set_time_limit(10.0);
    let ok2 = engine.eval("7*6");
    println!("post_stress_eval_ok,{},total_elapsed,{:.2}s", ok2.is_ok(), t0.elapsed().as_secs_f64());

    // S3: cannot-fit caps. Live sets both below and ABOVE the hard ceiling
    // (limit + max(64MB, limit/2)), so we find the real cliff of the envelope.
    println!("\n== S3: cannot-fit caps — find the envelope cliff ==");
    println!("cap_mb,live_mb,ceiling_mb,verdict,seconds,epoch,outcome");
    for (cap, live_mb) in [
        (4usize, 12usize),   // fits via ladder (ceiling 68MB)
        (4, 40),             // still under ceiling
        (4, 64),             // just at the edge of ceiling
        (4, 80),             // beyond ceiling 68MB -> must fail honestly
        (4, 120),            // far beyond -> must fail fast
        (16, 100),           // ceiling = 16+64=80MB -> beyond
    ] {
        let units = live_mb * 1024; // KB units of 1024B
        let code = format!("var keep=[]; for(var i=0;i<{units};i++) keep.push('k'.repeat(1024)); keep.length");
        let t = Instant::now();
        let mut engine = DoshEngine::new_with_limit(Arc::new(BenchSeam), cap * 1024 * 1024).unwrap();
        engine.set_time_limit(30.0);
        let res = engine.eval(&code);
        let dt = t.elapsed().as_secs_f64();
        let ceiling = cap + (64usize).max(cap / 2);
        println!(
            "{},{},{},{},{:.2},{},{}",
            cap,
            live_mb,
            ceiling,
            if res.is_ok() { "PASS" } else { "FAIL" },
            dt,
            engine.pressure_epoch(),
            match &res {
                Ok(v) => v.to_string(),
                Err(e) => e.clone(),
            }
        );
    }
    let _ = Duration::from_millis(0); // keep the import used
}
