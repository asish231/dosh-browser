from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Kind = Literal["html", "json", "text", "xml", "other"]


class Link(BaseModel):
    n: int
    text: str
    url: str


class FieldSpec(BaseModel):
    name: str
    type: str = "text"
    value: str = ""
    required: bool = False
    options: list[str] = Field(default_factory=list)


class Form(BaseModel):
    n: int
    method: str = "GET"
    action: str = ""
    fields: list[FieldSpec] = Field(default_factory=list)


class Meta(BaseModel):
    title: str = ""
    description: str = ""
    language: str = ""
    canonical: str = ""
    extra: dict[str, str] = Field(default_factory=dict)


class Target(BaseModel):
    ref: str
    role: str
    text: str = ""
    tag: str = ""
    name: str = ""
    href: str = ""
    checked: bool | None = None
    challenge: bool = False
    path: list[int] = Field(default_factory=list)


class Challenge(BaseModel):
    kind: str
    passable: str = "click"
    detail: str = ""
    refs: list[str] = Field(default_factory=list)


class CaptchaTile(BaseModel):
    id: str = ""
    top: str = ""
    left: str = ""
    row: int | None = None
    column: int | None = None
    role: str = "button"


class FrameInfo(BaseModel):
    ref: str
    parent_ref: str = ""
    url: str = ""
    title: str = ""
    name: str = ""
    depth: int = 0
    status: int = 0
    content_type: str = ""
    js_ran: bool = False
    scripts: int = 0
    challenge: bool = False
    error: str = ""
    html_excerpt: str = ""
    post_messages: list[dict] = Field(default_factory=list)


class CaptchaFrame(FrameInfo):
    kind: str = "iframe"


class CaptchaReport(BaseModel):
    present: bool = False
    kind: str = ""
    site_key: str = ""
    callback: str = ""
    prompt: str = ""
    checkbox_label: str = ""
    checkbox_checked: bool | None = None
    sprite_url: str = ""
    sprite_bytes: int = 0
    sprite_content_type: str = ""
    sprite_sha256: str = ""
    sprite_endpoint: str = ""
    tiles: list[CaptchaTile] = Field(default_factory=list)
    buttons: list[str] = Field(default_factory=list)
    frames: list[CaptchaFrame] = Field(default_factory=list)
    hidden_fields: list[str] = Field(default_factory=list)
    note: str = ""
    view: str = ""


class CaptchaScript(BaseModel):
    src: str = ""
    type: str = ""
    code: str = ""
    frame_ref: str = ""
    truncated: bool = False


class CaptchaCodePart(BaseModel):
    name: str
    ref: str = ""
    kind: str = ""
    url: str = ""
    html: str = ""
    truncated: bool = False


class CaptchaTileSelection(BaseModel):
    tile_id: str = ""
    row: int | None = None
    column: int | None = None
    confidence: float = 0.0
    reason: str = ""


class CaptchaSolution(BaseModel):
    solved: bool = False
    kind: str = ""
    prompt: str = ""
    tiles_selected: list[CaptchaTileSelection] = Field(default_factory=list)
    model_used: str = ""
    attempt: int = 0
    verify_clicked: bool = False
    token: str = ""
    error: str = ""
    view: str = ""


class CaptchaCodeDump(BaseModel):
    present: bool = False
    kind: str = ""
    site_key: str = ""
    callback: str = ""
    parts: list[CaptchaCodePart] = Field(default_factory=list)
    scripts: list[CaptchaScript] = Field(default_factory=list)
    sprite_b64: str = ""
    sprite_content_type: str = ""
    code: str = ""
    note: str = ""


class AcdBlock(BaseModel):
    id: str
    type: str
    text: str = ""
    level: int | None = None
    ref: str = ""
    role: str = ""
    href: str = ""
    rows: list[list[str]] = Field(default_factory=list)
    locator: str = ""


class AcdAction(BaseModel):
    ref: str
    kind: str = "click"
    role: str = ""
    label: str = ""
    name: str = ""
    href: str = ""
    run: str = ""


class AcdApi(BaseModel):
    index: int
    url: str
    status: int = 0
    content_type: str = ""
    method: str = "GET"
    data: Any = None


class Acd(BaseModel):
    version: str = "1"
    url: str
    title: str = ""
    blocks: list[AcdBlock] = Field(default_factory=list)
    actions: list[AcdAction] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)
    api: list[AcdApi] = Field(default_factory=list)
    frames: list[FrameInfo] = Field(default_factory=list)
    framework: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    unavailable: list[str] = Field(default_factory=list)


class Page(BaseModel):
    url: str
    final_url: str = ""
    status: int
    content_type: str = ""
    kind: Kind = "html"
    title: str = ""
    view: str = ""
    links: list[Link] = Field(default_factory=list)
    forms: list[Form] = Field(default_factory=list)
    headings: list[str] = Field(default_factory=list)
    meta: Meta = Field(default_factory=Meta)
    spa_hint: bool = False
    js_ran: bool = False
    targets: list[Target] = Field(default_factory=list)
    challenges: list[Challenge] = Field(default_factory=list)
    frames: list[FrameInfo] = Field(default_factory=list)
    captcha: CaptchaReport | None = None
    acd: Acd | None = None
    engine: str = ""
    chars: int = 0
    headers: dict[str, str] = Field(default_factory=dict)


class FetchResult(BaseModel):
    url: str
    final_url: str
    status: int
    content_type: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    kind: Kind = "other"
    body: str = ""
    truncated: bool = False
    raw: bytes = b""


class ExtractResult(BaseModel):
    url: str
    fields: dict[str, Any] = Field(default_factory=dict)


class SearchHit(BaseModel):
    n: int
    title: str
    url: str
    snippet: str = ""


class SearchResult(BaseModel):
    query: str
    engine: str = ""
    hits: list[SearchHit] = Field(default_factory=list)
    view: str = ""
