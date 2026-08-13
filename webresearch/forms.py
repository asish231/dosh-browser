from __future__ import annotations

from urllib.parse import urljoin

from webresearch.parse import Document
from webresearch.types import FieldSpec, Form

INPUT_TAGS = "input, textarea, select, button"


def forms(doc: Document, *, max_forms: int = 12) -> list[Form]:
    """Extract forms the LLM can fill and submit over HTTP."""
    if doc.tree is None:
        return []
    out: list[Form] = []
    for i, node in enumerate(doc.tree.css("form"), start=1):
        if i > max_forms:
            break
        method = (node.attributes.get("method") or "GET").upper()
        action = urljoin(doc.url, node.attributes.get("action") or doc.url)
        fields: list[FieldSpec] = []
        for el in node.css(INPUT_TAGS):
            attrs = el.attributes
            ftype = (attrs.get("type") or el.tag or "text").lower()
            if ftype in {"hidden", "submit", "button", "image", "reset"}:
                name = attrs.get("name") or ""
                if ftype == "hidden" and name:
                    fields.append(
                        FieldSpec(
                            name=name,
                            type="hidden",
                            value=attrs.get("value") or "",
                        )
                    )
                continue
            name = attrs.get("name") or attrs.get("id") or ""
            if not name:
                continue
            options: list[str] = []
            if el.tag == "select":
                options = [
                    (opt.attributes.get("value") or opt.text(strip=True))
                    for opt in el.css("option")
                ]
                ftype = "select"
            value = attrs.get("value") or ""
            if el.tag == "textarea":
                value = el.text(strip=True)
                ftype = "textarea"
            fields.append(
                FieldSpec(
                    name=name,
                    type=ftype,
                    value=value,
                    required="required" in attrs,
                    options=options,
                )
            )
        out.append(Form(n=i, method=method, action=action, fields=fields))
    return out
