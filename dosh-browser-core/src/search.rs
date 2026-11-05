use base64::prelude::*;
use kuchiki::traits::TendrilSink;
use reqwest::Client;
use url::Url;

use crate::content::text_content;
use crate::fetch;
use crate::types::{SearchHit, SearchResult};

pub const DDG_HTML_URL: &str = "https://html.duckduckgo.com/html/";
pub const DDG_LITE_URL: &str = "https://lite.duckduckgo.com/lite/";
pub const BING_SEARCH_URL: &str = "https://www.bing.com/search";
pub const GOOGLE_SEARCH_URL: &str = "https://www.google.com/search";
pub const YAHOO_SEARCH_URL: &str = "https://search.yahoo.com/search";

pub fn unwrap_ddg(raw_url: &str) -> String {
    let full_url = if raw_url.starts_with('/') {
        format!("https://duckduckgo.com{}", raw_url)
    } else {
        raw_url.to_string()
    };
    if let Ok(parsed) = Url::parse(&full_url) {
        if parsed.host_str().map(|h| h.contains("duckduckgo.com")).unwrap_or(false)
            && parsed.path().starts_with("/l/")
        {
            for (k, v) in parsed.query_pairs() {
                if k == "uddg" {
                    return v.to_string();
                }
            }
        }
    }
    raw_url.to_string()
}

pub fn unwrap_bing(raw_url: &str) -> String {
    if let Ok(parsed) = Url::parse(raw_url) {
        if parsed.host_str().map(|h| h.contains("bing.com")).unwrap_or(false)
            && parsed.path().starts_with("/ck/a")
        {
            for (k, v) in parsed.query_pairs() {
                if k == "u" && v.starts_with("a1") {
                    let b64_part = &v[2..];
                    let mut b64_str = b64_part.to_string();
                    let rem = b64_str.len() % 4;
                    if rem > 0 {
                        b64_str.push_str(&"=".repeat(4 - rem));
                    }
                    if let Ok(decoded) = BASE64_STANDARD.decode(b64_str.as_bytes()) {
                        if let Ok(target) = String::from_utf8(decoded) {
                            if target.starts_with("http://") || target.starts_with("https://") {
                                return target;
                            }
                        }
                    }
                }
            }
        }
    }
    raw_url.to_string()
}

pub fn unwrap_google(raw_url: &str) -> Option<String> {
    if raw_url.is_empty() || raw_url.starts_with('#') {
        return None;
    }
    let full_url = if raw_url.starts_with('/') {
        format!("https://www.google.com{}", raw_url)
    } else {
        raw_url.to_string()
    };
    if let Ok(parsed) = Url::parse(&full_url) {
        if parsed.path() == "/url" {
            for (k, v) in parsed.query_pairs() {
                if k == "q" || k == "url" {
                    let target = v.to_string();
                    if target.starts_with("http://") || target.starts_with("https://") {
                        let low = target.to_lowercase();
                        if !low.contains("google.com/search") && !low.contains("google.com/sorry") {
                            return Some(target);
                        }
                    }
                }
            }
        } else if (parsed.scheme() == "http" || parsed.scheme() == "https")
            && !parsed.host_str().map(|h| h.contains("google.")).unwrap_or(false)
        {
            return Some(full_url);
        }
    }
    None
}

pub fn is_ddg_bot_challenge(body: &str, status: u16) -> bool {
    if status == 202 {
        return true;
    }
    let low = body.to_lowercase();
    low.contains("botnet_challenge")
        || low.contains("unfortunately, bots use duckduckgo too")
        || low.contains("anomaly.js")
        || (low.contains("challenge") && low.contains("vqd") && low.contains("proof"))
}

pub fn parse_ddg_html(html: &str, max_hits: usize) -> Vec<SearchHit> {
    let document = kuchiki::parse_html().one(html);
    let mut hits = Vec::new();

    let Ok(results) = document.select(".result") else {
        return hits;
    };

    for (idx, result) in results.enumerate() {
        if hits.len() >= max_hits {
            break;
        }

        let node = result.as_node();

        let mut title = String::new();
        let mut url_str = String::new();

        if let Ok(a_node) = node.select_first("a.result__a, .result__title a, a") {
            title = text_content(a_node.as_node()).trim().to_string();
            if let Some(elem) = a_node.as_node().as_element() {
                if let Some(href) = elem.attributes.borrow().get("href") {
                    url_str = unwrap_ddg(href);
                }
            }
        }

        if title.is_empty() || url_str.is_empty() {
            continue;
        }

        let mut snippet = String::new();
        if let Ok(snippet_node) = node.select_first(".result__snippet") {
            snippet = text_content(snippet_node.as_node()).trim().to_string();
        }

        hits.push(SearchHit {
            n: (idx + 1) as i64,
            title,
            url: url_str,
            snippet,
        });
    }

    hits
}

