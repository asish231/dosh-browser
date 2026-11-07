//! `ab_pool_bench.rs` — Stage-4 pooled ArrayBuffer allocator benchmark.
//!
//! Allocates ~100 MB of ArrayBuffers in the engine and reports the pool's
//! accounting + process RSS. Run twice to compare the default allocator with
//! the pool:
//!   cargo run -p doshgentv8-core --release --example ab_pool_bench           # default
//!   WR_AB_POOL=1 cargo run -p doshgentv8-core --release --example ab_pool_bench  # pooled

use doshgentv8_core::ab_pool::{ab_pool_enabled, ab_pool_stats};
use doshgentv8_core::{DoshEngine, HostSeam};
use std::sync::Arc;
use std::time::Instant;

struct BenchSeam;
impl HostSeam for BenchSeam {
    fn call(&self, _name: &str, _args: &[String]) -> Result<Option<String>, String> {
        Ok(None)
    }
}

fn rss_kb() -> i64 {
    let out = std::process::Command::new("ps")
        .args(["-o", "rss=", "-p", &std::process::id().to_string()])
        .output()
        .expect("ps failed");
    String::from_utf8_lossy(&out.stdout).trim().parse().unwrap_or(0)
}

fn main() {
    println!("pool_enabled,{}", ab_pool_enabled());
    let mut engine = DoshEngine::new(Arc::new(BenchSeam)).expect("engine create failed");
    engine.set_time_limit(30.0);
    let t = Instant::now();
    // 100 x 1 MiB ArrayBuffers, retained for the measurement.
    let res = engine.eval(
        r#"var a=[]; for(var i=0;i<100;i++) a.push(new ArrayBuffer(1024*1024)); a.length"#,
    );
    let dt = t.elapsed().as_secs_f64();
    let s = ab_pool_stats();
    println!(
        "result,{:?},seconds,{:.2},rss_kb,{},pool_live,{},pool_peak,{},pool_allocs,{},pool_budget,{}",
        res, dt, rss_kb(), s.live, s.peak, s.allocs, s.budget
    );
    // Drop and force GC, then re-measure.
    engine.eval("a = null; 1").expect("drop eval failed");
    engine.pump_jobs();
    engine.pump_jobs();
    let s2 = ab_pool_stats();
    println!(
        "after_drop_rss_kb,{},pool_live,{},pool_peak,{}",
        rss_kb(),
        s2.live,
        s2.peak
    );
}
