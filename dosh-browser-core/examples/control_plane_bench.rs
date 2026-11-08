//! `control_plane_bench.rs` — Empirical benchmark for the remediated
//! `dosh-browser-core` control plane, causal trace, NativeCrypto, Shadow DOM,
//! and ACD v2 pipeline (2026-11-08).
//!
//! Run:
//!   cargo run --manifest-path dosh-browser-core/Cargo.toml --release --example control_plane_bench

use std::time::Instant;
use dosh_browser_core::acd::build_acd;
use dosh_browser_core::runtime::Runtime;
use dosh_browser_core::session::Session;
use dosh_browser_core::shell::{execute_command, ShellStore};

#[tokio::main]
async fn main() {
    println!("=== DOSH-BROWSER-CORE CONTROL PLANE & ENGINE BENCHMARK (2026-11-08) ===");

    let client = reqwest::Client::new();
    let mut session = Session::new(client.clone());
    let store = ShellStore::new(None).unwrap();

    // 1. Seed a rich SPA fixture with canvas, iframe[srcdoc], shadow DOM, and mock API
    execute_command(
        "net.rule",
        &serde_json::json!({
            "action": "mock",
            "pattern": "api.example.com/telemetry",
            "status": 200,
            "body": "{\"recorded\":true}",
            "content_type": "application/json"
        }),
        &mut session,
        &store,
    )
    .await
    .unwrap();

    let html = r#"<!DOCTYPE html><html><head><title>BenchApp</title></head><body>
        <main>
            <h1>Control Plane Benchmark</h1>
            <span id="counter">0</span>
            <button id="act-btn" onclick="var c=document.getElementById('counter'); c.textContent=String(Number(c.textContent)+1); console.log('btn_click_' + c.textContent); fetch('https://api.example.com/telemetry');">Trigger Action</button>
            <canvas id="chart" width="64" height="64"></canvas>
            <script>
                var ctx = document.getElementById('chart').getContext('2d');
                ctx.fillStyle = '#00aa88';
                ctx.fillRect(0, 0, 32, 32);
            </script>
            <iframe name="widget" srcdoc="<html><head><title>WidgetFrame</title></head><body><script>window.__w = 777;</script><p>Frame Widget Body</p></body></html>"></iframe>
        </main>
    </body></html>"#;

    let t_load = Instant::now();
    session.goto_html("https://example.com/app", html, 8000).unwrap();
    let load_ms = t_load.elapsed().as_secs_f64() * 1000.0;

    let inv = execute_command("actions", &serde_json::json!({}), &mut session, &store).await.unwrap();
    let ref_id = inv["actions"][0]["ref_id"].as_str().unwrap().to_string();
    let epoch0 = session.document_epoch;

    // 2. Live Act vs Idempotent Replay vs Stale Epoch Refusal
    let t_act = Instant::now();
    let r1 = execute_command(
        "act",
        &serde_json::json!({
            "ref": ref_id,
            "expected_epoch": epoch0,
            "idempotency_key": "bench-idem-1"
        }),
        &mut session,
        &store,
    )
    .await
    .unwrap();
    let live_act_ms = t_act.elapsed().as_secs_f64() * 1000.0;
    assert_eq!(r1["replayed_idempotent"], false);

    let t_idem = Instant::now();
    for _ in 0..1_000 {
        let r_cached = execute_command(
            "act",
            &serde_json::json!({
                "ref": ref_id,
                "expected_epoch": epoch0,
                "idempotency_key": "bench-idem-1"
            }),
            &mut session,
            &store,
        )
        .await
        .unwrap();
        assert_eq!(r_cached["replayed_idempotent"], true);
    }
    let idem_replay_us = t_idem.elapsed().as_secs_f64() * 1_000_000.0 / 1_000.0;

    let t_stale = Instant::now();
    for _ in 0..1_000 {
        let stale = execute_command(
            "act",
            &serde_json::json!({
                "ref": ref_id,
                "expected_epoch": epoch0,
                "idempotency_key": "bench-idem-stale"
            }),
            &mut session,
            &store,
        )
        .await;
        assert!(stale.is_err());
    }
    let stale_fence_us = t_stale.elapsed().as_secs_f64() * 1_000_000.0 / 1_000.0;
    println!(
        "action_gateway: page_load_ms={:.2} live_act_ms={:.2} idempotent_replay_us={:.2} stale_epoch_fence_us={:.2}",
        load_ms, live_act_ms, idem_replay_us, stale_fence_us
    );

    // 3. Causal Trace (`caused_by` propagation to Network + Console + DOM Delta)
    let inv2 = execute_command("actions", &serde_json::json!({}), &mut session, &store).await.unwrap();
    let ref_id2 = inv2["actions"][0]["ref_id"].as_str().unwrap().to_string();
    let t_trace = Instant::now();
    let tr = execute_command("trace", &serde_json::json!({ "ref": ref_id2 }), &mut session, &store)
        .await
        .unwrap();
    let trace_ms = t_trace.elapsed().as_secs_f64() * 1000.0;
    assert_eq!(tr["network"].as_array().unwrap().len(), 1);
    assert_eq!(tr["console"].as_array().unwrap().len(), 1);
    println!(
        "causal_trace: trace_bundle_ms={:.2} caused_net={} caused_console={}",
        trace_ms,
        tr["network"].as_array().unwrap().len(),
        tr["console"].as_array().unwrap().len()
    );

    // 4. Live Heap Telemetry + Canvas2Digest + Child Frame Eval
    let t_heap = Instant::now();
    let hp = execute_command("heap", &serde_json::json!({}), &mut session, &store).await.unwrap();
    let heap_us = t_heap.elapsed().as_secs_f64() * 1_000_000.0;

    let t_cd = Instant::now();
    let cd = execute_command("canvas2digest", &serde_json::json!({}), &mut session, &store).await.unwrap();
    let canvas_digest_us = t_cd.elapsed().as_secs_f64() * 1_000_000.0;

    let t_fe = Instant::now();
    let fe = execute_command(
        "frame.eval",
        &serde_json::json!({ "frame": "frame-1", "expr": "document.title + ':' + window.__w" }),
        &mut session,
        &store,
    )
    .await
    .unwrap();
    let frame_eval_cold_ms = t_fe.elapsed().as_secs_f64() * 1000.0;
    assert_eq!(fe["result"], "WidgetFrame:777");

    let t_fe_warm = Instant::now();
    let _ = execute_command(
        "frame.eval",
        &serde_json::json!({ "frame": "frame-1", "expr": "window.__w + 1" }),
        &mut session,
        &store,
    )
    .await
    .unwrap();
    let frame_eval_warm_us = t_fe_warm.elapsed().as_secs_f64() * 1_000_000.0;
    println!(
        "live_inspection: heap_query_us={:.1} used_heap_kb={} canvas2digest_us={:.1} dominant_color={} frame_eval_cold_ms={:.2} frame_eval_warm_us={:.1}",
        heap_us,
        hp["used_heap_bytes"].as_u64().unwrap_or(0) / 1024,
        canvas_digest_us,
        cd["canvases"][0]["dominant_palette"][0]["color"].as_str().unwrap_or(""),
        frame_eval_cold_ms,
        frame_eval_warm_us
    );

    // 5. NativeCrypto (`crypto.subtle`) & Shadow DOM `>>>` Piercing Throughput
    let mut rt = Runtime::new(client, "https://example.com").unwrap();
    rt.load_document(
        r#"<html><body><div id="host"><template shadowrootmode="open"><span id="inner-target">PiercedOK</span></template></div></body></html>"#,
        "https://example.com",
    )
    .unwrap();

    let t_shadow = Instant::now();
    let sh_res = rt
        .eval(r#"(function(){ var n=0; for(var i=0;i<500;i++){ var el=document.querySelector('#host >>> #inner-target'); if(el) n++; } return n; })()"#)
        .unwrap();
    let shadow_us_per_query = t_shadow.elapsed().as_secs_f64() * 1_000_000.0 / 500.0;
    assert_eq!(sh_res, "500");

    let t_crypto = Instant::now();
    rt.eval(
        r#"(function(){
            var enc = new TextEncoder();
            var rawKey = new Uint8Array(32);
            var iv = new Uint8Array(12);
            var data = enc.encode("benchmark-payload-for-native-ring-crypto");
            globalThis.__benchCryptoDone = 0;
            crypto.subtle.importKey("raw", rawKey, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]).then(function(hKey) {
                return crypto.subtle.importKey("raw", rawKey, { name: "AES-GCM" }, false, ["encrypt", "decrypt"]).then(function(aKey) {
                    var p = Promise.resolve();
                    for (var i = 0; i < 100; i++) {
                        p = p.then(function() {
                            return crypto.subtle.sign("HMAC", hKey, data);
                        }).then(function() {
                            return crypto.subtle.encrypt({ name: "AES-GCM", iv: iv }, aKey, data);
                        }).then(function(ct) {
                            return crypto.subtle.decrypt({ name: "AES-GCM", iv: iv }, aKey, ct);
                        }).then(function() {
                            globalThis.__benchCryptoDone++;
                        });
                    }
                });
            });
        })()"#,
    )
    .unwrap();
    rt.engine.pump_jobs();
    let crypto_total_ms = t_crypto.elapsed().as_secs_f64() * 1000.0;
    let done = rt.eval("String(globalThis.__benchCryptoDone)").unwrap();
    assert_eq!(done, "100");
    println!(
        "crypto_and_shadow: shadow_pierce_us_per_query={:.2} crypto_100_hmac_aes_gcm_roundtrips_ms={:.2} per_op_us={:.2}",
        shadow_us_per_query,
        crypto_total_ms,
        (crypto_total_ms * 1000.0) / 300.0
    );

    // 6. ACD v2 Wikipedia-Class Reference Recovery + JSON-LD + Frame Projection
    let mut wiki_html = String::from("<html><body><main><h1>Web Browser</h1>");
    for i in 1..=40 {
        wiki_html.push_str(&format!("<p>Article paragraph #{i} with substantive encyclopedia prose.</p>"));
    }
    wiki_html.push_str("<ol class=\"mw-references references\">");
    for i in 1..=200 {
        wiki_html.push_str(&format!("<li>Reference citation #{i}: Journal of Agentic Systems (2026).</li>"));
    }
    wiki_html.push_str("</ol>");
    wiki_html.push_str(r#"<iframe srcdoc="<p>Interactive citation viewer frame</p>"></iframe>"#);
    wiki_html.push_str(r#"<script type="application/ld+json">{"@type":"Article","headline":"Web Browser Architecture","articleBody":"Compiled agent representation."}</script>"#);
    wiki_html.push_str("</main></body></html>");

    let t_acd_def = Instant::now();
    let acd_def = build_acd("https://en.wikipedia.org/wiki/Web_browser", "Web Browser", &wiki_html, &[], &[], &[], &serde_json::json!({}), 500);
    let acd_def_ms = t_acd_def.elapsed().as_secs_f64() * 1000.0;

    let t_acd_refs = Instant::now();
    let acd_refs = build_acd(
        "https://en.wikipedia.org/wiki/Web_browser",
        "Web Browser",
        &wiki_html,
        &[],
        &[],
        &[],
        &serde_json::json!({"include_references": true}),
        500,
    );
    let acd_refs_ms = t_acd_refs.elapsed().as_secs_f64() * 1000.0;
    println!(
        "acd_v2_wikipedia_fixture: default_blocks={} (ms={:.2}) with_references_blocks={} (ms={:.2}) recovered_citations={}",
        acd_def.blocks.len(),
        acd_def_ms,
        acd_refs.blocks.len(),
        acd_refs_ms,
        acd_refs.blocks.len().saturating_sub(acd_def.blocks.len())
    );
}