pub fn parse_ddg_lite_html(html: &str, max_hits: usize) -> Vec<SearchHit> {
    let document = kuchiki::parse_html().one(html);
    let mut hits = Vec::new();

    let Ok(links) = document.select("a.result-link") else {
        return hits;
    };
    let snippets: Vec<String> = if let Ok(snips) = document.select("td.result-snippet") {
        snips.map(|s| text_content(s.as_node()).trim().to_string()).collect()
    } else {
        Vec::new()
    };

    for (idx, a_node) in links.enumerate() {
        if hits.len() >= max_hits {
            break;
        }
        let title = text_content(a_node.as_node()).trim().to_string();
        let mut url_str = String::new();
        if let Some(elem) = a_node.as_node().as_element() {
            if let Some(href) = elem.attributes.borrow().get("href") {
                url_str = unwrap_ddg(href);
            }
        }
        if title.is_empty() || url_str.is_empty() {
            continue;
        }
        let snippet = snippets.get(idx).cloned().unwrap_or_default();
        hits.push(SearchHit {
            n: (hits.len() + 1) as i64,
            title,
            url: url_str,
            snippet,
        });
    }

    hits
}

pub fn parse_bing_html(html: &str, max_hits: usize) -> Vec<SearchHit> {
    let document = kuchiki::parse_html().one(html);
    let mut hits = Vec::new();
    let Ok(results) = document.select("li.b_algo") else {
        return hits;
    };
    let mut seen_urls = std::collections::HashSet::new();

    for result in results {
        if hits.len() >= max_hits {
            break;
        }
        let node = result.as_node();
        let mut title = String::new();
        let mut url_str = String::new();

        if let Ok(h2_a) = node.select_first("h2 a, a") {
            title = text_content(h2_a.as_node()).trim().to_string();
            if let Some(elem) = h2_a.as_node().as_element() {
                if let Some(href) = elem.attributes.borrow().get("href") {
                    url_str = unwrap_bing(href);
                }
            }
        }

        if title.is_empty() || url_str.is_empty() || seen_urls.contains(&url_str) {
            continue;
        }
        seen_urls.insert(url_str.clone());

        let mut snippet = String::new();
        if let Ok(p_node) = node.select_first("div.b_caption p, p.b_lineclamp, p") {
            snippet = text_content(p_node.as_node()).trim().to_string();
        }

        hits.push(SearchHit {
            n: (hits.len() + 1) as i64,
            title,
            url: url_str,
            snippet,
        });
    }

    hits
}

pub fn parse_google_html(html: &str, max_hits: usize) -> Vec<SearchHit> {
    let document = kuchiki::parse_html().one(html);
    let mut hits = Vec::new();
    let Ok(cards) = document.select("div.g, div.Gx5Zad, div.ezO2md, div[data-sokoban-container]") else {
        return hits;
    };
    let mut seen_urls = std::collections::HashSet::new();

    for card in cards {
        if hits.len() >= max_hits {
            break;
        }
        let node = card.as_node();
        let mut title = String::new();
        let mut url_str = String::new();

        if let Ok(a_node) = node.select_first("a[href]") {
            if let Some(elem) = a_node.as_node().as_element() {
                if let Some(href) = elem.attributes.borrow().get("href") {
                    if let Some(unwrapped) = unwrap_google(href) {
                        url_str = unwrapped;
                    }
                }
            }
            if let Ok(h3_node) = a_node.as_node().select_first("h3") {
                title = text_content(h3_node.as_node()).trim().to_string();
            } else {
                title = text_content(a_node.as_node()).trim().to_string();
            }
        }

        if title.is_empty() || url_str.is_empty() || seen_urls.contains(&url_str) {
            continue;
        }
        seen_urls.insert(url_str.clone());

        let mut snippet = String::new();
        if let Ok(sn_node) = node.select_first("div.VwiC3b, span.aCOpRe, div.s, div.FrI1Sb, span.st") {
            snippet = text_content(sn_node.as_node()).trim().to_string();
        }

        hits.push(SearchHit {
            n: (hits.len() + 1) as i64,
            title,
            url: url_str,
            snippet,
        });
    }

    hits
}

