"""LLM inspect + click/type. Numbered refs, including challenge widgets."""

from __future__ import annotations

from urllib.parse import urljoin

from webresearch.engine.tree import html_to_tree, tree_to_html
from webresearch.types import Challenge, Target

INTERACTIVE_TAGS = {"a", "button", "input", "textarea", "select", "iframe", "label"}

CHALLENGE_RULES: list[tuple[str, str, tuple[str, ...]]] = [
    ("recaptcha", "click", ("recaptcha", "g-recaptcha", "i'm not a robot", "im not a robot", "i am not a robot")),
    ("hcaptcha", "click", ("hcaptcha", "h-captcha")),
    ("turnstile", "unlikely", ("cf-turnstile", "challenges.cloudflare.com")),
    ("cloudflare", "block", ("just a moment", "checking your browser", "cf-browser-verification", "attention required")),
    ("google_sorry", "block", ("unusual traffic", "/sorry/", "detected unusual")),
    ("datadome", "block", ("datadome",)),
]


def _text_of(node: dict) -> str:
    if node.get("t") == "#text":
        return node.get("x") or ""
    parts: list[str] = []
    for child in node.get("c") or []:
        parts.append(_text_of(child))
    return " ".join(" ".join(parts).split())


def _is_interactive(node: dict) -> bool:
    tag = node.get("t") or ""
    attrs = node.get("a") or {}
    if tag in INTERACTIVE_TAGS:
        if tag == "input" and (attrs.get("type") or "").lower() == "hidden":
            return False
        return True
    role = (attrs.get("role") or "").lower()
    if role in {"button", "checkbox", "link", "textbox"}:
        return True
    if attrs.get("onclick"):
        return True
    return False


def _collect_interactive(
    node: dict, acc: list[tuple[dict, list[int]]], path: list[int] | None = None
) -> None:
    path = path or []
    if _is_interactive(node):
        acc.append((node, list(path)))
    children = node.get("c") or []
    index = 0
    for child in children:
        if isinstance(child, dict) and child.get("t") != "#text":
            _collect_interactive(child, acc, [*path, index])
            index += 1


def _role(node: dict) -> str:
    tag = node.get("t") or ""
    attrs = node.get("a") or {}
    typ = (attrs.get("type") or "").lower()
    role = (attrs.get("role") or "").lower()
    src = (attrs.get("src") or "").lower()
    title = (attrs.get("title") or "").lower()
    cls = (attrs.get("class") or "").lower()
    blob = " ".join([tag, typ, role, src, title, cls, _text_of(node).lower()])
    if "recaptcha" in blob or "i'm not a robot" in blob or "i am not a robot" in blob:
        return "recaptcha"
    if "hcaptcha" in blob:
        return "hcaptcha"
    if "turnstile" in blob or "cloudflare" in blob:
        return "turnstile"
    if tag == "iframe":
        return "iframe"
    if tag == "a":
        return "link"
    if tag == "button" or role == "button" or typ == "submit":
        return "button"
    if typ == "checkbox" or role == "checkbox":
        return "checkbox"
    if typ == "range" or role == "slider":
        return "slider"
    if tag in {"input", "textarea"} or role == "textbox":
        return "textbox"
    if tag == "select":
        return "select"
    if tag == "label":
        return "label"
    return tag or "element"


def count_interactive(html: str) -> int:
    from webresearch.engine.tree import html_to_tree
    tree = html_to_tree(html)
    nodes: list[tuple[dict, list[int]]] = []
    _collect_interactive(tree, nodes)
    return len(nodes)


def detect_challenges(html: str, url: str = "", targets: list[Target] | None = None) -> list[Challenge]:
    blob = f"{html} {url}".lower()
    found: list[Challenge] = []
    targets = targets or []
    for kind, passable, needles in CHALLENGE_RULES:
        if not any(n in blob for n in needles):
            continue
        refs = [t.ref for t in targets if t.challenge or t.role in {kind, "recaptcha", "hcaptcha", "turnstile", "checkbox"}]
        if kind == "recaptcha":
            detail = (
                "Use browser_captcha to inspect the anchor/challenge frames. "
                "A top-level checkbox is clickable only when a listed ref is present; "
                "this does not satisfy server-side verification."
            )
        elif kind == "hcaptcha":
            detail = (
                "Use browser_captcha for frame details. A listed top-level ref can be toggled, "
                "but that does not solve the visual or server-side challenge."
            )
        elif kind == "google_sorry":
            detail = "Google sorry/captcha interstitial. Clicking rarely passes. Use browser_search instead of google.com HTML."
        elif kind == "cloudflare":
            detail = "Cloudflare/Turnstile bot wall. A mini-DOM click will not satisfy it."
        else:
            detail = "Bot check detected. Try click on listed refs; many still fail."
        found.append(Challenge(kind=kind, passable=passable, detail=detail, refs=refs[:8]))
    return found


