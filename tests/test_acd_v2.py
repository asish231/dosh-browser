"""Tests for Advanced ACD 2.0 and Micro-Classifier in Python."""

import pytest
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from webresearch.acd import build_acd
from webresearch.provenance import (
    classify_node,
    cluster_siblings,
    compute_css_path,
    compute_xpath,
    extract_features,
    extract_provenance,
)


def test_provenance_css_and_xpath():
    html = """
    <html>
      <body>
        <main id="main">
          <article class="post">
            <h1>Case Law</h1>
            <p>First paragraph</p>
            <p>Second paragraph</p>
          </article>
        </main>
      </body>
    </html>
    """
    tree = HTMLParser(html)
    p2 = tree.css("p")[1]

    css = compute_css_path(p2)
    assert "main#main" in css
    assert "p:nth-of-type(2)" in css

    xpath = compute_xpath(p2)
    assert "/html[1]/body[1]/main[1]" in xpath
    assert "p[2]" in xpath

    prov = extract_provenance(p2)
    assert prov.tag == "p"
    assert prov.css_path == css
    assert prov.xpath == xpath


def test_classifier_article_vs_nav():
    html_article = """
    <article class="post-content">
      <h1>Headline</h1>
      <p>This is a long legal discussion regarding agreements between parties and intent to create legal consequences.</p>
      <p>In Balfour v Balfour, Atkin LJ emphasized the doctrine of domestic arrangements.</p>
    </article>
    """
    tree = HTMLParser(html_article)
    article_node = tree.css("article")[0]
    feats = extract_features(article_node)
    cat, conf = classify_node(feats)
    assert cat == "ArticleBody"
    assert conf >= 0.80

    html_nav = """
    <nav class="navbar">
      <a href="/home">Home</a>
      <a href="/about">About</a>
      <a href="/contact">Contact</a>
      <a href="/terms">Terms</a>
    </nav>
    """
    tree_nav = HTMLParser(html_nav)
    nav_node = tree_nav.css("nav")[0]
    feats_nav = extract_features(nav_node)
    cat_nav, conf_nav = classify_node(feats_nav)
    assert cat_nav == "Navigation"
    assert conf_nav >= 0.85


def test_cluster_siblings():
    html = """
    <div id="results">
      <div class="hit"><h3>Result 1</h3><p>Snippet 1</p><a href="/1">Link 1</a></div>
      <div class="hit"><h3>Result 2</h3><p>Snippet 2</p><a href="/2">Link 2</a></div>
      <div class="hit"><h3>Result 3</h3><p>Snippet 3</p><a href="/3">Link 3</a></div>
    </div>
    """
    tree = HTMLParser(html)
    parent = tree.css("#results")[0]
    cols = cluster_siblings(parent, 1)
    assert len(cols) == 1
    assert cols[0].category == "SearchResults"
    assert cols[0].count == 3


def test_build_acd_v2_integration():
    html = """
    <html>
      <body>
        <main id="main">
          <article class="post">
            <h1>Balfour Case Analysis</h1>
            <p>A landmark English contract law decision on intention to create legal relations.</p>
          </article>
          <div id="results">
            <div class="card"><h3>Item 1</h3><p>Desc 1</p><a href="/1">1</a></div>
            <div class="card"><h3>Item 2</h3><p>Desc 2</p><a href="/2">2</a></div>
            <div class="card"><h3>Item 3</h3><p>Desc 3</p><a href="/3">3</a></div>
          </div>
        </main>
      </body>
    </html>
    """
    acd = build_acd(url="https://law.example.com", title="Law Title", html=html)
    assert acd.version == "2"
    assert len(acd.blocks) > 0

    # Block provenance & classification
    h1_block = next(b for b in acd.blocks if b.type == "heading")
    assert h1_block.provenance is not None
    assert h1_block.provenance.tag == "h1"
    assert "main#main" in h1_block.provenance.css_path

    p_block = next(b for b in acd.blocks if b.type == "text")
    assert p_block.semantic_category == "ArticleBody"
    assert p_block.confidence is not None and p_block.confidence > 0.5

    # Collections
    assert len(acd.collections) > 0
    col = acd.collections[0]
    assert col.category == "SearchResults"
    assert col.count == 3