pub fn parse_yahoo_html(html: &str, max_hits: usize) -> Vec<SearchHit> {
    let document = kuchiki::parse_html().one(html);
    let mut hits = Vec::new();
    let Ok(cards) = document.select("div.algo-sr, div.algo") else {
        return hits;
    };
    let mut seen_urls = std::collections::HashSet::new();

    for card in cards {
        if hits.len() >= max_hits {
            break;
        }
        let node = card.as_node();
        let mut title = String::new();
        let mut url_str = String::new();

        if let Ok(a_node) = node.select_first("h3 a, div.compTitle a, a.d-ib, a[href]") {
            title = text_content(a_node.as_node()).trim().to_string();
            if let Some(elem) = a_node.as_node().as_element() {
                if let Some(href) = elem.attributes.borrow().get("href") {
                    url_str = href.to_string();
                }
            }
        }

        if title.is_empty() || url_str.is_empty() || seen_urls.contains(&url_str) {
            continue;
        }
        seen_urls.insert(url_str.clone());

        let mut snippet = String::new();
        if let Ok(p_node) = node.select_first("div.compText, p") {
            snippet = text_content(p_node.as_node()).trim().to_string();
        }

        hits.push(SearchHit {
            n: (hits.len() + 1) as i64,
            title,
            url: url_str,
            snippet,
        });
    }

    hits
}

pub fn format_search_view(query: &str, engine: &str, hits: &[SearchHit]) -> String {
    let mut out = format!("## Search Results for \"{}\" (via {})\n\n", query, engine);
    if hits.is_empty() {
        out.push_str("No results found.\n");
        return out;
    }

    for hit in hits {
        out.push_str(&format!("{}. [{}]({})\n", hit.n, hit.title, hit.url));
        if !hit.snippet.is_empty() {
            out.push_str(&format!("   {}\n", hit.snippet));
        }
        out.push('\n');
    }

    out
}

fn url_encode(input: &str) -> String {
    url::form_urlencoded::byte_serialize(input.as_bytes()).collect()
}

async fn fetch_bing(client: &Client, query: &str, max_hits: usize) -> Option<Vec<SearchHit>> {
    let url = format!("{}?q={}&setlang=en", BING_SEARCH_URL, url_encode(query));
    let headers_json = r#"{
        "Referer": "https://www.bing.com/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9"
    }"#;
    if let Ok(res) = fetch::fetch(client, &url, "GET", None, headers_json, 4_000_000).await {
        if res.status == 200 {
            let hits = parse_bing_html(&res.body, max_hits);
            if !hits.is_empty() {
                return Some(hits);
            }
        }
    }
    None
}

async fn fetch_google(client: &Client, query: &str, max_hits: usize) -> Option<Vec<SearchHit>> {
    let url = format!("{}?q={}&hl=en&gl=us&num={}", GOOGLE_SEARCH_URL, url_encode(query), max_hits);
    let headers_json = r#"{
        "Referer": "https://www.google.com/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "same-origin",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1"
    }"#;
    if let Ok(res) = fetch::fetch(client, &url, "GET", None, headers_json, 4_000_000).await {
        if res.status == 200 && !res.body.contains("meta content=\"0;url=/httpservice/retry/enablejs") {
            let hits = parse_google_html(&res.body, max_hits);
            if !hits.is_empty() {
                return Some(hits);
            }
        }
    }
    None
}

async fn fetch_ddg(client: &Client, query: &str, max_hits: usize) -> Option<Vec<SearchHit>> {
    let body_str = {
        let mut serializer = url::form_urlencoded::Serializer::new(String::new());
        serializer.append_pair("q", query);
        serializer.append_pair("b", "");
        serializer.finish()
    };
    let headers_json = r#"{
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://html.duckduckgo.com/"
    }"#;
    if let Ok(res) = fetch::fetch(client, DDG_HTML_URL, "POST", Some(body_str), headers_json, 4_000_000).await {
        if !is_ddg_bot_challenge(&res.body, res.status) {
            let hits = parse_ddg_html(&res.body, max_hits);
            if !hits.is_empty() {
                return Some(hits);
            }
        }
    }
    None
}