def inspect_html(html: str, url: str = "") -> tuple[list[Target], list[Challenge]]:
    tree = html_to_tree(html)
    nodes: list[tuple[dict, list[int]]] = []
    _collect_interactive(tree, nodes)
    targets: list[Target] = []
    seen_refs: dict[str, int] = {}
    for i, (node, path) in enumerate(nodes[:80], start=1):
        attrs = node.get("a") or {}
        role = _role(node)
        text = _text_of(node)[:120] or attrs.get("value") or attrs.get("title") or attrs.get("name") or attrs.get("aria-label") or ""
        href = attrs.get("href") or attrs.get("src") or ""
        checked = None
        if role == "checkbox" or (attrs.get("type") or "").lower() == "checkbox":
            checked = attrs.get("checked") is not None and str(attrs.get("checked")).lower() != "false"
        challenge = role in {"recaptcha", "hcaptcha", "turnstile"} or "robot" in text.lower()
        import hashlib
        digest = hashlib.sha1(
            "|".join([role, node.get("t") or "", attrs.get("name") or attrs.get("id") or "", text.strip()[:40], href]).encode("utf-8", "ignore")
        ).hexdigest()[:8]
        base_ref = f"e{digest}"
        n = seen_refs.get(base_ref, 0) + 1
        seen_refs[base_ref] = n
        ref = base_ref if n == 1 else f"{base_ref}-{n}"
        _ = path  # path retained in Target
        targets.append(
            Target(
                ref=ref,
                role=role,
                text=text.strip(),
                tag=node.get("t") or "",
                name=attrs.get("name") or attrs.get("id") or "",
                href=urljoin(url, href) if href and not href.startswith("javascript:") else href,
                checked=checked,
                challenge=challenge,
                path=path,
            )
        )
    return targets, detect_challenges(html, url, targets)


def _iter_refs(nodes: list[tuple[dict, list[int]]]):
    import hashlib
    seen: dict[str, int] = {}
    for i, (node, path) in enumerate(nodes, start=1):
        attrs = node.get("a") or {}
        role = _role(node)
        text = _text_of(node)[:120] or attrs.get("value") or attrs.get("title") or attrs.get("name") or attrs.get("aria-label") or ""
        href = attrs.get("href") or attrs.get("src") or ""
        digest = hashlib.sha1(
            "|".join([role, node.get("t") or "", attrs.get("name") or attrs.get("id") or "", text.strip()[:40], href]).encode("utf-8", "ignore")
        ).hexdigest()[:8]
        base = f"e{digest}"
        n = seen.get(base, 0) + 1
        seen[base] = n
        ref = base if n == 1 else f"{base}-{n}"
        yield node, path, ref


def _node_by_ref(html: str, ref: str) -> tuple[dict, list[int], object]:
    tree = html_to_tree(html)
    nodes: list[tuple[dict, list[int]]] = []
    _collect_interactive(tree, nodes)
    for node, path, r in _iter_refs(nodes):
        if r == ref:
            return node, path, tree
    raise ValueError(f"no target [{ref}]")


def click_html(html: str, url: str, ref: str) -> tuple[str, str | None]:
    """Toggle/activate inspect ref. Returns (html, navigate_url or None)."""
    if not ref or ref[0] != "e" or not ref[1:].replace("-", "").isalnum():
        raise ValueError(f"bad ref: {ref}")
    node, _path, tree = _node_by_ref(html, ref)
    tag = node.get("t") or ""
    attrs = node.setdefault("a", {})
    typ = (attrs.get("type") or "").lower()
    role = _role(node)

    if tag == "a":
        href = attrs.get("href") or ""
        if href and not href.startswith("#") and not href.lower().startswith("javascript:"):
            return tree_to_html(tree), urljoin(url, href)

    if typ == "checkbox" or role in {"checkbox", "recaptcha", "hcaptcha"}:
        if attrs.get("checked") is None or str(attrs.get("checked")).lower() == "false":
            attrs["checked"] = "checked"
            attrs["aria-checked"] = "true"
        else:
            attrs.pop("checked", None)
            attrs["aria-checked"] = "false"
        if role == "recaptcha":
            attrs["data-wr-clicked"] = "1"
        return tree_to_html(tree), None

    if tag == "label":
        return tree_to_html(tree), None

    if typ == "submit" or tag == "button" or role == "button":
        return tree_to_html(tree), None

    return tree_to_html(tree), None


def type_html(html: str, ref: str, text: str) -> str:
    if not ref or ref[0] != "e" or not ref[1:].replace("-", "").isalnum():
        raise ValueError(f"bad ref: {ref}")
    node, _path, tree = _node_by_ref(html, ref)
    tag = node.get("t") or ""
    attrs = node.setdefault("a", {})
    if tag not in {"input", "textarea", "select"} and (attrs.get("role") or "") != "textbox":
        raise ValueError(f"[{ref}] is not typeable")
    attrs["value"] = text
    if tag == "textarea":
        node["c"] = [{"t": "#text", "x": text}]
    return tree_to_html(tree)


def agent_view_extras(
    *,
    targets: list[Target],
    challenges: list[Challenge],
    js_ran: bool,
    js_info: dict | None = None,
) -> str:
    lines = [f"ENGINE: {(js_info or {}).get('engine') or ('quickjs' if js_ran else 'html')}"]
    info = js_info or {}
    if info.get("skipped_modules"):
        lines.append(
            f"NOTE: Skipped {info['skipped_modules']} ES module script(s) "
            "(React/Vue bundles). Mini-DOM is growing; use inspect/click on what rendered."
        )
    if info.get("scripts"):
        lines.append(f"JS: ran {info['scripts']} script(s), errors={info.get('errors', 0)}")
    if challenges:
        lines.append("CHALLENGE:")
        for ch in challenges:
            refs = ",".join(ch.refs) if ch.refs else "-"
            lines.append(f"  {ch.kind} passable={ch.passable} refs={refs}")
            lines.append(f"    {ch.detail}")
    if targets:
        lines.append("Inspect (LLM: click/type these refs):")
        for t in targets[:40]:
            flag = " CHALLENGE" if t.challenge else ""
            checked = "" if t.checked is None else f" checked={t.checked}"
            label = (t.text or t.name or t.href or t.tag)[:70]
            lines.append(f"[{t.ref}] {t.role} {label}{checked}{flag}")
    return "\n".join(lines)
