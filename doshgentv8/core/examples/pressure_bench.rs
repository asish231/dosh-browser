//! `pressure_bench.rs` — heap-pressure ladder benchmark (Stage 0-5 evidence).
//!
//! Runs the three pressure scenarios that distinguish the OLD near-heap
//! callback (hard kill on first near-limit hit, pre-`1d7801ad`) from the NEW
//! pressure-graded ladder:
//!   1. collectable waves — garbage-heavy hydration (the build.nvidia.com class)
//!   2. mixed hydration — waves + a small live set (realistic page shape)
//!   3. retained growth — genuine exhaustion (must die honestly, bounded)
//!
//! Run: cargo run -p doshgentv8-core --release --example pressure_bench

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
    String::from_utf8_lossy(&out.stdout)
        .trim()
        .parse()
        .unwrap_or(0)
}

fn scenario(name: &str, cap_mb: usize, code: &str) -> (bool, f64, i64, String, u8, usize) {
    let mut engine = DoshEngine::new_with_limit(Arc::new(BenchSeam), cap_mb * 1024 * 1024)
        .expect("engine create failed");
    engine.set_time_limit(30.0);
    let t = Instant::now();
    let res = engine.eval(code);
    let dt = t.elapsed().as_secs_f64();
    let rss = rss_kb();
    let epoch = engine.pressure_epoch();
    let granted = engine.heap_band().2;
    match res {
        Ok(v) => (true, dt, rss, v, epoch, granted),
        Err(e) => (false, dt, rss, e, epoch, granted),
    }
}

fn main() {
    let cases: Vec<(&str, usize, &str)> = vec![
        (
            "collectable-64MiB-garbage",
            24,
            r#"var t=0; for(var i=0;i<64000;i++){var s='x'.repeat(1024); t+=s.length;} t"#,
        ),
        (
            "mixed-hydration-40MiB",
            32,
            r#"var keep=[]; for(var w=0; w<40; w++){ var scratch=[]; for(var i=0;i<2000;i++) scratch.push('z'.repeat(512)); keep.push(scratch[0]); } keep.length"#,
        ),
        (
            "promotion-churn-150MiB",
            8,
            r#"var keep=null; var t=0; for(var w=0;w<50;w++){ var tmp=[]; for(var i=0;i<3000;i++){ var s='z'.repeat(1024); tmp.push(s); t+=s.length; } keep=tmp; } t"#,
        ),
        (
            "live-near-cap-churn",
            8,
            r#"var keep=[]; for(var i=0;i<7000;i++) keep.push('k'.repeat(1024)); var t=0; for(var w=0;w<300;w++){ var tmp=[]; for(var j=0;j<1000;j++){var s='z'.repeat(1024); tmp.push(s); t+=s.length;} } t"#,
        ),
        (
            "retained-growth-unbounded",
            16,
            r#"var a=[]; for(;;) a.push('y'.repeat(65536)); a.length"#,
        ),
    ];
    println!("scenario,cap_mb,verdict,seconds,rss_kb,pressure_epoch,granted_bytes,outcome");
    for (name, cap, code) in cases {
        let (ok, dt, rss, out, epoch, granted) = scenario(name, cap, code);
        println!(
            "{},{},{},{:.2},{},{},{},{}",
            name,
            cap,
            if ok { "PASS" } else { "FAIL" },
            dt,
            rss,
            epoch,
            granted,
            out.chars().take(40).collect::<String>()
        );
    }
    println!("final_rss_kb,{}", rss_kb());
}