async fn fetch_ddg_lite(client: &Client, query: &str, max_hits: usize) -> Option<Vec<SearchHit>> {
    let body_str = {
        let mut serializer = url::form_urlencoded::Serializer::new(String::new());
        serializer.append_pair("q", query);
        serializer.append_pair("kl", "us-en");
        serializer.finish()
    };
    let headers_json = r#"{
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://lite.duckduckgo.com/"
    }"#;
    if let Ok(res) = fetch::fetch(client, DDG_LITE_URL, "POST", Some(body_str), headers_json, 4_000_000).await {
        if !is_ddg_bot_challenge(&res.body, res.status) {
            let hits = parse_ddg_lite_html(&res.body, max_hits);
            if !hits.is_empty() {
                return Some(hits);
            }
        }
    }
    None
}

async fn fetch_yahoo(client: &Client, query: &str, max_hits: usize) -> Option<Vec<SearchHit>> {
    let url = format!("{}?p={}", YAHOO_SEARCH_URL, url_encode(query));
    let headers_json = r#"{
        "Referer": "https://search.yahoo.com/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9"
    }"#;
    if let Ok(res) = fetch::fetch(client, &url, "GET", None, headers_json, 4_000_000).await {
        if res.status == 200 {
            let hits = parse_yahoo_html(&res.body, max_hits);
            if !hits.is_empty() {
                return Some(hits);
            }
        }
    }
    None
}

