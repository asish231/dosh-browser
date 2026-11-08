//! `remediation_bench.rs` — Empirical benchmark for the completed Section-2
//! audit & partial-implementation remediation pass (2026-11-08).
//!
//! Run:
//!   cargo run --manifest-path doshgentv8/Cargo.toml -p doshgentv8-core --release --example remediation_bench

use std::sync::Arc;
use std::time::Instant;

use doshgentv8_core::parena::{Parena, TagId};
use doshgentv8_core::protocol::{DoshCommand, DoshOpCode};
use doshgentv8_core::{DoshEngine, HostSeam};

struct BenchSeam;
impl HostSeam for BenchSeam {
    fn call(&self, name: &str, args: &[String]) -> Result<Option<String>, String> {
        if name == "echo" {
            Ok(Some(args.join(",")))
        } else {
            Ok(None)
        }
    }
}

fn main() {
    println!("=== DOSH AUDIT REMEDIATION & SUBSYSTEM BENCHMARK (2026-11-08) ===");

    // 1. Cold vs Snapshot Isolate Boot
    let t0 = Instant::now();
    let mut engine = DoshEngine::new_with_limit(Arc::new(BenchSeam), 128 * 1024 * 1024).unwrap();
    let cold_boot_ms = t0.elapsed().as_secs_f64() * 1000.0;

    let snap_t0 = Instant::now();
    let blob = doshgentv8_core::create_dosh_snapshot(None).unwrap();
    let snap_create_ms = snap_t0.elapsed().as_secs_f64() * 1000.0;

    let warm_t0 = Instant::now();
    let mut warm_engine = DoshEngine::new_with_snapshot(Arc::new(BenchSeam), &blob).unwrap();
    let warm_boot_ms = warm_t0.elapsed().as_secs_f64() * 1000.0;
    assert_eq!(warm_engine.eval("typeof dosh.parenaNode").unwrap(), "function");

    println!(
        "boot: cold_ms={:.3} snapshot_blob_kb={:.1} snapshot_create_ms={:.3} warm_boot_ms={:.3} speedup={:.2}x",
        cold_boot_ms,
        blob.len() as f64 / 1024.0,
        snap_create_ms,
        warm_boot_ms,
        cold_boot_ms / warm_boot_ms.max(0.001)
    );

    // 2. DCP Owned & Scratch-Backed Command Dispatch Latency
    let mut arena = Parena::new();
    for i in 0..5_000u32 {
        arena.add_node(
            TagId::Div,
            if i == 0 { u32::MAX } else { 0 },
            &[("id", "node-item"), ("data-idx", "1234567890"), ("class", "card-row-item")],
            "Benchmark node text payload for State Weave zero-copy and spill tier verification.",
        );
    }
    engine.set_parena(arena);

    let iters = 10_000u32;
    let t_fuel = Instant::now();
    for _ in 0..iters {
        let cmd = DoshCommand::new(DoshOpCode::SetFuel).with_param(100_000);
        let _ = engine.dispatch_command(&cmd);
        let q = DoshCommand::new(DoshOpCode::GetFuel);
        let r = engine.dispatch_command(&q);
        assert_eq!(r.numeric_val, 100_000);
    }
    let dcp_fuel_ns = t_fuel.elapsed().as_nanos() as f64 / (iters as f64 * 2.0);

    let ids: Vec<u32> = (0..64).collect();
    let mut id_bytes = Vec::with_capacity(ids.len() * 4);
    for id in &ids {
        id_bytes.extend_from_slice(&id.to_le_bytes());
    }
    let batch_iters = 5_000u32;
    let t_batch = Instant::now();
    let mut last_len = 0usize;
    for _ in 0..batch_iters {
        let mut cmd = DoshCommand::new(DoshOpCode::BatchRead);
        cmd.payload = id_bytes.as_ptr();
        cmd.payload_len = id_bytes.len() as u32;
        let resp = engine.dispatch_owned(&cmd);
        last_len = resp.data.len();
    }
    let dcp_batch64_us = t_batch.elapsed().as_secs_f64() * 1_000_000.0 / batch_iters as f64;
    println!(
        "dcp: set_get_fuel_ns={:.1} batch_read_64_nodes_us={:.2} payload_bytes={}",
        dcp_fuel_ns, dcp_batch64_us, last_len
    );

    // 3. Borrowed-View JS Helpers (`dosh.parenaNode`, `dosh.parenaStats`)
    let js_iters = 2_000u32;
    let t_bv = Instant::now();
    let res = engine
        .eval(&format!(
            "(function(){{ var n=0; for(var i=0;i<{js_iters};i++){{ var o=dosh.parenaNode(i % 5000); if(o) n+=o.text.length; }} return n; }})()"
        ))
        .unwrap();
    let bv_us_per_call = t_bv.elapsed().as_secs_f64() * 1_000_000.0 / js_iters as f64;
    println!(
        "borrowed_view_js: dosh_parenaNode_us_per_call={:.2} total_chars_read={}",
        bv_us_per_call, res
    );

    // 4. Parena Future A: Text + Attr Column Spill & Warm Checkpoint
    let mut big_arena = Parena::new();
    let big_text = "T".repeat(1024);
    let big_attr = "A".repeat(512);
    for i in 0..10_000u32 {
        big_arena.add_node(
            TagId::Section,
            if i == 0 { u32::MAX } else { 0 },
            &[("data-blob", &big_attr), ("class", "spilled-section")],
            &big_text,
        );
    }
    let ram_before = big_arena.external_bytes();
    let t_wcp = Instant::now();
    let cp = big_arena.warm_checkpoint(512 * 1024); // 512 KiB hot prefix watermark
    let warm_cp_ms = t_wcp.elapsed().as_secs_f64() * 1000.0;
    let ram_after = big_arena.external_bytes();
    let spill_total = big_arena.spill_bytes();

    // Verify stitched reads across hot & spilled text + attr columns
    let t_read = Instant::now();
    for idx in [0u32, 500, 5_000, 9_999] {
        assert_eq!(big_arena.get_text(idx).len(), 1024);
        assert_eq!(
            big_arena.get_attrs(idx).get("data-blob").map(|s| s.len()),
            Some(512)
        );
    }
    let stitched_read_us = t_read.elapsed().as_secs_f64() * 1_000_000.0 / 4.0;
    println!(
        "parena_future_a: ram_before_kb={} ram_after_kb={} spill_kb={} reduction_pct={:.1}% warm_checkpoint_ms={:.2} stitched_read_us={:.2} has_spill_path={}",
        ram_before / 1024,
        ram_after / 1024,
        spill_total / 1024,
        (1.0 - (ram_after as f64 / ram_before as f64)) * 100.0,
        warm_cp_ms,
        stitched_read_us,
        cp.spill_path.is_some()
    );
}
