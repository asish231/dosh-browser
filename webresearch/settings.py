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
}

# Ordered: "auto" walks this list and uses the first model with a key that answers.
# Only GLM-4.7-Flash is enabled on z.ai; the paid GLM tiers return 1113 (no balance).
MODELS: list[dict[str, str]] = [
    {
        "id": "glm-4.7-flash",
        "family": "glm",
        "label": "GLM-4.7-Flash (free tier, ~1 request at a time)",
    },
    {
        "id": "mercury-2",
        "family": "mercury",
        "label": "Mercury 2 (fast diffusion, paid)",
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


def _override_model(family: str, model_id: str) -> str:
    """Let ZAI_MODEL / INCEPTION_MODEL pin a specific deployment name."""
    env_name = "ZAI_MODEL" if family == "glm" else "INCEPTION_MODEL"
    override = os.environ.get(env_name)
    if not override:
        return model_id
    # Only apply the override to the family's default entry.
    default_for_family = next(spec["id"] for spec in MODELS if spec["family"] == family)
    return override if model_id == default_for_family else model_id


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
    return {
        "provider": spec["family"],
        "vendor": family["vendor"],
        "label": spec["label"],
        "model": _override_model(spec["family"], model_id),
        "api_key": api_key,
        "api_key_env": family["api_key_env"],
        "base_url": base_url.rstrip("/"),
    }


def fallback_chain(name: str | None = None) -> list[dict[str, str]]:
    """Models to try in order. Only those with an API key configured."""
    model_id = resolve_model(name)
    if model_id != "auto":
        cfg = llm_config(model_id)
        return [cfg] if cfg["api_key"] else []
    chain: list[dict[str, str]] = []
    for spec in MODELS:
        cfg = llm_config(spec["id"])
        if cfg["api_key"]:
            chain.append(cfg)
    return chain


def available_models() -> list[dict[str, object]]:
    """Registry plus whether each model has a key configured."""
    out: list[dict[str, object]] = []
    for spec in MODELS:
        cfg = llm_config(spec["id"])
        out.append(
            {
                "id": spec["id"],
                "label": spec["label"],
                "vendor": cfg["vendor"],
                "provider": cfg["provider"],
                "base_url": cfg["base_url"],
                "configured": bool(cfg["api_key"]),
                "api_key_env": cfg["api_key_env"],
            }
        )
    return out
