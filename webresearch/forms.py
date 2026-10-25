from __future__ import annotations

from urllib.parse import urljoin

from webresearch.parse import Document
from webresearch.types import FieldSpec, Form

INPUT_TAGS = "input, textarea, select, button"


def forms(doc: Document, *, max_forms: int = 12) -> list[Form]:
    """Extract forms the LLM can fill and submit over HTTP.

    Field extraction matches what a real browser would put on the wire:
    - a radio group contributes ONE field (its checked member, if any) with
      every sibling value in ``options`` — the group is exclusive;
    - an unchecked checkbox is omitted entirely (browsers never submit it);
    - a select contributes its selected option's value, defaulting to the
      first option per the HTML spec when none is marked; a ``multiple``
      select contributes one field per selected option.
    """
    if doc.tree is None:
        return []
    out: list[Form] = []
    for i, node in enumerate(doc.tree.css("form"), start=1):
        if i > max_forms:
            break
        method = (node.attributes.get("method") or "GET").upper()
        action = urljoin(doc.url, node.attributes.get("action") or doc.url)
        fields: list[FieldSpec] = []
        seen_radio_groups: set[str] = set()
        for el in node.css(INPUT_TAGS):
            attrs = el.attributes
            ftype = (attrs.get("type") or el.tag or "text").lower()
            if ftype in {"hidden", "submit", "button", "image", "reset"}:
                name = attrs.get("name") or ""
                if ftype == "hidden" and name:
                    # Carry the token so a real submit replays it, but do not
                    # echo the value into the agent's view: it is a session
                    # blob, not input, and a stale copy would be misleading.
                    fields.append(
                        FieldSpec(name=name, type="hidden", value="")
                    )
                continue
            name = attrs.get("name") or attrs.get("id") or ""
            if not name:
                continue
            if el.tag == "textarea":
                fields.append(
                    FieldSpec(
                        name=name,
                        type="textarea",
                        value=el.text(strip=True),
                        required="required" in attrs,
                    )
                )
                continue
            if el.tag == "select":
                options = [
                    (opt.attributes.get("value") or opt.text(strip=True))
                    for opt in el.css("option")
                ]
                chosen = [opt for opt in el.css("option") if "selected" in opt.attributes]
                if not chosen and "multiple" not in attrs and options:
                    # HTML spec: with no option marked selected, the first
                    # option is the selected one.
                    chosen = [el.css("option")[0]]
                for opt in chosen:
                    fields.append(
                        FieldSpec(
                            name=name,
                            type="select",
                            value=(opt.attributes.get("value") or opt.text(strip=True)),
                            required="required" in attrs,
                            options=options,
                        )
                    )
                continue
            if el.tag == "input" and ftype == "radio":
                if name in seen_radio_groups:
                    continue
                seen_radio_groups.add(name)
                group_values: list[str] = []
                checked_value = ""
                required = False
                for r in node.css("input"):
                    r_attrs = r.attributes
                    if (r_attrs.get("type") or "").lower() != "radio":
                        continue
                    if (r_attrs.get("name") or r_attrs.get("id") or "") != name:
                        continue
                    value = r_attrs.get("value") or "on"
                    group_values.append(value)
                    if "checked" in r_attrs and not checked_value:
                        checked_value = value
                    if "required" in r_attrs:
                        required = True
                fields.append(
                    FieldSpec(
                        name=name,
                        type="radio",
                        value=checked_value,
                        required=required,
                        options=group_values,
                    )
                )
                continue
            if el.tag == "input" and ftype == "checkbox":
                if "checked" not in attrs:
                    # Browsers never submit an unchecked checkbox.
                    continue
                fields.append(
                    FieldSpec(
                        name=name,
                        type=ftype,
                        value=(attrs.get("value") or "on"),
                        required="required" in attrs,
                    )
                )
                continue
            fields.append(
                FieldSpec(
                    name=name,
                    type=ftype,
                    value=(attrs.get("value") or ""),
                    required="required" in attrs,
                )
            )
        out.append(Form(n=i, method=method, action=action, fields=fields))
    return out


def hidden_values(doc: Document, index: int) -> dict[str, str]:
    """Live hidden-field values for form ``index``, for use when submitting.

    ``forms()`` reports a hidden field's name but not its value on purpose: the
    value is a session-scoped blob (CSRF token, captcha hash, DataDome cid) that
    goes stale and should never be quoted back to the model as if it were
    something the agent authored. Submission still needs the real value, so it
    is read from the document at submit time.
    """
    if doc.tree is None:
        return {}
    forms_found = doc.tree.css("form")
    if not 1 <= index <= len(forms_found):
        return {}
    out: dict[str, str] = {}
    for el in forms_found[index - 1].css("input"):
        if (el.attributes.get("type") or "").lower() != "hidden":
            continue
        name = el.attributes.get("name") or ""
        value = el.attributes.get("value") or ""
        if name and value:
            out[name] = value
    return out
