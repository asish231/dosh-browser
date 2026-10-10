"""LLM provider + model registry. Secrets stay in .env, never in source."""

from __future__ import annotations

import os
from pathlib import Path

FAMILIES: dict[str, dict[str, str]] = {
    "glm": {
        "vendor": "z.ai",
        "api_key_env": "ZAI_API_KEY",
        "base_url_env": "ZAI_BASE_URL",
        "base_url": "https://api.z.ai/api/paas/v4",
    },
    "mercury": {
        "vendor": "Inception",
        "api_key_env": "INCEPTION_API_KEY",
        "base_url_env": "INCEPTION_BASE_URL",
        "base_url": "https://api.inceptionlabs.ai/v1",
    },
    # Any OpenAI-compatible endpoint, usually a pool fronting many models. The
    # base URL has to be supplied, because the key format alone does not
    # identify the provider: a `bce-v3/AK/SK` pair is a Baidu BCE credential and
    # needs request signing, not a bearer token, so guessing a host produces a
    # 401 that looks like a bad key.
    "openai_compat": {
        "vendor": "OpenAI-compatible",
        "api_key_env": "OPENAI_COMPAT_API_KEY",
        "base_url_env": "OPENAI_COMPAT_BASE_URL",
        "base_url": "",
    },
}

# Ordered: "auto" walks this list and uses the first model with a key that answers.
# Only GLM-4.7-Flash is enabled on z.ai; the paid GLM tiers return 1113 (no balance).
# "id" is what the agent passes to `--model`; "model" is the name sent on the
# wire. They differ whenever the pool names its own deployments.
MODELS: list[dict[str, str]] = [
    {
        "id": "glm-4.7-flash",
        "model": "glm-4.7-flash",
        "family": "glm",
        "label": "GLM-4.7-Flash (free tier, ~1 request at a time)",
    },
    {
        "id": "mercury-2",
        "model": "mercury-2",
        "family": "mercury",
        "label": "Mercury 2 (fast diffusion, paid)",
    },
    # A pool can expose hundreds of model ids. Rather than mirroring the whole
    # catalogue into this registry, a few representatives are listed and
    # OPENAI_COMPAT_MODEL pins any other id on the same endpoint. `auto` leads
    # because the pool picks a model per request, which is the right default
    # when the catalogue is too large to reason about.
    {
        "id": "pool-auto",
        "model": "auto",
        "family": "openai_compat",
        "label": "Model pool: endpoint-chosen (OPENAI_COMPAT_MODEL overrides)",
    },
    {
        "id": "pool-auto-code",
        "model": "auto-code",
        "family": "openai_compat",
        "label": "Model pool: coder-tuned auto route",
    },
]

BY_ID = {spec["id"]: spec for spec in MODELS}

ALIASES: dict[str, str] = {
    "auto": "auto",
    "glm": "glm-4.7-flash",
    "zai": "glm-4.7-flash",
    "z.ai": "glm-4.7-flash",
    "flash": "glm-4.7-flash",
    "glm-flash": "glm-4.7-flash",
    "glm-4.7-flash": "glm-4.7-flash",
    "mercury": "mercury-2",
    "mercury2": "mercury-2",
    "mercury-2": "mercury-2",
    "inception": "mercury-2",
    "compat": "pool-auto",
    "pool": "pool-auto",
    "auto-code": "pool-auto-code",
    "pool-auto-code": "pool-auto-code",
    "openai": "pool-auto",
    "openai-compat": "pool-auto",
    "tokenmaxxing": "pool-auto",
    "tokenmax": "pool-auto",
}

DEFAULT_MODEL = "glm-4.7-flash"


def load_dotenv(path: Path | None = None) -> Path | None:
    candidates = []
    if path is not None:
        candidates.append(path)
    candidates.append(Path.cwd() / ".env")
    candidates.append(Path(__file__).resolve().parents[1] / ".env")
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        for line in resolved.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
        return resolved
    return None


