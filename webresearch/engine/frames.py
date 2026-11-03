"""Small frame tree for the QuickJS engine.

Each iframe is fetched into its own HTML document and receives a fresh QuickJS
context. The implementation is intentionally bounded: it is an inspection
engine, not a layout or painting engine.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from html import unescape
from urllib.parse import unquote_to_bytes, urlencode, urljoin, urlparse

import httpx

from webresearch.engine.runtime import run_scripts
from webresearch.engine.tree import collect_iframes, extract_scripts, html_to_tree, walk
from webresearch.fetch import fetch
from webresearch.types import FrameInfo

MAX_FRAMES = 16
MAX_FRAME_DEPTH = 3
MAX_EXCERPT = 4000

def extract_origin(url: str) -> str:
    """Extract normalized scheme://host[:port] origin from a URL."""
    try:
        p = urlparse(url)
        scheme = p.scheme.lower()
        if not scheme or not p.hostname:
            return ""
        host = p.hostname.lower()
        port = p.port
        if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
            port = None
        if port:
            return f"{scheme}://{host}:{port}"
        return f"{scheme}://{host}"
    except Exception:
        return ""


_CHALLENGE_NEEDLES = (
    "recaptcha",
    "hcaptcha",
    "h-captcha",
    "turnstile",
    "cf-challenge",
    "datadome",
)


@dataclass(slots=True)
class FrameDocument:
    ref: str
    parent_ref: str
    url: str
    title: str = ""
    name: str = ""
    depth: int = 1
    status: int = 0
    content_type: str = ""
    html: str = ""
    js_info: dict = field(default_factory=dict)
    error: str = ""
    # Identity captured at load time for reuse matching on later passes:
    # pre-redirect resolved URL, raw iframe src, raw srcdoc content.
    origin_url: str = ""
    seed_src: str = ""
    seed_srcdoc: str = ""
    # Cursor: how many of this frame's post_messages have already been
    # delivered to the parent. Frames are reused across action passes now,
    # so without it every pass would re-deliver the frame's init messages.
    delivered_count: int = 0

    @property
    def js_ran(self) -> bool:
        return bool(self.js_info.get("ran"))

    @property
    def challenge(self) -> bool:
        blob = f"{self.url} {self.title} {self.html}".lower()
        return any(needle in blob for needle in _CHALLENGE_NEEDLES)

    @property
    def post_messages(self) -> list[dict]:
        return list(self.js_info.get("post_messages") or [])

    def to_info(self) -> FrameInfo:
        return FrameInfo(
            ref=self.ref,
            parent_ref=self.parent_ref,
            url=self.url,
            title=self.title,
            name=self.name,
            depth=self.depth,
            status=self.status,
            content_type=self.content_type,
            js_ran=self.js_ran,
            scripts=int(self.js_info.get("scripts") or 0),
            challenge=self.challenge,
            error=self.error,
            html_excerpt=_compact_excerpt(self.html),
            post_messages=self.post_messages,
        )


@dataclass(slots=True)
class _FrameSeed:
    parent_ref: str
    parent_url: str
    depth: int
    src: str = ""
    srcdoc: str = ""
    title: str = ""
    name: str = ""


def _compact_excerpt(html: str) -> str:
    text = " ".join((html or "").split())
    return text[:MAX_EXCERPT]


def _site_key_nodes(html: str) -> list[tuple[str, str]]:
    """Return (site key, data-s) for reCAPTCHA containers."""
    found: list[tuple[str, str]] = []
    tree = html_to_tree(html)
    for node in walk(tree):
        attrs = node.get("a") or {}
        key = attrs.get("data-sitekey") or ""
        if not key:
            continue
        blob = " ".join(
            [
                node.get("t") or "",
                attrs.get("class") or "",
                attrs.get("id") or "",
                attrs.get("data-callback") or "",
            ]
        ).lower()
        if "captcha" in blob or "sitekey" in str(attrs).lower():
            found.append((key, attrs.get("data-s") or ""))
    return found


