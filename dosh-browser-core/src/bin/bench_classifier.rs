use std::time::Instant;
use kuchiki::traits::TendrilSink;

use dosh_browser_core::features::extract_features;
use dosh_browser_core::classifier::{classify_node, SemanticCategory};
use dosh_browser_core::cluster::cluster_siblings;
use dosh_browser_core::provenance::extract_provenance;
use dosh_browser_core::acd::build_acd;

fn generate_synthetic_page(num_articles: usize, num_results: usize, num_cards: usize) -> String {
    let mut html = String::from("<!DOCTYPE html><html><head><title>Benchmark Test Corpus</title></head><body>");
    
    // Header & Navigation chrome
    html.push_str("<header><nav class='navbar'><a href='/home'>Home</a><a href='/about'>About</a><a href='/services'>Services</a><a href='/contact'>Contact</a><a href='/privacy'>Privacy</a></nav></header>");
    
    // Main content area
    html.push_str("<main id='main-content'>");
    
    // Articles (long prose, paragraphs, citations)
    for i in 0..num_articles {
        html.push_str(&format!(
            "<article id='article-{}' class='post-body'><h2>Legal Decision on Contract Doctrine #{}</h2><p>In accordance with landmark common law precedents, agreements between family members or spouses are strongly presumed to lack legal consequence unless clear intent is manifested by external evidence or written deed.</p><p>Lord Justice Atkin famously enunciated in Balfour v Balfour [1919] 2 KB 571 that mutual promises of domestic arrangements cannot flood courts with endless litigation.</p><a href='/precedent/{}'>Case Citation</a></article>",
            i, i, i
        ));
    }
    
    // Search Results (repeating sibling cards with h3, p, a)
    html.push_str("<div id='search-results-container' class='search-list'>");
    for i in 0..num_results {
        html.push_str(&format!(
            "<div class='search-hit'><h3>Search Hit Result #{}</h3><p>Detailed snippet explaining the historical evolution of consideration and privity in contract law.</p><a href='/result/{}'>View Full Judgment</a></div>",
            i, i
        ));
    }
    html.push_str("</div>");
    
    // Product Cards (images, prices, buy buttons)
    html.push_str("<div id='products-catalog' class='product-grid'>");
    for i in 0..num_cards {
        html.push_str(&format!(
            "<div class='product-card'><img src='/img/{}.jpg' alt='Product {}'><h4>Law Treatise Volume #{}</h4><span class='price'>$149.99</span><button>Add to Cart</button></div>",
            i, i, i
        ));
    }
    html.push_str("</div>");
    
    // Footer boilerplate & ads
    html.push_str("</main><footer id='footer' class='footer-nav'><p>Copyright 2026 Legal Research Corpus Inc. All rights reserved.</p><div class='ad-banner'><p>Advertisement: Best Law Books</p></div></footer></body></html>");
    
    html
}