def resolve_model(name: str | None = None) -> str:
    """Map a user alias to a canonical model id, or 'auto'."""
    load_dotenv()
    raw = (name or os.environ.get("LLM_MODEL") or DEFAULT_MODEL).strip().lower()
    if raw in ALIASES:
        return ALIASES[raw]
    if raw in BY_ID:
        return raw
    known = ", ".join(sorted({*ALIASES, "auto"}))
    raise ValueError(f"unknown model {name!r}. Try one of: {known}")


def _override_model(family: str, registry_id: str, wire_model: str) -> str:
    """Let <FAMILY>_MODEL pin a specific deployment name.

    Only the family's default entry is overridden, so the other listed entries
    keep their own wire names and stay usable as distinct choices. The
    comparison is on the registry id, not the wire name, because a pool entry's
    id and its deployment name differ.
    """
    override = os.environ.get(f"{family.upper()}_MODEL")
    if not override:
        return wire_model
    default_for_family = next(spec["id"] for spec in MODELS if spec["family"] == family)
    return override if registry_id == default_for_family else wire_model


def llm_config(name: str | None = None) -> dict[str, str]:
    """Config for one concrete model. 'auto' resolves to the first usable one."""
    load_dotenv()
    model_id = resolve_model(name)
    if model_id == "auto":
        chain = fallback_chain("auto")
        if not chain:
            envs = " or ".join(spec["api_key_env"] for spec in FAMILIES.values())
            raise ValueError(f"No LLM API key found. Set {envs} in .env.")
        return chain[0]

    spec = BY_ID[model_id]
    family = FAMILIES[spec["family"]]
    api_key = os.environ.get(family["api_key_env"]) or os.environ.get("LLM_API_KEY") or ""
    base_url = os.environ.get(family["base_url_env"]) or family["base_url"]
    deployment = _override_model(spec["family"], model_id, spec.get("model") or model_id)
    if spec["family"] == "openai_compat" and not base_url:
        raise ValueError(
            "openai_compat needs OPENAI_COMPAT_BASE_URL: a key alone does not "
            "identify the provider"
        )
    return {
        "provider": spec["family"],
        "vendor": family["vendor"],
        "label": spec["label"],
        "model": deployment,
        "api_key": api_key,
        "api_key_env": family["api_key_env"],
        "base_url": base_url.rstrip("/"),
    }


def fallback_chain(name: str | None = None) -> list[dict[str, str]]:
    """Models to try in order. Only those usable without extra configuration."""
    model_id = resolve_model(name)
    if model_id != "auto":
        try:
            cfg = llm_config(model_id)
        except ValueError:
            return []
        return [cfg] if cfg["api_key"] else []
    chain: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for spec in MODELS:
        try:
            cfg = llm_config(spec["id"])
        except ValueError:
            continue
        if not cfg["api_key"]:
            continue
        # Two registry entries can resolve to one deployment, e.g. when
        # OPENAI_COMPAT_MODEL pins the family default onto the same wire name
        # as another entry. A retry then re-sends the identical request.
        key = (cfg["base_url"], cfg["model"])
        if key in seen:
            continue
        seen.add(key)
        chain.append(cfg)
    return chain


def available_models() -> list[dict[str, object]]:
    """Registry plus whether each model has a key configured."""
    out: list[dict[str, object]] = []
    for spec in MODELS:
        try:
            cfg = llm_config(spec["id"])
        except ValueError as exc:
            out.append(
                {
                    "id": spec["id"],
                    "label": spec["label"],
                    "vendor": FAMILIES[spec["family"]]["vendor"],
                    "provider": spec["family"],
                    "base_url": "",
                    "configured": False,
                    "api_key_env": FAMILIES[spec["family"]]["api_key_env"],
                    "note": str(exc),
                }
            )
            continue
        out.append(
            {
                "id": spec["id"],
                "label": cfg["label"],
                "vendor": cfg["vendor"],
                "provider": cfg["provider"],
                "base_url": cfg["base_url"],
                "configured": bool(cfg["api_key"]),
                "api_key_env": cfg["api_key_env"],
            }
        )
    return out