pub async fn search(
    client: &Client,
    query: &str,
    max_hits: usize,
    engine: &str,
) -> Result<SearchResult, Box<dyn std::error::Error + Send + Sync>> {
    let chosen_engine = if engine.is_empty() { "auto" } else { engine };

    let (actual_engine, hits) = match chosen_engine {
        "bing" => {
            let hits = fetch_bing(client, query, max_hits).await.unwrap_or_default();
            ("bing", hits)
        }
        "google" => {
            let hits = fetch_google(client, query, max_hits).await.unwrap_or_default();
            ("google", hits)
        }
        "ddg" => {
            let hits = fetch_ddg(client, query, max_hits).await
                .or_else(|| None)
                .unwrap_or_default();
            ("ddg", hits)
        }
        "lite" => {
            let hits = fetch_ddg_lite(client, query, max_hits).await.unwrap_or_default();
            ("lite", hits)
        }
        "yahoo" => {
            let hits = fetch_yahoo(client, query, max_hits).await.unwrap_or_default();
            ("yahoo", hits)
        }
        _ => {
            // "auto": Waterfall across Bing -> Google -> DDG -> Yahoo -> DDG Lite
            if let Some(hits) = fetch_bing(client, query, max_hits).await {
                ("bing", hits)
            } else if let Some(hits) = fetch_google(client, query, max_hits).await {
                ("google", hits)
            } else if let Some(hits) = fetch_ddg(client, query, max_hits).await {
                ("ddg", hits)
            } else if let Some(hits) = fetch_yahoo(client, query, max_hits).await {
                ("yahoo", hits)
            } else if let Some(hits) = fetch_ddg_lite(client, query, max_hits).await {
                ("lite", hits)
            } else {
                ("auto", Vec::new())
            }
        }
    };

    let view = format_search_view(query, actual_engine, &hits);

    Ok(SearchResult {
        query: query.to_string(),
        engine: actual_engine.to_string(),
        hits,
        view,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const DDG_SAMPLE: &str = r##"
        <div class="results">
            <div class="result results_links results_links_deep web-result">
                <div class="result__body links_main links_deep">
                    <h2 class="result__title">
                        <a class="result__a" href="/l/?uddg=https%3A%2F%2Fwww.rust-lang.org%2F">Rust Programming Language</a>
                    </h2>
                    <a class="result__snippet" href="#">A language empowering everyone to build reliable and efficient software.</a>
                </div>
            </div>
            <div class="result results_links results_links_deep web-result">
                <div class="result__body links_main links_deep">
                    <h2 class="result__title">
                        <a class="result__a" href="/l/?uddg=https%3A%2F%2Fcrates.io%2F">Crates.io: Rust Package Registry</a>
                    </h2>
                    <a class="result__snippet" href="#">The Rust community crate registry.</a>
                </div>
            </div>
        </div>
    "##;

    #[test]
    fn test_unwrap_ddg() {
        let unwrapped = unwrap_ddg("https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Ftest");
        assert_eq!(unwrapped, "https://example.com/test");

        let direct = unwrap_ddg("https://direct.example.com");
        assert_eq!(direct, "https://direct.example.com");
    }

    #[test]
    fn test_parse_ddg_html() {
        let hits = parse_ddg_html(DDG_SAMPLE, 10);
        assert_eq!(hits.len(), 2);
        assert_eq!(hits[0].title, "Rust Programming Language");
        assert_eq!(hits[0].url, "https://www.rust-lang.org/");
        assert!(hits[0].snippet.contains("empowering everyone"));

        assert_eq!(hits[1].title, "Crates.io: Rust Package Registry");
        assert_eq!(hits[1].url, "https://crates.io/");
    }

    #[test]
    fn test_unwrap_bing() {
        // aHR0cHM6Ly9leGFtcGxlLmNvbS9zb21ldGhpbmc= is "https://example.com/something"
        let bing_url = "https://www.bing.com/ck/a?!&&p=123&u=a1aHR0cHM6Ly9leGFtcGxlLmNvbS9zb21ldGhpbmc&ntb=1";
        assert_eq!(unwrap_bing(bing_url), "https://example.com/something");
    }

    #[test]
    fn test_unwrap_google() {
        let google_url = "https://www.google.com/url?q=https://example.com/target&sa=U";
        assert_eq!(unwrap_google(google_url), Some("https://example.com/target".to_string()));

        let internal = "https://www.google.com/search?q=test";
        assert_eq!(unwrap_google(internal), None);
    }

    #[test]
    fn test_is_ddg_bot_challenge() {
        assert!(is_ddg_bot_challenge("Unfortunately, bots use DuckDuckGo too.", 200));
        assert!(is_ddg_bot_challenge("anything", 202));
        assert!(is_ddg_bot_challenge("src=\"anomaly.js\"", 200));
        assert!(!is_ddg_bot_challenge("<html><head><title>Search</title></head></html>", 200));
    }

    #[test]
    fn test_parse_bing_html() {
        let sample = r#"
            <ol id="b_results">
                <li class="b_algo">
                    <h2><a href="https://example.com/algo">Algo Result</a></h2>
                    <div class="b_caption"><p>Description text for algo result.</p></div>
                </li>
            </ol>
        "#;
        let hits = parse_bing_html(sample, 5);
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].title, "Algo Result");
        assert_eq!(hits[0].url, "https://example.com/algo");
        assert_eq!(hits[0].snippet, "Description text for algo result.");
    }

    #[test]
    fn test_parse_ddg_lite_html() {
        let sample = r#"
            <table>
                <tr>
                    <td><a class="result-link" href="https://example.com/lite">Lite Result</a></td>
                </tr>
                <tr>
                    <td class="result-snippet">Lite snippet text.</td>
                </tr>
            </table>
        "#;
        let hits = parse_ddg_lite_html(sample, 5);
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].title, "Lite Result");
        assert_eq!(hits[0].url, "https://example.com/lite");
        assert_eq!(hits[0].snippet, "Lite snippet text.");
    }

    #[test]
    fn test_parse_google_html() {
        let sample = r#"
            <div class="g">
                <a href="https://example.com/goog"><h3>Google Result</h3></a>
                <div class="VwiC3b">Google snippet text.</div>
            </div>
        "#;
        let hits = parse_google_html(sample, 5);
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].title, "Google Result");
        assert_eq!(hits[0].url, "https://example.com/goog");
        assert_eq!(hits[0].snippet, "Google snippet text.");
    }

    #[test]
    fn test_format_search_view() {
        let hits = parse_ddg_html(DDG_SAMPLE, 10);
        let view = format_search_view("rust lang", "ddg", &hits);
        assert!(view.contains("## Search Results for \"rust lang\" (via ddg)"));
        assert!(view.contains("1. [Rust Programming Language](https://www.rust-lang.org/)"));
    }
}
