"""Complete action surface for agents.

The original `inspect.py` enumerates interactive elements in document order and
stops at 80, with no signal that more exist. On a real page (a Nuxt marketing
site, for example) that silently hides 238 of 318 controls, including 136 of 183
links. An agent cannot plan against a surface it cannot see the shape of.

This module enumerates the *whole* surface, assigns stable refs that survive
ordering, and reports exactly what was omitted when a budget forces a cut. It
also separates the three ways an agent can act on a control:

* ``navigate``  - the element carries an href the HTTP lane can follow
* ``submit``    - the element belongs to a form the HTTP lane can POST
* ``activate``  - a bare control (button, checkbox, slider) that only a real
                  engine can actuate, because there is no href and no form

That last category is where agent capability actually ends today, so the view
says so plainly instead of presenting those controls as if they were clickable.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser as HTMLParser
from selectolax.lexbor import LexborNode as Node

from webresearch.parse import Document

# Elements that only a layout/render engine can actuate.
ACTIVE_TAGS = {"button", "input", "textarea", "select", "option", "label", "summary"}
EMBED_TAGS = {"iframe", "canvas", "video", "audio", "object", "embed"}
NAV_TAGS = {"a", "area"}

# Sliders and drag handles are real controls on modern challenge widgets, and
# the markup gives no standard signal for them. Everything below matches class
# tokens *exactly*, never by substring: in real markup "slider" is a prefix of
# sliderContainer, sliderMask, sliderbg and sliderIcon, so a substring test
# cannot tell the handle from the parts around it. That single confusion is
# what turns one control into six phantom actions.
DRAG_ATTR_HINTS = ("drag", "draggable", "swipe", "scrubber")

# Elements naming themselves as the movable handle.
_HANDLE_TOKEN_NAMES = (
    "slider", "handle", "knob", "grip", "lever", "thumb", "carriage", "cursor",
)

# Elements naming themselves as the drop target.
# Matched against token *suffixes*, since a BEM library will prefix them:
# '.slider' -> 'slider', 'ddv1-slider-target' -> 'slidertarget'.
_DRAG_TARGET_CLASSES = ("slidertarget", "dragtarget", "droptarget", "thumbtarget")

# Containers that host a handle. Consulted only to locate a handle's group,
# never to qualify the container itself as one.
_DRAG_CONTAINER_CLASSES = (
    "slidercontainer", "dragcontainer", "trackwrapper", "sliderwrap",
    "rangecontainer",
)

_DRAG_BARENESS_HINTS = ("handle", "knob", "grip", "lever")
_MEDIA_TAGS = {"audio", "video", "track", "source", "iframe", "canvas", "img", "picture"}

# Generic class names that describe plumbing rather than purpose. They are too
# weak to label a control with.
_MACHINERY_TOKENS = frozenset(
    {
        "active", "disabled", "hidden", "visible", "wrapper", "container",
        "inner", "outer", "content", "wrapper", "item", "list", "row",
        "col", "cell", "block", "element", "component", "module", "root",
        "base", "main", "wrap", "box", "group", "container", "parent", "child",
    }
)

# Parts of a slider that are presentational and must never be offered as the
# handle: the rail behind it, the fill that tracks progress, and the icon drawn
# inside either one. Naming them here is clearer than trying to infer intent
# from the absence of a label.
_SLIDER_DECORATION_TOKENS = frozenset(
    {
        "sliderbg", "slidermask", "slidericon", "slidertargeticon",
        "slidertrack", "sliderrail", "sliderfill", "slidershadow",
        "sliderbgactive", "thumbicon", "targeticon",
    }
)

_ANON_HANDLE_CLASSES = _HANDLE_TOKEN_NAMES


@dataclass(slots=True)
class Action:
    """One thing an agent can try to do, and how it can be done."""

    ref: str
    kind: str  # 'navigate' | 'submit' | 'activate' | 'drag' | 'dropzone' | 'embed'
    role: str
    label: str
    target: str = ""  # url for navigate, form index for submit, selector for drag
    # For a form field: the `name` an HTTP submit would send it under, plus a CSS
    # selector for the element itself. Both are needed and they are not
    # interchangeable: `name` is for the wire ("email"), `selector` is for
    # addressing the element ("#email"), and `target` is the form it belongs to.
    field_name: str = ""
    selector: str = ""
    tag: str = ""
    checked: bool | None = None
    disabled: bool = False
    frame: str = ""  # set when the control lives inside an iframe document
    notes: str = ""

    @property
    def callable_without_engine(self) -> bool:
        """True when the HTTP lane alone can carry this out."""
        return self.kind in {"navigate", "submit"}


@dataclass
class ActionSurface:
    """The full inventory plus an honest account of what was left out."""

    actions: list[Action] = field(default_factory=list)
    total_found: int = 0
    truncated: int = 0
    frame_actions: int = 0
    engine_only: int = 0
    dropzones: int = 0

    @property
    def complete(self) -> bool:
        return self.truncated == 0

    def coverage_line(self) -> str:
        if not self.truncated:
            return f"ACTIONS: {len(self.actions)} of {self.total_found} (complete)"
        return (
            f"ACTIONS: {len(self.actions)} of {self.total_found} "
            f"({self.truncated} hidden by budget; raise limit or filter by kind)"
        )

    def view(self, *, limit: int = 60) -> str:
        lines = [self.coverage_line()]
        if self.engine_only:
            lines.append(
                f"ENGINE_ONLY: {self.engine_only} control(s) need a real browser "
                "(no href, no form) — HTTP click cannot reach them"
            )
        if self.frame_actions:
            lines.append(
                f"IN_FRAMES: {self.frame_actions} control(s) live inside iframe "
                "documents; read each frame source before planning"
            )
        for action in self.actions[:limit]:
            label = action.label or action.tag or "?"
            line = f"[{action.ref}] {action.kind:8} {action.role:9} {label[:64]}"
            if action.target:
                line += f" -> {action.target[:70]}"
            if action.checked is not None:
                line += f" checked={str(action.checked).lower()}"
            if action.disabled:
                line += " (disabled)"
            if action.notes:
                line += f"  # {action.notes}"
            lines.append(line)
        if len(self.actions) > limit:
            lines.append(f"... {len(self.actions) - limit} more; query by kind or raise limit")
        return "\n".join(lines)


def _text_of(node: Node, *, limit: int = 120) -> str:
    """Best available label: what a person would call the control, first.

    Visible text and human-facing attributes outrank machine identifiers. An
    ordering that preferred `id` labelled `<button id="go">Go</button>` as "go",
    which is both wrong and unpronounceable. `name`/`value`/`id` are consulted
    only when nothing human-facing exists.
    """
    attrs = node.attributes
    for attr in ("aria-label", "title", "alt", "placeholder"):
        value = (attrs.get(attr) or "").strip()
        if value and len(value) > 1:
            return value[:limit]
    text = node.text(separator=" ", strip=True).strip()
    if text:
        return text[:limit]
    for attr in ("name", "value", "id"):
        value = (attrs.get(attr) or "").strip()
        if value and len(value) > 1:
            return value[:limit]
    for cls in (attrs.get("class") or "").split():
        label = _class_label(cls) if not _is_mechanics_class(cls) else ""
        if label:
            return label[:limit]
    return ""


def _is_editable(node: Node) -> bool:
    """True when the element itself is contenteditable (empty string = true)."""
    raw = node.attributes.get("contenteditable")
    if raw is None:
        return False
    return raw.strip().lower() != "false"


def _role_of(node: Node, kind: str) -> str:
    attrs = node.attributes
    blob = " ".join(
        [
            node.tag or "",
            attrs.get("type") or "",
            attrs.get("role") or "",
            attrs.get("class") or "",
            attrs.get("id") or "",
        ]
    ).lower()
    if "captcha" in blob:
        return "captcha"
    if kind == "drag" or any(h in blob for h in DRAG_ATTR_HINTS):
        return "slider" if "slider" in blob or "range" in blob else "drag"
    if attrs.get("type") == "checkbox" or (attrs.get("role") or "").lower() == "checkbox":
        return "checkbox"
    if attrs.get("type") == "radio":
        return "radio"
    typ = (attrs.get("type") or "").lower()
    if node.tag in {"input", "textarea", "select"}:
        if typ in {"submit", "button", "image", "reset"}:
            return "button"
        if node.tag == "select":
            return "select"
        if node.tag == "textarea":
            return "textarea"
        if typ == "file":
            return "file"
        if typ == "password":
            return "password"
        # Normalise the HTML `type` into a role name, so `type=text` reads as
        # "textbox" the way inspect.py reports it.
        return "checkbox" if typ == "checkbox" else "textbox"
    if node.tag == "button":
        return "button"
    if _is_editable(node):
        return "editable"
    if node.tag == "label":
        return "label"
    if node.tag in {"iframe", "canvas", "video", "audio"}:
        return node.tag
    if node.tag in NAV_TAGS:
        return "link"
    if typ == "submit":
        return "button"
    return (attrs.get("role") or node.tag or "element").lower()


def _disabled(node: Node) -> bool:
    attrs = node.attributes
    if "disabled" in attrs:
        return True
    if attrs.get("aria-disabled") == "true":
        return True
    parent = node.parent
    hops = 0
    while parent is not None and hops < 4:
        if "disabled" in parent.attributes:
            return True
        parent = parent.parent
        hops += 1
    return False


def _checked(node: Node) -> bool | None:
    attrs = node.attributes
    aria = (attrs.get("aria-checked") or "").lower()
    if aria in {"true", "false"}:
        return aria == "true"
    typ = (attrs.get("type") or "").lower()
    if typ in {"checkbox", "radio"} or (attrs.get("role") or "").lower() == "checkbox":
        if "checked" in attrs:
            return str(attrs.get("checked")).lower() != "false"
        return False
    return None


def _stable_ref(doc_url: str, node: Node, index: int) -> str:
    """Content-addressed ref: stable across reorderings of unrelated elements.

    Identity is the element's own description, never its position, so inserting
    a banner does not invalidate every ref on the page. Distinguishing
    attributes are included because repeated controls (six one-digit audio
    boxes, a radio group) differ by nothing else and would otherwise collapse
    onto a single ref.
    """
    attrs = node.attributes
    distinguishing = [
        attrs.get("data-index"),
        attrs.get("data-v"),
        attrs.get("value"),
        attrs.get("aria-label"),
        attrs.get("title"),
        attrs.get("name"),
    ]
    identity = "|".join(
        [
            node.tag or "",
            attrs.get("id") or "",
            attrs.get("href") or "",
            attrs.get("type") or "",
            attrs.get("class") or "",
            *[d for d in distinguishing if d],
            (node.text(separator=" ", strip=True)[:40]),
        ]
    )
    if not identity.strip("|"):
        identity = f"anon-{index}"
    digest = hashlib.sha1(f"{doc_url}|{identity}".encode()).hexdigest()[:8]
    return f"a{digest}"


def _escape_ident(value: str) -> str:
    """Escape a value for use inside a CSS identifier.

    Tailwind emits class names containing ':' and '[' ('lg:tw-flex',
    'tw-max-w-300'). Unescaped, they produce selectors the engine rejects, which
    is how a correct ref ended up with a selector that matched nothing.
    """
    return re.sub(r"([^\w-])", r"\\\1", value)


def _css_path(node: Node, root: Node) -> str:
    """Approximate selector usable against a real engine.

    Stops at the nearest id, since a unique id makes every ancestor step
    unnecessary. Class segments are escaped and limited to a few, because
    utility-class frameworks emit long unstable lists. The result is a plain
    descendant chain so it resolves against whichever document the control
    actually lives in, including a cross-origin challenge frame.
    """
    parts: list[str] = []
    cursor: Node | None = node
    hops = 0
    while cursor is not None and hops < 6:
        tag = cursor.tag or "*"
        node_id = cursor.attributes.get("id")
        if node_id:
            parts.append(f"#{_escape_ident(node_id)}")
            break
        cls = (cursor.attributes.get("class") or "").split()
        if cls:
            # Two classes is enough to disambiguate and keeps the selector
            # short; utility frameworks change class order on every build.
            parts.append(tag + "".join("." + _escape_ident(c) for c in cls[:2]))
        else:
            parts.append(tag)
        cursor = cursor.parent
        hops += 1

    # Drop ancestors the engine cannot help with anyway. 'html' and 'body' are
    # in every document, so keeping them only lengthens the selector, and
    # '-document' is not a valid CSS type selector at all: it leaked into the
    # path above and made every selector fail to resolve.
    while parts and parts[-1].split(".", 1)[0] in {"html", "body", "-document"}:
        parts.pop()
    return " > ".join(reversed(parts)) if parts else (node.tag or "*")


def _form_index_of(node: Node) -> int | None:
    parent = node.parent
    hops = 0
    while parent is not None and hops < 24:
        if parent.tag == "form":
            idx = 1
            root = parent
            while root.parent is not None:
                root = root.parent
            for form in root.css("form"):
                if form is parent or form.html == parent.html:
                    return idx
                idx += 1
            return idx
        parent = parent.parent
        hops += 1
    return None


def _is_mechanics_class(cls: str) -> bool:
    """Class names that describe how a control works, not what it does."""
    tokens = {cls.lower()}
    tokens |= {t for t in re.split(r"[_\-]", cls.lower()) if t}
    tokens |= {t for t in re.findall(r"[A-Z]?[a-z0-9]+", cls)}
    return bool(
        tokens
        & (set(_HANDLE_TOKEN_NAMES) | set(_DRAG_TARGET_CLASSES) | _SLIDER_DECORATION_TOKENS)
    )


def _class_label(cls: str) -> str:
    """Turn a CSS class into a readable label, or '' when it is plumbing.

    Icon-only controls are routinely identified only by class ('logo', 'close',
    'menu'), and those are perfectly good labels. BEM and state classes are not:
    'sliderTargetIcon' and 'is-active' describe machinery, so they are rejected
    rather than shown to the model as if a person had named the control.
    """
    if len(cls) <= 2 or not cls.replace("-", "").isalpha():
        return ""
    if any(ch.isupper() for ch in cls):
        # camelCase / BEM: split on case boundaries for readability, but only
        # accept it when the result is a real word rather than plumbing.
        words = re.findall(r"[A-Z]?[a-z0-9]+", cls)
        if len(words) < 2:
            return ""
        if any(w.lower() in _MACHINERY_TOKENS for w in words):
            return ""
        return " ".join(words).title()
    if cls.lower() in _MACHINERY_TOKENS:
        return ""
    return cls.replace("-", " ").title()


def collect(html: str, url: str, *, max_actions: int = 200) -> ActionSurface:
    """Enumerate every control the agent could act on, with honest accounting."""
    tree = HTMLParser(html)
    root = tree.root
    surface = ActionSurface()
    if root is None:
        return surface

    # Deliberately no inside_chrome() filter here. That filter is correct for
    # reading an article, where nav/footer/sidebar are noise, and wrong for
    # driving a page: "Open menu", breadcrumbs, and pagination are precisely
    # the controls an agent needs. Read and drive want different surfaces.
    selector = ", ".join(sorted(NAV_TAGS | ACTIVE_TAGS | EMBED_TAGS))
    candidates: list[tuple[Node, str]] = []
    for node in root.css(selector):
        candidates.append((node, ""))
    # Handles and drop targets are frequently bare divs with no interactive tag and
    # no role, so the tag selector above can never see them. Sweep for the
    # class/structure signature separately and de-duplicate. Contenteditable
    # elements are the same story: any tag can carry the attribute.
    seen_nodes = {id(node) for node, _ in candidates}
    for node in root.css("div, span, i, section, li"):
        if id(node) in seen_nodes:
            continue
        if not (_is_drag(node) or _is_drop_target(node)):
            continue
        candidates.append((node, ""))
        seen_nodes.add(id(node))
    for node in root.css("[contenteditable]"):
        if id(node) in seen_nodes:
            continue
        if not _is_editable(node):
            continue
        candidates.append((node, ""))
        seen_nodes.add(id(node))

    candidates = _pick_one_handle_per_container(candidates)

    surface.total_found = len(candidates)

    ordered: list[Action] = []
    for index, (node, _) in enumerate(candidates, start=1):
        tag = node.tag or ""
        attrs = node.attributes
        href = attrs.get("href") or ""
        label = _text_of(node)
        # Handles are deliberately unlabelled in the DOM; the only thing an
        # agent can be told is that this is a drag target, so synthesise it.
        if not _label_of(node) and _is_drag(node):
            label = "drag handle"

        form_index = _form_index_of(node)
        is_submit_control = (
            tag == "button"
            and (attrs.get("type") or "").lower() in {"submit", "image"}
        ) or (tag == "input" and (attrs.get("type") or "").lower() in {"submit", "image"})

        if tag in NAV_TAGS and href and not href.startswith("#") and not href.lower().startswith("javascript:"):
            kind = "navigate"
            target = urljoin(url, href)
        elif tag == "form":
            continue
        elif _is_drop_target(node):
            # Where the handle has to arrive. Recorded so an agent can see the
            # goal geometry, but it is not itself actuated.
            kind = "dropzone"
            target = _css_path(node, root)
        elif _is_drag(node):
            # Checked before form membership: a range input inside a form is
            # still actuated by dragging, not by POSTing a value.
            kind = "drag"
            target = _css_path(node, root)
        elif _is_editable(node):
            # Rich editors (Quill/ProseMirror/Medium) are typed into via the
            # engine: text lands in textContent with input events carrying data.
            kind = "activate"
            target = _css_path(node, root)
        elif form_index is not None and (is_submit_control or tag in {"input", "textarea", "select", "button"}):
            kind = "submit"
            target = f"form[{form_index}]"
        elif _has_drag_ancestor(node, root):
            # A slider handle is often a bare div with no drag semantics of its
            # own, identifiable only from its container or a sibling target.
            kind = "drag"
            target = _css_path(node, root)
        elif tag in EMBED_TAGS:
            kind = "embed"
            target = urljoin(url, (attrs.get("src") or attrs.get("data") or "")) if (attrs.get("src") or attrs.get("data")) else ""
        elif tag in ACTIVE_TAGS:
            kind = "activate"
            target = _css_path(node, root)
        else:
            continue

        # Hidden inputs are submission tokens (CSRF, captcha hashes, DataDome
        # cids), never something the agent fills. Listing them as actions
        # invites it to invent values, so keep them out of the action surface
        # and let form extraction carry them instead.
        if tag == "input" and (attrs.get("type") or "").lower() == "hidden":
            continue

        notes = ""
        if kind in {"activate", "drag", "embed"}:
            notes = "needs real engine"
        if _is_editable(node):
            notes = "contenteditable: pass text to type into the editor"
        if tag == "canvas":
            notes = "canvas element: visual pixels not rendered natively; inspect AST/attributes or data-URL"
        elif tag in {"video", "audio"}:
            notes = "media stream: inspect audio/video source and track attributes"
        elif tag in EMBED_TAGS:
            notes = "content is a separate document; read its source"

        ordered.append(
            Action(
                ref=_stable_ref(url, node, index),
                kind=kind,
                role=_role_of(node, kind),
                label=label,
                target=target,
                field_name=(
                    (attrs.get("name") or attrs.get("id") or "") if kind == "submit" else ""
                ),
                selector=_css_path(node, root),
                tag=tag,
                checked=_checked(node),
                disabled=_disabled(node),
                notes=notes,
            )
        )

    surface.engine_only = sum(
        1 for a in ordered if not a.callable_without_engine
    )
    surface.dropzones = sum(1 for a in ordered if a.kind == "dropzone")
    for action in ordered:
        if action.kind == "dropzone":
            action.label = "drop target"
            action.notes = "drag goal; reached by dragging a handle onto it"

    kept = ordered[:max_actions]
    surface.truncated = max(0, len(ordered) - len(kept))
    surface.actions = kept
    return surface


def _has_drag_ancestor(node: Node, root: Node) -> bool:
    """True when the node sits under a drag container with a move target.

    Captures the common widget shape where the handle carries no draggable
    attribute and only its parent declares the affordance.
    """
    parent = node.parent
    hops = 0
    while parent is not None and parent is not root and hops < 4:
        blob = " ".join(
            [
                parent.tag or "",
                parent.attributes.get("class") or "",
                parent.attributes.get("id") or "",
            ]
        ).lower()
        if any(hint in blob for hint in DRAG_ATTR_HINTS):
            return True
        parent = parent.parent
        hops += 1
    return False


def _class_tokens(node: Node | None) -> set[str]:
    """Normalised class/id/role tokens, split so 'sliderMask' != 'slider'."""
    if node is None:
        return set()
    attrs = node.attributes or {}
    raw = " ".join(
        [
            attrs.get("class") or "",
            attrs.get("id") or "",
            attrs.get("role") or "",
        ]
    )
    tokens: set[str] = set()
    for tok in re.split(r"[\s\-_]+", raw.lower()):
        if not tok:
            continue
        tokens.add(tok)
        # BEM blocks: captcha__slider__icon -> also contribute 'slider'.
        for part in tok.split("__"):
            if part and part != tok:
                tokens.add(part)
    return tokens


def _is_drop_target(node: Node) -> bool:
    """A slot a handle is dragged into, as opposed to the handle itself.

    DataDome ships both inside one container (`.slider` and `.sliderTarget`), so
    the solver would aim at the wrong element without this distinction. Matched
    by token equality and suffix because a component library prefixes its
    classes: 'ddv1-slider-target' still resolves to a drop target.
    """
    tokens = _class_tokens(node)
    if tokens & set(_DRAG_TARGET_CLASSES):
        return True
    return any(
        token.endswith(name)
        for token in tokens
        for name in _DRAG_TARGET_CLASSES
    )


def _has_own_name(node: Node) -> bool:
    """Does this element's own vocabulary name it as a drag part?

    Token-exact, never substring: 'slider' must not match 'sliderContainer',
    'sliderMask' or 'sliderbg', which is exactly the confusion that made one
    handle read as six phantom controls.
    """
    tokens = _class_tokens(node)
    if not tokens:
        return False
    if tokens & set(_HANDLE_TOKEN_NAMES):
        return True
    return any(token in _DRAG_BARENESS_HINTS for token in tokens)


def _matches_container(node: Node | None, names: tuple[str, ...]) -> bool:
    wanted = set(names)
    return bool(_class_tokens(node) & wanted)


def _is_drag(node: Node) -> bool:
    """Is this element the movable handle of a drag control?

    Kept deliberately short, and ordered so the two unambiguous signals win
    before any structural guess: explicit ARIA/native semantics first, then the
    element's own class token, then container shape. Everything below the first
    two rules is inference, and inference is where the phantom handles came from.
    """
    attrs = node.attributes
    typ = (attrs.get("type") or "").lower()
    if typ == "range":
        return True
    if attrs.get("draggable") == "true":
        return True
    if attrs.get("role") in {"slider", "scrollbar"}:
        return True
    if (attrs.get("aria-valuenow") is not None) and attrs.get("aria-valuemin") is not None:
        return True

    # Media and frames are never handles, though "track" collides with slider
    # vocabulary and "range" is an input type.
    if node.tag in _MEDIA_TAGS:
        return False

    tokens = _class_tokens(node)

    # A drop target is the goal, not the thing that moves.
    if _is_drop_target(node):
        return False

    # Presentational slider parts share the handle's shape, so they are named
    # out before any structural rule can claim them.
    if tokens & _SLIDER_DECORATION_TOKENS:
        return False

    # The handle names itself: a bare 'slider' / 'handle' / 'thumb' token.
    if tokens & set(_HANDLE_TOKEN_NAMES):
        return True

    if tokens & set(DRAG_ATTR_HINTS):
        return True

    # A real <input type=range> is a slider by definition, and a control that
    # advertises aria-valuenow already looks like a value-bearing slider.
    if typ == "range":
        return True

    # Last resort: an unnamed div inside a known drag container that also
    # renders a drop target. Catches handles named by an author who used no
    # vocabulary at all.
    parent = node.parent
    if (
        parent is not None
        and not _label_of(node)
        and _matches_container(parent, _DRAG_CONTAINER_CLASSES)
    ):
        for sibling in parent.css("*"):
            if sibling is node or sibling.tag in _MEDIA_TAGS:
                continue
            if _class_tokens(sibling) & set(_DRAG_TARGET_CLASSES):
                return True
    return False


def _affordance_root(node: Node) -> str:
    """Nearest ancestor that itself declares a drag affordance, else the parent.

    For `<div class=sliderContainer><div class=slider><i class=sliderIcon>` the
    container, handle and icon all resolve to the same root, so exactly one
    survives the dedupe.
    """
    cursor = node.parent
    hops = 0
    while cursor is not None and hops < 6:
        if _matches_container(cursor, _DRAG_CONTAINER_CLASSES):
            return "".join(sorted(_class_tokens(cursor)))
        cursor = cursor.parent
        hops += 1
    return ""


def _pick_one_handle_per_container(
    candidates: list[tuple[Node, str]],
) -> list[tuple[Node, str]]:
    """A slider has one handle. Its container, mask and icon all match the
    drag signature, so keeping every match produces N phantom handles for one
    control. Keep the deepest match per container: the handle is the innermost
    element, while the container and mask are its ancestors.
    """
    # Group by the nearest ancestor that *declares* the affordance, not by the
    # immediate parent. A handle, its icon, and its mask are siblings under one
    # container; keying on the immediate parent would treat them separately.
    by_container: dict[str, tuple[int, int]] = {}
    for position, (node, _) in enumerate(candidates):
        if not _is_drag(node):
            continue
        # Handles identified by their own class name are already unambiguous,
        # so they bypass container grouping.
        if _label_of(node):
            continue
        key = _affordance_root(node)
        depth = _depth(node)
        current = by_container.get(key)
        if current is None or depth > current[1]:
            by_container[key] = (position, depth)

    keep = {position for position, _ in by_container.values()}
    keep.update(
        position
        for position, (node, _) in enumerate(candidates)
        if _is_drag(node) and _label_of(node)
    )
    return [
        item
        for position, item in enumerate(candidates)
        if not _is_drag(item[0]) or position in keep
    ]


def _depth(node: Node) -> int:
    depth = 0
    cursor = node.parent
    while cursor is not None and depth < 32:
        depth += 1
        cursor = cursor.parent
    return depth


def _label_of(node: Node) -> str:
    """Human-visible identity of an element, or empty when it has none.

    Deliberately ignores `name` and `id`: those are machine identifiers, they
    never make a control meaningful to an agent, and treating them as labels hid
    the fact that a slider handle carries no text at all.
    """
    attrs = node.attributes or {}
    for attr in ("aria-label", "title", "alt", "placeholder"):
        value = (attrs.get(attr) or "").strip()
        if value:
            return value
    text = node.text(separator=" ", strip=True).strip()
    if text:
        return text
    # Icon-only controls are routinely identified purely by class ('logo',
    # 'close', 'menu'), and those are perfectly good labels. Accept a class as
    # a label only when it reads as a name; machinery and handle classes do not,
    # which is what keeps the slider handle reported as unlabelled.
    for cls in (attrs.get("class") or "").split():
        # A handle's own name is vocabulary about *mechanics* ('slider',
        # 'handle'), not about what the control does. Surfacing it would make
        # an unlabelled handle look self-describing and hide the fact that the
        # agent has nothing to go on. Drop targets get a synthesised label for
        # the same reason: 'Slidertarget' is a class name, not a noun a person
        # would use.
        if _is_mechanics_class(cls):
            continue
        label = _class_label(cls)
        if label:
            return label
    return ""


def surface_from_doc(doc: Document, *, max_actions: int = 200) -> ActionSurface:
    return collect(doc.body, doc.url, max_actions=max_actions)