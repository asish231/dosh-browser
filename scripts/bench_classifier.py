"""Comprehensive Python benchmark for ACD 2.0 Micro-Classifier and Subtree Clustering."""

import time
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from webresearch.acd import build_acd
from webresearch.provenance import (
    classify_node,
    cluster_siblings,
    extract_features,
    extract_provenance,
)


def generate_synthetic_page(num_articles: int, num_results: int, num_cards: int) -> str:
    html = ["<!DOCTYPE html><html><head><title>Benchmark Test Corpus</title></head><body>"]
    html.append("<header><nav class='navbar'><a href='/home'>Home</a><a href='/about'>About</a><a href='/services'>Services</a><a href='/contact'>Contact</a><a href='/privacy'>Privacy</a></nav></header>")
    html.append("<main id='main-content'>")

    for i in range(num_articles):
        html.append(
            f"<article id='article-{i}' class='post-body'><h2>Legal Decision on Contract Doctrine #{i}</h2>"
            "<p>In accordance with landmark common law precedents, agreements between family members or spouses are strongly presumed to lack legal consequence unless clear intent is manifested by external evidence or written deed.</p>"
            "<p>Lord Justice Atkin famously enunciated in Balfour v Balfour [1919] 2 KB 571 that mutual promises of domestic arrangements cannot flood courts with endless litigation.</p>"
            f"<a href='/precedent/{i}'>Case Citation</a></article>"
        )

    html.append("<div id='search-results-container' class='search-list'>")
    for i in range(num_results):
        html.append(
            f"<div class='search-hit'><h3>Search Hit Result #{i}</h3>"
            "<p>Detailed snippet explaining the historical evolution of consideration and privity in contract law.</p>"
            f"<a href='/result/{i}'>View Full Judgment</a></div>"
        )
    html.append("</div>")

    html.append("<div id='products-catalog' class='product-grid'>")
    for i in range(num_cards):
        html.append(
            f"<div class='product-card'><img src='/img/{i}.jpg' alt='Product {i}'><h4>Law Treatise Volume #{i}</h4>"
            f"<span class='price'>$149.99</span><button>Add to Cart</button></div>"
        )
    html.append("</div>")

    html.append("</main><footer id='footer' class='footer-nav'><p>Copyright 2026 Legal Research Corpus Inc. All rights reserved.</p><div class='ad-banner'><p>Advertisement: Best Law Books</p></div></footer></body></html>")
    return "".join(html)


def main():
    print("============================================================")
    print("     ADVANCED ACD 2.0 & MICRO-CLASSIFIER BENCHMARK (PYTHON) ")
    print("============================================================")

    # 1. Accuracy & Confusion Matrix Test
    print("\n[1] Evaluating Classification Accuracy & Confusion Matrix...")
    test_html = generate_synthetic_page(10, 20, 10)
    tree = HTMLParser(test_html)

    articles = tree.css("article")
    pred_article = sum(1 for a in articles if classify_node(extract_features(a))[0] == "ArticleBody")
    
    navs = tree.css("nav")
    pred_nav = sum(1 for n in navs if classify_node(extract_features(n))[0] == "Navigation")

    cards = tree.css("div.product-card")
    pred_card = sum(1 for c in cards if classify_node(extract_features(c))[0] == "ProductCard")

    print(f"  - ArticleBody Accuracy:   {pred_article}/{len(articles)} ({(pred_article / len(articles)) * 100:.1f}%)")
    print(f"  - Navigation Accuracy:    {pred_nav}/{len(navs)} ({(pred_nav / len(navs)) * 100:.1f}%)")
    print(f"  - ProductCard Accuracy:   {pred_card}/{len(cards)} ({(pred_card / len(cards)) * 100:.1f}%)")

    # 2. Sibling Clustering & Collection Discovery
    print("\n[2] Evaluating Sibling Subtree Clustering...")
    search_container = tree.css_first("#search-results-container")
    collections = cluster_siblings(search_container, 1)
    print(f"  - Discovered Collections: {len(collections)}")
    if collections:
        print(f"  - Collection #1: Category='{collections[0].category}', Sibling Count={collections[0].count}, Name='{collections[0].name}'")

    # 3. Inference Throughput & Latency Benchmark
    print("\n[3] Measuring Micro-Classifier Latency & Throughput...")
    large_html = generate_synthetic_page(50, 100, 50)
    large_tree = HTMLParser(large_html)
    nodes = large_tree.css("article, div, nav, footer, p, h2, h3, h4")
    total_nodes = len(nodes)

    # Warmup
    for n in nodes:
        feats = extract_features(n)
        _ = classify_node(feats)

    iterations = 5
    start = time.perf_counter()
    for _ in range(iterations):
        for n in nodes:
            feats = extract_features(n)
            _ = classify_node(feats)
            _ = extract_provenance(n)
    elapsed = time.perf_counter() - start
    total_evals = total_nodes * iterations
    per_node_us = (elapsed * 1_000_000.0) / total_evals
    throughput = total_evals / elapsed

    print(f"  - Evaluated Nodes: {total_nodes} across {iterations} iterations ({total_evals} total operations)")
    print(f"  - Total Elapsed Time: {elapsed * 1000.0:.2f} ms")
    print(f"  - Latency Per Node (Extract + Classify + Provenance): {per_node_us:.2f} µs")
    print(f"  - Classification Throughput: {throughput:.0f} nodes/second")

    # 4. End-to-End ACD 2.0 Build Latency
    print("\n[4] Measuring End-to-End ACD 2.0 Build Latency...")
    start_build = time.perf_counter()
    acd_iterations = 10
    for _ in range(acd_iterations):
        _ = build_acd(url="https://bench.test", title="Bench Title", html=large_html, max_blocks=500)
    acd_elapsed = time.perf_counter() - start_build
    per_page_ms = (acd_elapsed * 1000.0) / acd_iterations

    print(f"  - End-to-End ACD 2.0 Time (Parse + Filter + ML + Provenance + Cluster): {per_page_ms:.2f} ms per full page")
    print("\n============================================================")
    print("             BENCHMARK COMPLETED SUCCESSFULLY               ")
    print("============================================================")


if __name__ == "__main__":
    main()

