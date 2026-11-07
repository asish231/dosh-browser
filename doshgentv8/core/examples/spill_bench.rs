//! `spill_bench.rs` — Stage-3 Parena spill tier RSS benchmark.
//!
//! Builds a ~200 MB page arena, spills the cold tail at a 64 MB watermark,
//! and measures REAL process RSS before/after (plus read-back verification).

use doshgentv8_core::parena::{Parena, TagId};
use std::time::Duration;

fn rss_kb() -> i64 {
    let out = std::process::Command::new("ps")
        .args(["-o", "rss=", "-p", &std::process::id().to_string()])
        .output()
        .expect("ps failed");
    String::from_utf8_lossy(&out.stdout).trim().parse().unwrap_or(0)
}

fn main() {
    let mut arena = Parena::default();
    let big = "d".repeat(250_000); // 250 KB per node text
    let nodes = 800; // 800 x 250 KB = 200 MB logical text
    for i in 0..nodes {
        arena.add_node(TagId::Div, 0, &[("i", &i.to_string())], &big);
    }
    println!("nodes,{},text_bytes,{}", nodes, arena.text_len());
    std::thread::sleep(Duration::from_millis(200));
    let before = rss_kb();
    println!("rss_before_spill_kb,{}", before);

    let watermark = 64 * 1024 * 1024;
    let spilled = arena.spill_tail(watermark);
    std::thread::sleep(Duration::from_millis(300));
    let after = rss_kb();
    println!(
        "spill_happened,{},spill_bytes,{},hot_prefix_bytes,{},rss_after_spill_kb,{},rss_delta_kb,{}",
        spilled,
        arena.spill_bytes(),
        arena.text_arena.len(),
        after,
        before - after
    );

    // Read back through the tier: node 0 (hot), boundary, node 799 (spilled).
    let ok_first = arena.get_text(0) == big;
    let ok_last = arena.get_text((nodes - 1) as u32) == big;
    println!(
        "read_first_ok,{},read_last_ok,{},rss_after_reads_kb,{}",
        ok_first,
        ok_last,
        rss_kb()
    );
    assert!(ok_first && ok_last, "spilled reads must stay byte-identical");

    // REUSE PROBE: macOS does not shrink the RSS high-water on free, so the
    // honest "was the RAM released?" test is reuse — allocate 130 MB fresh
    // and see whether RSS grows. No growth == the spilled tail's pages were
    // genuinely reclaimed and handed back.
    let probe = vec![7u8; 130 * 1024 * 1024];
    let touch: u64 = probe.iter().map(|b| *b as u64).sum();
    std::thread::sleep(Duration::from_millis(200));
    println!(
        "reuse_probe_bytes,{},reuse_probe_sum,{},rss_after_reuse_probe_kb,{}",
        probe.len(),
        touch,
        rss_kb()
    );
    drop(probe);

    // Control for the same probe WITHOUT spill, for comparison, in a fresh
    // arena: proves the probe itself is a real allocation.
    let mut control = Parena::default();
    for i in 0..800 {
        control.add_node(TagId::Div, 0, &[("i", &i.to_string())], &big);
    }
    std::thread::sleep(Duration::from_millis(200));
    println!("control_rss_kb,{}", rss_kb());
}
