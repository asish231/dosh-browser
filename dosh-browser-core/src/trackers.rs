use std::collections::{HashMap, HashSet};
use url::Url;

const TWO_LEVEL_SUFFIXES: &[&str] = &[
    "co.uk", "org.uk", "me.uk", "ac.uk", "gov.uk", "net.uk", "sch.uk",
    "co.jp", "ne.jp", "or.jp", "go.jp", "ac.jp",
    "com.au", "net.au", "org.au", "id.au", "gov.au", "edu.au",
    "co.nz", "net.nz", "org.nz", "govt.nz",
    "co.kr", "or.kr", "go.kr", "ac.kr", "ne.kr",
    "com.br", "net.br", "org.br", "gov.br", "edu.br",
    "co.in", "net.in", "org.in", "gov.in", "ac.in",
    "com.mx", "net.mx", "org.mx", "gob.mx", "edu.mx",
    "co.za", "net.za", "org.za", "gov.za", "ac.za",
    "com.ar", "net.ar", "org.ar", "gov.ar", "edu.ar",
    "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn",
    "co.il", "org.il", "net.il", "gov.il", "ac.il",
    "com.tr", "net.tr", "org.tr", "gov.tr", "edu.tr",
    "com.sg", "net.sg", "org.sg", "gov.sg", "edu.sg",
    "com.hk", "net.hk", "org.hk", "gov.hk", "edu.hk",
    "com.tw", "net.tw", "org.tw", "gov.tw", "edu.tw",
    "co.th", "or.th", "go.th", "ac.th", "net.th",
    "com.ph", "net.ph", "org.ph", "gov.ph", "edu.ph",
    "com.my", "net.my", "org.my", "gov.my", "edu.my",
    "co.id", "or.id", "go.id", "ac.id", "net.id",
    "com.vn", "net.vn", "org.vn", "gov.vn", "edu.vn",
];

pub fn registrable_domain(raw_host: &str) -> String {
    let host = raw_host.trim().trim_end_matches('.').to_lowercase();
    if host.is_empty() {
        return String::new();
    }
    let parts: Vec<&str> = host.split('.').collect();
    if parts.len() < 2 {
        return host;
    }
    let candidate = format!("{}.{}", parts[parts.len() - 2], parts[parts.len() - 1]);
    let suffix_set: HashSet<&str> = TWO_LEVEL_SUFFIXES.iter().copied().collect();
    if suffix_set.contains(candidate.as_str()) && parts.len() >= 3 {
        format!(
            "{}.{}.{}",
            parts[parts.len() - 3],
            parts[parts.len() - 2],
            parts[parts.len() - 1]
        )
    } else {
        candidate
    }
}

pub const SEED_ENTITIES: &[(&str, &[&str])] = &[
    (
        "Google",
        &[
            "google-analytics.com",
            "googletagmanager.com",
            "doubleclick.net",
            "googlesyndication.com",
            "googleadservices.com",
            "google.com",
            "googletagservices.com",
            "googleapis.com",
            "gstatic.com",
            "youtube.com",
        ],
    ),
    (
        "Meta",
        &["facebook.net", "facebook.com", "fbcdn.net", "fbsbx.com"],
    ),
    (
        "Microsoft",
        &["bing.com", "clarity.ms", "linkedin.com", "microsoft.com"],
    ),
    (
        "Amazon",
        &["amazon-adsystem.com", "amazon.com", "aax.amazon-adsystem.com"],
    ),
    ("TikTok", &["tiktok.com", "tiktokv.com", "tiktokcdn.com"]),
    ("Adobe", &["adobe.com", "demdex.net", "omtrdc.net"]),
    ("Oracle", &["oracle.com", "bluekai.com", "eloqua.com"]),
    ("Salesforce", &["salesforce.com", "pardot.com", "evergage.com"]),
    ("Hotjar", &["hotjar.com", "hotjar.io"]),
    ("Mixpanel", &["mixpanel.com"]),
    ("Segment", &["segment.com", "segment.io"]),
    ("Amplitude", &["amplitude.com"]),
    ("New Relic", &["newrelic.com", "nr-data.net"]),
    ("Cloudflare Insights", &["cloudflareinsights.com"]),
    ("Yandex", &["yandex.ru", "yandex.net", "mc.yandex.ru"]),
    ("Baidu", &["baidu.com", "bdstatic.com"]),
    ("Criteo", &["criteo.com", "criteo.net"]),
    ("Outbrain", &["outbrain.com"]),
    ("Taboola", &["taboola.com", "taboolasyndication.com"]),
    ("Quantcast", &["quantcast.com", "quantserve.com"]),
    ("Comscore", &["comscore.com", "scorecardresearch.com"]),
    ("Nielsen", &["nielsen.com", "imrworldwide.com"]),
    ("Akamai mPulse", &["akstat.io", "go-mpulse.net"]),
    ("Datadog RUM", &["datadoghq-browser-agent.com"]),
    ("Sentry", &["sentry.io"]),
    ("FullStory", &["fullstory.com"]),
    ("Snowplow", &["snowplowanalytics.com"]),
];