fn main() {
    println!("============================================================");
    println!("     ADVANCED ACD 2.0 & MICRO-CLASSIFIER BENCHMARK SUITE    ");
    println!("============================================================");

    // 1. Accuracy & Confusion Matrix Test
    println!("\n[1] Evaluating Classification Accuracy & Confusion Matrix...");
    let test_html = generate_synthetic_page(10, 20, 10);
    let doc = kuchiki::parse_html().one(test_html.as_str());

    let mut true_article = 0;
    let mut pred_article = 0;
    let mut true_nav = 0;
    let mut pred_nav = 0;
    let mut true_prod = 0;
    let mut pred_prod = 0;
    let mut confidences: Vec<f64> = Vec::new();

    for elem in doc.select("article").unwrap() {
        true_article += 1;
        let feats = extract_features(elem.as_node());
        let res = classify_node(&feats);
        if res.category == SemanticCategory::ArticleBody {
            pred_article += 1;
            confidences.push(res.confidence);
        }
    }

    for elem in doc.select("nav").unwrap() {
        true_nav += 1;
        let feats = extract_features(elem.as_node());
        let res = classify_node(&feats);
        if res.category == SemanticCategory::Navigation {
            pred_nav += 1;
            confidences.push(res.confidence);
        }
    }

    for elem in doc.select("div.product-card").unwrap() {
        true_prod += 1;
        let feats = extract_features(elem.as_node());
        let res = classify_node(&feats);
        if res.category == SemanticCategory::ProductCard {
            pred_prod += 1;
            confidences.push(res.confidence);
        }
    }

    let avg_conf: f64 = if !confidences.is_empty() {
        confidences.iter().sum::<f64>() / (confidences.len() as f64)
    } else { 0.0 };

    println!("  - ArticleBody Accuracy:   {}/{} ({:.1}%)", pred_article, true_article, (pred_article as f64 / true_article as f64) * 100.0);
    println!("  - Navigation Accuracy:    {}/{} ({:.1}%)", pred_nav, true_nav, (pred_nav as f64 / true_nav as f64) * 100.0);
    println!("  - ProductCard Accuracy:   {}/{} ({:.1}%)", pred_prod, true_prod, (pred_prod as f64 / true_prod as f64) * 100.0);
    println!("  - Mean Classifier Confidence: {:.3}", avg_conf);

    // 2. Sibling Clustering & Collection Discovery
    println!("\n[2] Evaluating Sibling Subtree Clustering...");
    let search_container = doc.select("#search-results-container").unwrap().next().unwrap();
    let collections = cluster_siblings(search_container.as_node(), 1);
    println!("  - Discovered Collections: {}", collections.len());
    if !collections.is_empty() {
        println!("  - Collection #1: Category='{}', Sibling Count={}, Name='{}'", 
            collections[0].category, collections[0].count, collections[0].name);
    }

    // 3. Inference Throughput & Latency Benchmark
    println!("\n[3] Measuring Micro-Classifier Latency & Throughput...");
    let large_html = generate_synthetic_page(50, 100, 50);
    let large_doc = kuchiki::parse_html().one(large_html.as_str());

    let nodes: Vec<_> = large_doc.select("article, div, nav, footer, p, h2, h3, h4").unwrap().map(|e| e.as_node().clone()).collect();
    let total_nodes = nodes.len();

    // Warmup
    for n in &nodes {
        let feats = extract_features(n);
        let _ = classify_node(&feats);
    }

    let iterations = 10;
    let start = Instant::now();
    for _ in 0..iterations {
        for n in &nodes {
            let feats = extract_features(n);
            let _ = classify_node(&feats);
            let _ = extract_provenance(n);
        }
    }
    let duration = start.elapsed();
    let total_evals = total_nodes * iterations;
    let per_node_us = duration.as_secs_f64() * 1_000_000.0 / (total_evals as f64);
    let throughput = (total_evals as f64) / duration.as_secs_f64();

    println!("  - Evaluated Nodes: {} across {} iterations ({} total operations)", total_nodes, iterations, total_evals);
    println!("  - Total Elapsed Time: {:?}", duration);
    println!("  - Latency Per Node (Extract + Classify + Provenance): {:.2} µs", per_node_us);
    println!("  - Classification Throughput: {:.0} nodes/second", throughput);

    // 4. End-to-End ACD 2.0 Generation Benchmark
    println!("\n[4] Measuring End-to-End ACD 2.0 Build Latency...");
    let start_build = Instant::now();
    let acd_iterations = 20;
    for _ in 0..acd_iterations {
        let _ = build_acd("https://bench.test", "Bench Title", &large_html, &[], &[], &[], &serde_json::json!({}), 500);
    }
    let acd_duration = start_build.elapsed();
    let per_page_ms = (acd_duration.as_secs_f64() * 1000.0) / (acd_iterations as f64);

    println!("  - End-to-End ACD 2.0 Time (Parse + Filter + ML + Provenance + Cluster): {:.2} ms per full page", per_page_ms);
    println!("\n============================================================");
    println!("             BENCHMARK COMPLETED SUCCESSFULLY               ");
    println!("============================================================");
}