def _recaptcha_anchor_url(html: str, parent_url: str, site_key: str, data_s: str) -> str:
    scripts, _ = extract_scripts(html)
    script_urls = [item.get("src") or "" for item in scripts]
    enterprise = any("recaptcha/enterprise" in src.lower() for src in script_urls)
    host = "www.recaptcha.net" if any("recaptcha.net" in src.lower() for src in script_urls) else "www.google.com"
    path = "/recaptcha/enterprise/anchor" if enterprise else "/recaptcha/api2/anchor"

    parsed = urlparse(parent_url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    origin = f"{parsed.scheme}://{parsed.hostname or ''}:{port}"
    encoded_origin = base64.urlsafe_b64encode(origin.encode()).decode().rstrip("=") + "."
    params = {
        "ar": "1",
        "k": site_key,
        "co": encoded_origin,
        "hl": "en",
        "size": "normal",
        "cb": "webresearch",
    }
    if data_s:
        params["s"] = data_s
    return f"https://{host}{path}?{urlencode(params)}"


def _discover_seeds(
    html: str,
    parent_url: str,
    *,
    parent_ref: str,
    depth: int,
    synthetic_captcha: bool,
) -> list[_FrameSeed]:
    seeds = [
        _FrameSeed(
            parent_ref=parent_ref,
            parent_url=parent_url,
            depth=depth,
            src=item.get("src") or "",
            srcdoc=unescape(item.get("srcdoc") or ""),
            title=item.get("title") or "",
            name=item.get("name") or "",
        )
        for item in collect_iframes(html)
    ]

    # Large challenge scripts often stop before injecting their iframe in the
    # mini-DOM. Expose a deterministic anchor document from the public site key
    # so an agent can still inspect the widget structure.
    low_url = parent_url.lower()
    actual_urls = {urljoin(parent_url, seed.src) for seed in seeds if seed.src}
    has_captcha_frame = any(
        "captcha" in f"{seed.src} {seed.title} {seed.name}".lower() for seed in seeds
    )
    if synthetic_captcha and "recaptcha/" not in low_url and not has_captcha_frame:
        for site_key, data_s in _site_key_nodes(html):
            anchor = _recaptcha_anchor_url(html, parent_url, site_key, data_s)
            if anchor not in actual_urls:
                seeds.append(
                    _FrameSeed(
                        parent_ref=parent_ref,
                        parent_url=parent_url,
                        depth=depth,
                        src=anchor,
                        title="reCAPTCHA anchor (derived from data-sitekey)",
                        name="recaptcha-anchor",
                    )
                )
                actual_urls.add(anchor)
    return seeds


def _decode_data_url(url: str) -> tuple[str, str]:
    header, _, payload = url.partition(",")
    content_type = header[5:].split(";", 1)[0] or "text/plain"
    raw = base64.b64decode(payload) if ";base64" in header.lower() else unquote_to_bytes(payload)
    return raw.decode("utf-8", errors="replace"), content_type


def _fire_frame_load(
    parent_ctx,
    documents: list,
    seed: "_FrameSeed",
    frame_url: str,
) -> bool:
    """A frame document finishing its load fires `load` on its iframe element.

    The event goes to the realm that OWNS the iframe element: the top parent
    for depth-1 frames, the parent frame's live context for nested ones.
    reCAPTCHA's parent client gates its anchor channel processing on this
    event; without it the widget handshake waits forever.
    """
    import json as _json

    ctx = parent_ctx
    if seed.parent_ref:
        owner = next((d for d in documents if d.ref == seed.parent_ref), None)
        if owner is not None:
            ctx = (owner.js_info or {}).get("ctx") or ctx
    if ctx is None:
        return False
    fired = False
    for candidate in (seed.src or "", frame_url or ""):
        if not candidate or candidate.startswith("about:"):
            continue
        try:
            fired = bool(ctx.eval(f"globalThis.__wrFireFrameLoad({_json.dumps(candidate)}); true"))
        except Exception:
            fired = False
        if fired:
            break
    return fired


def _frame_inbox(
    ref: str,
    document_url: str,
    parent_url: str,
    parent_messages: list[dict] | None,
) -> list[dict]:
    """Filter the parent's postMessages down to the ones addressed to one frame.

    Recipient identity: "*", ""/"self", the frame's ref, or its (resolved)
    URL. Origin constraint: targetOrigin must be "*" or match the frame's
    origin exactly (scheme/host/port).
    """
    if not parent_messages:
        return []
    frame_origin = extract_origin(document_url)
    parent_origin = extract_origin(parent_url)
    inbox: list[dict] = []
    for m in parent_messages:
        # 1. Frame recipient identity
        target_id = m.get("target") or "*"
        # 'self' is the sender's own loopback lane (window.postMessage to
        # itself — reCAPTCHA's channel service does this at init). Such a
        # message is addressed to NO frame: the self lane already delivered
        # it inside the sender's realm, with the live port. Delivering it
        # here materialized a proxy in a foreign frame and HIJACKED the
        # sender's loopback kept end (the rewire pointed it at the foreign
        # realm), silently killing the widget's internal channel.
        if target_id == "self":
            continue
        if target_id not in {"*", ""}:
            if target_id in {"parent", "top", "reply"}:
                continue
            if target_id != ref and target_id != document_url:
                resolved_target = urljoin(parent_url, target_id)
                if resolved_target != document_url:
                    continue
        # 2. Origin constraint: exact scheme/host/port comparison
        target_orig = m.get("target_origin") or m.get("origin") or "*"
        if target_orig not in {"*", "", "/"}:
            expected_origin = extract_origin(target_orig)
            if expected_origin and expected_origin != frame_origin:
                continue
        msg_copy = dict(m)
        msg_copy["origin"] = m.get("origin") or parent_origin
        # Sender identity: every message in this inbox comes from the parent
        # document's window. Delivered source must BE that window, or widget
        # handshakes (recaptcha-setup et al.) fail their identity checks.
        msg_copy["source_window"] = {"window": "parent"}
        inbox.append(msg_copy)
    return inbox


def _target_matches_frame(msg: dict, doc: FrameDocument, parent_url: str) -> bool:
    """Does a message the parent posted name `doc` as a recipient?"""
    tgt = str(msg.get("target") or "*")
    if tgt in {"*", "", "child", "frame", "reply"}:
        return True
    for candidate in (doc.seed_src, doc.url):
        if not candidate:
            continue
        if tgt == candidate:
            return True
        try:
            if urljoin(parent_url, candidate) == urljoin(parent_url, tgt):
                return True
        except Exception:
            continue
    return False


_NONCE_CACHE: dict[int, str] = {}


def _ctx_nonce(ctx) -> str:
    """The context nonce the runtime injected (`globalThis.__WR_CTX_KEY`).

    Cross-realm port endpoints are addressed as "<nonce>:<localId>"; the
    nonce qualifies a realm-local port table globally. Cached per context
    object identity — contexts outlive page actions.
    """
    if ctx is None:
        return ""
    key = id(ctx)
    if key not in _NONCE_CACHE:
        try:
            _NONCE_CACHE[key] = str(ctx.eval("String(globalThis.__WR_CTX_KEY || '')") or "")
        except Exception:
            _NONCE_CACHE[key] = ""
    return _NONCE_CACHE[key]


def _resolver(parent_ctx, frames: list[FrameDocument]):
    """Map an origin nonce to the QuickJS context that owns it.

    Transferred-port metadata records the SENDER's nonce; the kept endpoint
    (the sibling left behind to receive replies) lives in that realm, so
    rewiring needs to reach back into it.
    """
    parent_nonce = _ctx_nonce(parent_ctx)
    frame_cache = [
        (doc, _ctx_nonce(doc.js_info.get("ctx")) if doc.js_info.get("ctx") else "")
        for doc in frames
    ]

    def resolve(nonce: str):
        if nonce and nonce == parent_nonce:
            return parent_ctx
        for doc, fnonce in frame_cache:
            if nonce and fnonce and fnonce == nonce:
                return doc.js_info.get("ctx")
        return None

    return resolve


def _wire_received_ports(msgs: list[dict], *, receiving_ctx, resolve_ctx_by_nonce) -> int:
    """After a delivery that carried transferred ports, rewire the sender's
    kept ends to the proxy ids the receiving realm materialized.

    Returns how many kept ends were rewired. Without this both directions of
    a split channel fall silent: the proxy can reach the kept end through the
    recorded origin key, but the kept end still has no address for the proxy.
    """
    wired = 0
    recv_nonce = _ctx_nonce(receiving_ctx)
    if not recv_nonce:
        return 0
    for msg in msgs:
        recv = msg.get("recv_ports") or []
        if not recv:
            continue
        for mt_raw, pid in zip(msg.get("ports") or [], recv):
            mt = mt_raw if isinstance(mt_raw, dict) else {}
            try:
                kept = int(mt.get("kept") or 0)
            except (TypeError, ValueError):
                kept = 0
            if not kept:
                continue
            sender_ctx = resolve_ctx_by_nonce(str(mt.get("ctx") or ""))
            if sender_ctx is None:
                continue
            key = json.dumps(f"{recv_nonce}:{pid}")
            try:
                sender_ctx.eval(f"globalThis.__wrPortSetRemote({kept}, {key});")
                wired += 1
            except Exception:
                continue
    return wired


def _parse_port_target(target: str) -> tuple[str, int]:
    """'port:<nonce>:<localId>' -> (nonce, localId)."""
    rest = target[len("port:"):] if target.startswith("port:") else ""
    nonce, _, local = rest.rpartition(":")
    try:
        return nonce, int(local or 0)
    except ValueError:
        return nonce, 0


def is_port_message(msg: dict) -> bool:
    """A message posted THROUGH a MessagePort rather than across a window."""
    return str(msg.get("target") or "").startswith("port:")


def drain_frame_posts(doc: FrameDocument) -> None:
    """Fold postMessages the frame's context produced OUTSIDE any run_scripts
    pass (direct evals: OTP fills, probes, port wiring) into the document's
    routed queue, deduped by uid.

    Without this, the per-frame delivery cursor never sees them and the
    traffic silently dies — the direct-eval posts landed in a queue nothing
    read."""
    from webresearch.engine.runtime import take_pending_posts

    fctx = doc.js_info.get("ctx")
    if fctx is None:
        return
    pending = take_pending_posts(fctx)
    if not pending:
        return
    msgs = doc.js_info.setdefault("post_messages", [])
    uids = {m.get("uid") for m in msgs}
    for m in pending:
        if m.get("uid") not in uids:
            msgs.append(m)
            uids.add(m.get("uid"))


async def _route_port_msgs(
    msgs: list[dict],
    *,
    parent_ctx,
    parent_html: str,
    parent_url: str,
    client: httpx.AsyncClient,
    frames: list[FrameDocument],
) -> tuple[int, int, list[dict]]:
    """Deliver port-channel messages into the owning realm's port inbox.

    The window lane is for cross-document messages; split channels speak
    directly. Delivery goes through ``run_scripts`` on the owning LIVE
    context — not a bare eval — so replies the receiver posts while handling
    the message (through either lane) are captured in that pass's info dict
    instead of vanishing into whatever ``info`` the context's host bridge was
    last registered with.

    Returns (routed, unrouted, parent_produced). parent_produced are the
    messages the PARENT realm posted while handling deliveries — window
    and port traffic for the next roundtrip round.
    """
    routed = 0
    unrouted = 0
    parent_nonce = _ctx_nonce(parent_ctx)
    parent_produced: list[dict] = []

    # Group by owning realm so each realm gets one bounded pass.
    parent_batch: list[dict] = []
    frame_batches: list[tuple[FrameDocument, list[dict]]] = []
    for m in msgs:
        nonce, local_id = _parse_port_target(str(m.get("target") or ""))
        if not nonce or local_id <= 0:
            unrouted += 1
            continue
        if nonce == parent_nonce:
            parent_batch.append(m)
            continue
        for doc in frames:
            fctx = doc.js_info.get("ctx")
            if fctx is not None and _ctx_nonce(fctx) == nonce:
                for fb_doc, fb_list in frame_batches:
                    if fb_doc is doc:
                        fb_list.append(m)
                        break
                else:
                    frame_batches.append((doc, [m]))
                break
        else:
            unrouted += 1

    if parent_batch:
        try:
            _, out = await run_scripts(
                parent_html, parent_url, client, force=True,
                messages=parent_batch, ctx=parent_ctx,
            )
            routed += len(parent_batch)
            parent_produced.extend(out.get("post_messages") or [])
            # Nested transfers: rewire the sender's kept ends to the proxies
            # the parent realm just materialized.
            _wire_received_ports(
                parent_batch, receiving_ctx=parent_ctx,
                resolve_ctx_by_nonce=_resolver(parent_ctx, frames),
            )
        except Exception:
            unrouted += len(parent_batch)

    for doc, batch in frame_batches:
        fctx = doc.js_info.get("ctx")
        if fctx is None:
            unrouted += len(batch)
            continue
        try:
            _, out = await run_scripts(
                doc.html, doc.url, client, force=True,
                messages=batch, ctx=fctx,
            )
            routed += len(batch)
            doc.js_info.setdefault("post_messages", []).extend(
                out.get("post_messages") or []
            )
            _wire_received_ports(
                batch, receiving_ctx=fctx,
                resolve_ctx_by_nonce=_resolver(parent_ctx, frames),
            )
        except Exception:
            unrouted += len(batch)
    return routed, unrouted, parent_produced


async def frame_message_roundtrip(
    frames: list[FrameDocument],
    *,
    parent_ctx,
    parent_url: str,
    parent_html: str,
    client: httpx.AsyncClient,
    max_rounds: int = 5,
    initial_parent_messages: list[dict] | None = None,
) -> dict:
    """Shuttle postMessage traffic between live frame contexts and the live parent.

    One round: deliver the parent's pending messages into addressed frames
    (sender: the parent window), then lift each frame's parent-addressed
    replies into the parent (sender: that frame's contentWindow). Both
    directions run against LIVE contexts, so widget state — an in-flight
    recaptcha-setup handshake, for instance — survives every hop.

    Messages posted THROUGH a MessagePort (targets like "port:<nonce>:<id>")
    bypass the window lane and route straight into the owning realm's port inbox.
    """
    summary = {
        "child_delivered": 0,
        "frame_inbound": 0,
        "parent_outbound": 0,
        "port_routed": 0,
        "port_unrouted": 0,
        "port_wires": 0,
        "rounds": 0,
        "frame_errors": [],
    }
    parent_msgs: list[dict] = list(initial_parent_messages or [])
    for _ in range(max_rounds):
        progressed = False

        # Traffic the parent produced outside any pass (direct evals) joins
        # the routed queue here; uid order keeps the log faithful.
        try:
            from webresearch.engine.runtime import take_pending_posts

            parent_msgs.extend(take_pending_posts(parent_ctx))
        except Exception:
            pass

        # 0. Split port-channel traffic off the window lane.
        port_msgs = [m for m in parent_msgs if is_port_message(m)]
        parent_msgs = [m for m in parent_msgs if not is_port_message(m)]
        if port_msgs:
            progressed = True
            routed, unrouted, produced = await _route_port_msgs(
                port_msgs,
                parent_ctx=parent_ctx,
                parent_html=parent_html,
                parent_url=parent_url,
                client=client,
                frames=frames,
            )
            summary["port_routed"] += routed
            summary["port_unrouted"] += unrouted
            parent_msgs.extend(produced)

        # 1. Parent -> frames.
        if parent_msgs:
            inbound = [dict(m, source_window={"window": "parent"}) for m in parent_msgs]
            summary["parent_outbound"] += len(inbound)
            progressed = True
            for doc in frames:
                fctx = doc.js_info.get("ctx")
                if fctx is None:
                    continue
                inbox = [m for m in inbound if _target_matches_frame(m, doc, parent_url)]
                if not inbox:
                    continue
                try:
                    _, out = await run_scripts(
                        doc.html, doc.url, client, force=True,
                        messages=inbox, ctx=fctx,
                    )
                    summary["frame_inbound"] += len(inbox)
                    # Ports transferred parent->frame: rewire the parent's kept
                    # ends to the proxies the frame's realm just materialized.
                    summary["port_wires"] += _wire_received_ports(
                        inbox, receiving_ctx=fctx,
                        resolve_ctx_by_nonce=_resolver(parent_ctx, frames),
                    )
                    doc.js_info.setdefault("post_messages", []).extend(
                        out.get("post_messages") or []
                    )
                    for err in (out.get("error_messages") or [])[:5]:
                        summary["frame_errors"].append(f"{doc.ref}: {err}")
                except Exception as exc:
                    summary["frame_errors"].append(f"{doc.ref}: {type(exc).__name__}: {exc}"[:300])
            parent_msgs = []

        # 2. Frames -> parent (cursor: only the undelivered tail).
        child_msgs: list[tuple[FrameDocument, dict]] = []
        for doc in frames:
            drain_frame_posts(doc)
            pms = doc.post_messages
            if doc.delivered_count >= len(pms):
                continue
            for m in pms[doc.delivered_count:]:
                if m.get("target") in {"parent", "top", "reply", "*", ""} or is_port_message(m):
                    child_msgs.append((doc, m))
            doc.delivered_count = len(pms)
        if child_msgs:
            progressed = True
            stamped = []
            for doc, m in child_msgs:
                s = dict(m)
                s["source_window"] = {"window": "frame", "src": doc.seed_src or doc.url}
                stamped.append(s)
            port_child = [s for s in stamped if is_port_message(s)]
            window_child = [s for s in stamped if not is_port_message(s)]
            if port_child:
                routed, unrouted, produced = await _route_port_msgs(
                    port_child,
                    parent_ctx=parent_ctx,
                    parent_html=parent_html,
                    parent_url=parent_url,
                    client=client,
                    frames=frames,
                )
                summary["port_routed"] += routed
                summary["port_unrouted"] += unrouted
                parent_msgs.extend(produced)
            if window_child:
                summary["child_delivered"] += len(window_child)
                out = await run_scripts(
                    parent_html, parent_url, client, force=True,
                    messages=window_child, ctx=parent_ctx,
                )
                out_info = out[1] if isinstance(out, tuple) else out
                # Ports transferred frame->parent: rewire the frame's kept
                # ends to the proxies the parent's realm just materialized.
                summary["port_wires"] += _wire_received_ports(
                    window_child, receiving_ctx=parent_ctx,
                    resolve_ctx_by_nonce=_resolver(parent_ctx, frames),
                )
                parent_msgs.extend(out_info.get("post_messages") or [])

        if not progressed:
            break
        summary["rounds"] += 1
    return summary


def _doc_identity(doc: FrameDocument) -> str:
    """Stable per-document identity for reuse matching: the pre-redirect
    resolved URL for src frames, the srcdoc content for srcdoc frames."""
    if doc.seed_srcdoc:
        return f"srcdoc:{hash(doc.seed_srcdoc)}"
    return doc.origin_url or doc.url


async def load_frames(
    html: str,
    url: str,
    client: httpx.AsyncClient,
    *,
    js: str = "auto",
    max_frames: int = MAX_FRAMES,
    max_depth: int = MAX_FRAME_DEPTH,
    synthetic_captcha: bool = True,
    parent_headers: dict | None = None,
    parent_messages: list[dict] | None = None,
    cached: list[FrameDocument] | None = None,
    parent_ctx=None,
) -> list[FrameDocument]:
    """Fetch a bounded, flat frame tree with parent refs.

    With ``cached`` — the frame documents already loaded for THIS document,
    as on an action pass — unchanged frames are REUSED, not re-fetched and
    re-run: their live QuickJS contexts, DOM state, and accumulated
    postMessages persist between actions exactly like the parent page's
    context does. Only frames whose identity changed (new or moved src,
    changed srcdoc, removed iframe) are fetched fresh, and new parent
    messages are delivered into the reused frame's live context instead of
    replaying its whole document.
    """
    # Reuse index: (parent ref at load time, identity) -> cached document.
    cache: dict[tuple[str, str], FrameDocument] = {}
    if cached:
        for doc in cached:
            cache[(doc.parent_ref, _doc_identity(doc))] = doc

    queue = _discover_seeds(
        html,
        url,
        parent_ref="",
        depth=1,
        synthetic_captcha=synthetic_captcha,
    )
    documents: list[FrameDocument] = []
    seen: set[tuple[str, str]] = set()
    # New positional ref -> load-time ref for reused documents; nested seeds
    # look up their cache entries under the parent's LOAD-TIME ref.
    new_to_old: dict[str, str] = {}
    reused: list[FrameDocument] = []

    while queue and len(documents) < max_frames:
        seed = queue.pop(0)
        frame_url = urljoin(seed.parent_url, seed.src) if seed.src else seed.parent_url
        identity = (seed.parent_ref, frame_url if seed.src else f"srcdoc:{hash(seed.srcdoc)}")
        if identity in seen:
            continue
        seen.add(identity)

        ref = f"f{len(documents) + 1}"

        # Reuse lookup: same identity under the same parent. Depth-1 frames
        # are keyed under the root parent (""); nested frames under their
        # parent's load-time ref.
        hit: FrameDocument | None = None
        if cached is not None:
            parent_old = "" if seed.parent_ref == "" else new_to_old.get(seed.parent_ref)
            if parent_old is not None:
                ident = f"srcdoc:{hash(seed.srcdoc)}" if seed.srcdoc else frame_url
                hit = cache.get((parent_old, ident))

        if hit is not None:
            # REUSE: the frame is alive -- keep its ctx, DOM, and messages.
            # Only the positional ref and parent wiring are refreshed.
            old_ref = hit.ref
            hit.ref = ref
            hit.parent_ref = seed.parent_ref
            hit.depth = seed.depth
            if seed.title:
                hit.title = seed.title
            if seed.name:
                hit.name = seed.name
            documents.append(hit)
            new_to_old[ref] = old_ref
            reused.append(hit)
            # Nested frames come from the reused document's current html:
            # present children are matched against the cache next round,
            # removed ones drop out, new ones fetch fresh.
            if hit.html and seed.depth < max_depth:
                queue.extend(
                    _discover_seeds(
                        hit.html,
                        hit.url,
                        parent_ref=ref,
                        depth=seed.depth + 1,
                        synthetic_captcha=synthetic_captcha,
                    )
                )
            continue

        document = FrameDocument(
            ref=ref,
            parent_ref=seed.parent_ref,
            url=frame_url,
            title=seed.title,
            name=seed.name,
            depth=seed.depth,
            origin_url=frame_url,
            seed_src=seed.src,
            seed_srcdoc=seed.srcdoc,
        )
        try:
            frame_headers: dict | None = None
            if seed.srcdoc:
                document.status = 200
                document.content_type = "text/html"
                document.html = seed.srcdoc
            elif not seed.src or frame_url.startswith("about:"):
                document.status = 200
                document.content_type = "text/html"
                document.html = ""
            elif frame_url.startswith("data:"):
                document.html, document.content_type = _decode_data_url(frame_url)
                document.status = 200
            elif urlparse(frame_url).scheme in {"http", "https"}:
                result = await fetch(
                    frame_url,
                    client=client,
                    headers={"Referer": seed.parent_url},
                )
                document.url = result.final_url or frame_url
                document.status = result.status
                document.content_type = result.content_type
                document.html = result.body
                frame_headers = result.headers
                from webresearch.engine.framing import check_frame_embedding
                block_reason, policy_notes = check_frame_embedding(
                    result.headers, seed.parent_url, document.url
                )
                from webresearch.engine.cross_origin import check_subresource as _check_xo
                xo_reason, xo_notes = _check_xo(
                    result.headers, document.url, seed.parent_url, parent_headers
                )
                policy_notes = list(policy_notes) + list(xo_notes)
                if block_reason is None:
                    block_reason = xo_reason
                if policy_notes:
                    document.js_info["frame_policies"] = policy_notes
                if block_reason is not None:
                    document.error = block_reason
                    document.html = ""
            else:
                document.error = f"unsupported frame URL scheme: {urlparse(frame_url).scheme}"

            if document.html and "html" in document.content_type.lower() and js != "never":
                frame_inbox = _frame_inbox(ref, document.url, url, parent_messages)
                document.html, frame_js = await run_scripts(
                    document.html,
                    document.url,
                    client,
                    force=(js == "always"),
                    headers=frame_headers,
                    messages=frame_inbox,
                )
                document.js_info.update(frame_js)
                # Ports transferred parent->frame at load: rewire the parent's
                # kept ends to the proxies this fresh realm materialized.
                if parent_ctx is not None and any(m.get("ports") for m in frame_inbox):
                    _wire_received_ports(
                        frame_inbox,
                        receiving_ctx=document.js_info.get("ctx"),
                        resolve_ctx_by_nonce=_resolver(parent_ctx, documents),
                    )
            # The frame's document has loaded: fire `load` on its iframe
            # element in the owning realm (real browser lifecycle).
            if seed.src or seed.srcdoc:
                _fire_frame_load(parent_ctx, documents, seed, frame_url)
        except Exception as exc:
            document.error = f"{type(exc).__name__}: {exc}"[:500]

        documents.append(document)
        if document.html and seed.depth < max_depth:
            queue.extend(
                _discover_seeds(
                    document.html,
                    document.url,
                    parent_ref=ref,
                    depth=seed.depth + 1,
                    synthetic_captcha=synthetic_captcha,
                )
            )

    # Parent -> frame messages for REUSED frames are delivered into the
    # live context (no document re-run): the frame keeps its state, message
    # handlers run and may reply, and the pump's fresh postMessages
    # accumulate on the document for the session's child -> parent cursor.
    if reused and parent_messages:
        for doc in reused:
            frame_ctx = doc.js_info.get("ctx")
            if frame_ctx is None:
                continue
            inbox = _frame_inbox(doc.ref, doc.url, url, parent_messages)
            if not inbox:
                continue
            try:
                pumped_html, pump_info = await run_scripts(
                    doc.html,
                    doc.url,
                    client,
                    force=True,
                    messages=inbox,
                    ctx=frame_ctx,
                )
                doc.html = pumped_html or doc.html
                doc.js_info.setdefault("post_messages", []).extend(
                    pump_info.get("post_messages") or []
                )
                # Ports transferred parent->frame into a reused (live) realm:
                # the parent's kept ends learn the proxies' addresses here.
                _wire_received_ports(
                    inbox,
                    receiving_ctx=frame_ctx,
                    resolve_ctx_by_nonce=_resolver(parent_ctx, documents),
                )
                extra_errors = int(pump_info.get("errors") or 0)
                if extra_errors:
                    doc.js_info["errors"] = int(doc.js_info.get("errors") or 0) + extra_errors
                    doc.js_info.setdefault("error_messages", []).extend(
                        (pump_info.get("error_messages") or [])[:5]
                    )
            except Exception as exc:
                doc.error = f"{type(exc).__name__}: {exc}"[:500]

    return documents


def frame_infos(documents: list[FrameDocument]) -> list[FrameInfo]:
    return [document.to_info() for document in documents]

