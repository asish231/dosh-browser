"""J4: Intl beyond en-US — honest locale packs for high-traffic locales.

Before J4, every locale fell back to en-US semantics (disclosed). Now
de/fr/es/it/pt/ja/zh/ko/hi/ar carry REAL conventions: month/day names,
number group/decimal separators, currency symbols + position, plural
rule families, and list conjunctions. Uncovered locales still fall
back to en-US, and resolvedOptions() discloses which pack applied
('native-data' vs 'en-US-fallback') — never a fake exotic string.
"""

import json

from quickjs import Context

from webresearch.engine.runtime import MINIDOM_JS, _host_parse_html
from webresearch.engine.tree import html_to_tree


def _ctx() -> Context:
    ctx = Context()
    ctx.add_callable("host_parse_html", _host_parse_html)
    ctx.eval(MINIDOM_JS)
    tree = html_to_tree("<html><body></body></html>")
    ctx.eval(f"__load({json.dumps(tree)}, 'https://example.com/');")
    return ctx


def test_german_number_and_date_formatting():
    ctx = _ctx()
    got = json.loads(ctx.eval("""
        (function() {
            var nf = new Intl.NumberFormat('de-DE');
            var dtf = new Intl.DateTimeFormat('de-DE', { month: 'long', day: 'numeric', year: 'numeric' });
            var d = new Date(2026, 0, 15);  /* January 15, 2026 */
            return JSON.stringify({
                num: nf.format(1234567.89),
                month: dtf.format(d),
                ro: dtf.resolvedOptions().localePack
            });
        })()
    """))
    assert got["num"] == "1.234.567,89", f"German separators: dot groups, comma decimal — got {got['num']}"
    assert "Januar" in got["month"], f"German month name — got {got['month']}"
    assert got["ro"] == "native-data"


def test_french_date_names():
    ctx = _ctx()
    got = json.loads(ctx.eval("""
        (function() {
            var d = new Date(2026, 2, 10); /* March 10 */
            var dtf = new Intl.DateTimeFormat('fr-FR', { weekday: 'long', month: 'long', day: 'numeric' });
            return JSON.stringify({ out: dtf.format(d) });
        })()
    """))
    assert "mars" in got["out"], f"French month — got {got['out']}"
    assert "mardi" in got["out"] or "mars" in got["out"]


def test_currency_position_by_locale():
    ctx = _ctx()
    got = json.loads(ctx.eval("""
        (function() {
            var de = new Intl.NumberFormat('de-DE', { style: 'currency', currency: 'EUR' });
            var jp = new Intl.NumberFormat('ja-JP', { style: 'currency', currency: 'JPY' });
            var us = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
            var xx = new Intl.NumberFormat('xx-XX', { style: 'currency', currency: 'XYZ' });
            return JSON.stringify({
                de: de.format(5),
                jp: jp.format(5),
                us: us.format(5),
                xx: xx.format(5),
                xxPack: xx.resolvedOptions().localePack
            });
        })()
    """))
    assert got["de"].endswith("\u20ac") or "\u20ac" in got["de"], f"EUR suffix in German — got {got['de']}"
    assert got["jp"].startswith("\u00a5"), f"JPY prefix in Japanese — got {got['jp']}"
    assert "." not in got["jp"], f"JPY has no minor units — got {got['jp']}"
    assert "USD" in got["us"], f"en-US keeps ISO code (no USD mapping) — got {got['us']}"
    assert "XYZ" in got["xx"], f"unmapped currency keeps ISO code — got {got['xx']}"
    assert got["xxPack"] == "en-US-fallback"


def test_east_asian_no_plural():
    ctx = _ctx()
    got = json.loads(ctx.eval("""
        (function() {
            var ja = new Intl.PluralRules('ja-JP');
            var en = new Intl.PluralRules('en-US');
            return JSON.stringify({
                ja1: ja.select(1),
                ja0: ja.select(0),
                jaN: ja.select(5),
                en1: en.select(1),
                enN: en.select(5)
            });
        })()
    """))
    assert got["ja1"] == "other" and got["ja0"] == "other" and got["jaN"] == "other", \
        f"Japanese has no plural categories — got {got}"
    assert got["en1"] == "one" and got["enN"] == "other"


def test_arabic_plural_categories():
    ctx = _ctx()
    got = json.loads(ctx.eval("""
        (function() {
            var ar = new Intl.PluralRules('ar-EG');
            return JSON.stringify({
                zero: ar.select(0),
                one: ar.select(1),
                two: ar.select(2),
                few: ar.select(5),
                many: ar.select(15),
                other: ar.select(1000)
            });
        })()
    """))
    assert got["zero"] == "zero" and got["one"] == "one" and got["two"] == "two"
    assert got["few"] == "few" and got["many"] == "many" and got["other"] == "other"


def test_locale_list_conjunctions():
    ctx = _ctx()
    got = json.loads(ctx.eval("""
        (function() {
            var de = new Intl.ListFormat('de-DE');
            var ja = new Intl.ListFormat('ja-JP');
            var en = new Intl.ListFormat('en-US');
            return JSON.stringify({
                de: de.format(['a', 'b', 'c']),
                ja: ja.format(['a', 'b', 'c']),
                en: en.format(['a', 'b', 'c'])
            });
        })()
    """))
    assert " und " in got["de"], f"German list uses 'und' — got {got['de']}"
    assert got["ja"] == "a\u3001b\u3001c", f"Japanese list uses ideographic comma — got {got['ja']}"
    assert got["en"] == "a, b, and c", f"English Oxford-style — got {got['en']}"


def test_uncovered_locale_falls_back_honestly():
    ctx = _ctx()
    got = json.loads(ctx.eval("""
        (function() {
            var sw = new Intl.DateTimeFormat('sw-KE', { month: 'long' });
            var d = new Date(2026, 0, 15);
            return JSON.stringify({
                out: sw.format(d),
                pack: sw.resolvedOptions().localePack
            });
        })()
    """))
    assert got["pack"] == "en-US-fallback", f"uncovered locale must disclose fallback — got {got['pack']}"
    assert "January" in got["out"], f"fallback uses real en-US data — got {got['out']}"
