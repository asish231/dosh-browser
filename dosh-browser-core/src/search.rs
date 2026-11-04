use kuchiki::traits::TendrilSink;
use reqwest::Client;
use url::Url;

use crate::content::text_content;
use crate::fetch;
use crate::types::{SearchHit, SearchResult};

pub const DDG_HTML_URL: &str = "https://html.duckduckgo.com/html/";

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

        // 1. Title and URL
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

        // 2. Snippet
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

pub async fn search(
    client: &Client,
    query: &str,
    max_hits: usize,
    engine: &str,
) -> Result<SearchResult, Box<dyn std::error::Error + Send + Sync>> {
    let chosen_engine = if engine == "auto" || engine.is_empty() {
        "ddg"
    } else {
        engine
    };

    let hits = match chosen_engine {
        "ddg" => {
            let body_str = {
                let mut serializer = url::form_urlencoded::Serializer::new(String::new());
                serializer.append_pair("q", query);
                serializer.append_pair("b", "");
                serializer.finish()
            };

            let res = fetch::fetch(
                client,
                DDG_HTML_URL,
                "POST",
                Some(body_str),
                "{\"Content-Type\": \"application/x-www-form-urlencoded\"}",
                4_000_000,
            )
            .await?;

            parse_ddg_html(&res.body, max_hits)
        }
        _ => {
            let body_str = {
                let mut serializer = url::form_urlencoded::Serializer::new(String::new());
                serializer.append_pair("q", query);
                serializer.finish()
            };

            let res = fetch::fetch(
                client,
                DDG_HTML_URL,
                "POST",
                Some(body_str),
                "{\"Content-Type\": \"application/x-www-form-urlencoded\"}",
                4_000_000,
            )
            .await?;

            parse_ddg_html(&res.body, max_hits)
        }
    };

    let view = format_search_view(query, chosen_engine, &hits);

    Ok(SearchResult {
        query: query.to_string(),
        engine: chosen_engine.to_string(),
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
    fn test_format_search_view() {
        let hits = parse_ddg_html(DDG_SAMPLE, 10);
        let view = format_search_view("rust lang", "ddg", &hits);
        assert!(view.contains("## Search Results for \"rust lang\" (via ddg)"));
        assert!(view.contains("1. [Rust Programming Language](https://www.rust-lang.org/)"));
    }
}