pub const SURROGATES: &[(&str, &str)] = &[
    (
        "google-analytics.com",
        "/* surrogate: google-analytics */\nvar _gaq=_gaq||[];function ga(){}\n",
    ),
    (
        "googletagmanager.com",
        "/* surrogate: gtm */\nwindow.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}\n",
    ),
    (
        "connect.facebook.net",
        "/* surrogate: facebook pixel */\nfunction fbq(){}\n",
    ),
    (
        "hotjar.com",
        "/* surrogate: hotjar */\nwindow.hj=window.hj||function(){};\n",
    ),
    (
        "platform.twitter.com",
        "/* surrogate: twitter widgets */\nwindow.twttr=window.twttr||{widgets:{load:function(){}}};\n",
    ),
];

pub const FUNCTIONAL_CHALLENGE_MARKERS: &[&str] = &[
    "google.com/recaptcha/",
    "recaptcha.net/recaptcha/",
    "gstatic.com/recaptcha/",
    "hcaptcha.com/1/api.js",
    "challenges.cloudflare.com/turnstile/",
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TrackingDecision {
    pub status: &'static str, // "ALLOWED" | "SAME_ENTITY_ALLOWED" | "FUNCTIONAL_ALLOWED" | "BLOCKED"
    pub entity: String,
    pub surrogate: String,
}

pub struct TrackerGraph {
    domain_to_entity: HashMap<String, String>,
}

impl Default for TrackerGraph {
    fn default() -> Self {
        Self::new()
    }
}

impl TrackerGraph {
    pub fn new() -> Self {
        let mut domain_to_entity = HashMap::new();
        for &(entity, domains) in SEED_ENTITIES {
            for &d in domains {
                domain_to_entity.insert(d.to_lowercase(), entity.to_string());
            }
        }
        Self { domain_to_entity }
    }

    pub fn entity_of(&self, host: &str) -> String {
        let h = host.to_lowercase();
        if let Some(ent) = self.domain_to_entity.get(&h) {
            return ent.clone();
        }
        for (domain, ent) in &self.domain_to_entity {
            if h.ends_with(&format!(".{}", domain)) {
                return ent.clone();
            }
        }
        String::new()
    }

    pub fn evaluate(&self, request_url: &str, document_url: &str) -> TrackingDecision {
        let Ok(req_u) = Url::parse(request_url) else {
            return TrackingDecision {
                status: "ALLOWED",
                entity: String::new(),
                surrogate: String::new(),
            };
        };
        let Ok(doc_u) = Url::parse(document_url) else {
            return TrackingDecision {
                status: "ALLOWED",
                entity: String::new(),
                surrogate: String::new(),
            };
        };

        let req_host = req_u.host_str().unwrap_or("").to_lowercase();
        let doc_host = doc_u.host_str().unwrap_or("").to_lowercase();

        if req_host.is_empty() || doc_host.is_empty() {
            return TrackingDecision {
                status: "ALLOWED",
                entity: String::new(),
                surrogate: String::new(),
            };
        }

        let entity = self.entity_of(&req_host);
        if entity.is_empty() {
            return TrackingDecision {
                status: "ALLOWED",
                entity: String::new(),
                surrogate: String::new(),
            };
        }

        let req_low = request_url.to_lowercase();
        if FUNCTIONAL_CHALLENGE_MARKERS.iter().any(|&m| req_low.contains(m)) {
            return TrackingDecision {
                status: "FUNCTIONAL_ALLOWED",
                entity,
                surrogate: String::new(),
            };
        }

        if registrable_domain(&req_host) == registrable_domain(&doc_host) {
            return TrackingDecision {
                status: "ALLOWED",
                entity,
                surrogate: String::new(),
            };
        }

        let doc_entity = self.entity_of(&doc_host);
        if !doc_entity.is_empty() && doc_entity == entity {
            return TrackingDecision {
                status: "SAME_ENTITY_ALLOWED",
                entity,
                surrogate: String::new(),
            };
        }

        let mut surrogate = String::new();
        for &(marker, stub) in SURROGATES {
            if req_host.contains(marker) {
                surrogate = stub.to_string();
                break;
            }
        }

        TrackingDecision {
            status: "BLOCKED",
            entity,
            surrogate,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_registrable_domain() {
        assert_eq!(registrable_domain("a.example.co.uk"), "example.co.uk");
        assert_eq!(registrable_domain("tracker.example.com"), "example.com");
        assert_eq!(registrable_domain("example.com"), "example.com");
    }

    #[test]
    fn test_tracker_graph_evaluation() {
        let g = TrackerGraph::new();
        let d = g.evaluate("https://www.google-analytics.com/g/collect", "https://shop.example/");
        assert_eq!(d.status, "BLOCKED");
        assert_eq!(d.entity, "Google");
        assert!(!d.surrogate.is_empty());

        let d2 = g.evaluate("https://cdn.shop.example/app.js", "https://shop.example/");
        assert_eq!(d2.status, "ALLOWED");

        let d3 = g.evaluate("https://unknown-tracker-xyz.example/x.js", "https://shop.example/");
        assert_eq!(d3.status, "ALLOWED");

        // reCAPTCHA is functional challenge
        let d4 = g.evaluate("https://www.google.com/recaptcha/api.js", "https://shop.example/");
        assert_eq!(d4.status, "FUNCTIONAL_ALLOWED");
    }
}

