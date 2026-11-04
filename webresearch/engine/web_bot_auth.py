"""web_bot_auth.py — RFC 9421 HTTP Message Signatures for Agent Identity.

Implements cryptographic request signing for authorized web bots and AI agents.
Allows dosh-browser to prove its authentic identity to web servers and CDN anti-bot
gates (Cloudflare, Fastly, Akamai) via standard Ed25519 or HMAC-SHA256 signatures,
preventing unnecessary CAPTCHAs and IP bans.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from urllib.parse import urlparse


class BotAuthConfig:
    def __init__(
        self,
        key_id: str,
        agent_identifier: str,
        secret_key: bytes,
        algorithm: str = "hmac-sha256",
    ) -> None:
        self.key_id = key_id
        self.agent_identifier = agent_identifier
        self.secret_key = secret_key
        self.algorithm = algorithm.lower()


def sign_request(
    config: BotAuthConfig,
    method: str,
    target_uri: str,
    authority: str | None = None,
    timestamp: int | None = None,
) -> dict[str, str]:
    """Generate RFC 9421 signed headers dictionary."""
    if authority is None:
        parsed = urlparse(target_uri)
        authority = parsed.netloc

    now = timestamp if timestamp is not None else int(time.time())
    label = "sig1"

    # Signature-Input header
    sig_input = (
        f'{label}=("@method" "@target-uri" "@authority" "signature-agent");'
        f'created={now};keyid="{config.key_id}";alg="{config.algorithm}"'
    )

    # Signature base string per RFC 9421 Section 2.5
    params = sig_input[len(label) + 1 :]
    base_string = (
        f'"@method": {method.upper()}\n'
        f'"@target-uri": {target_uri}\n'
        f'"@authority": {authority}\n'
        f'"signature-agent": {config.agent_identifier}\n'
        f'"@signature-params": {params}'
    )

    if config.algorithm == "hmac-sha256":
        mac = hmac.new(config.secret_key, base_string.encode("utf-8"), hashlib.sha256).digest()
        sig_val = f"{label}=:{base64.b64encode(mac).decode('ascii')}:"
    elif config.algorithm == "ed25519":
        try:
            from cryptography.hazmat.primitives.asymmetric import ed25519
            priv = ed25519.Ed25519PrivateKey.from_private_bytes(config.secret_key[:32])
            sig_bytes = priv.sign(base_string.encode("utf-8"))
            sig_val = f"{label}=:{base64.b64encode(sig_bytes).decode('ascii')}:"
        except ImportError:
            # Fallback to HMAC-SHA256 if cryptography not installed
            mac = hmac.new(config.secret_key, base_string.encode("utf-8"), hashlib.sha256).digest()
            sig_val = f"{label}=:{base64.b64encode(mac).decode('ascii')}:"
    else:
        raise ValueError(f"Unsupported signature algorithm: {config.algorithm}")

    return {
        "signature-agent": config.agent_identifier,
        "signature-input": sig_input,
        "signature": sig_val,
    }


def sign_web_bot_auth(
    config: BotAuthConfig,
    authority: str,
    timestamp: int | None = None,
) -> dict[str, str]:
    """Generate Cloudflare WebBotAuth convention signed headers:
    ("@authority" "signature-agent");created=...;expires=...;keyid="...";alg="ed25519";nonce="...";tag="web-bot-auth"
    """
    import secrets

    now = timestamp if timestamp is not None else int(time.time())
    expires = now + 60
    nonce = base64.b64encode(secrets.token_bytes(64)).decode("ascii")

    clean_agent = config.agent_identifier.strip('"')
    agent_hdr = f'"https://{clean_agent}"'
    label = "sig1"

    sig_input_val = (
        f'("@authority" "signature-agent");created={now};expires={expires};'
        f'keyid="{config.key_id}";alg="ed25519";nonce="{nonce}";tag="web-bot-auth"'
    )
    sig_input = f"{label}={sig_input_val}"

    base_string = (
        f'"@authority": {authority}\n'
        f'"signature-agent": {agent_hdr}\n'
        f'"@signature-params": {sig_input_val}'
    )

    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
        priv = ed25519.Ed25519PrivateKey.from_private_bytes(config.secret_key[:32])
        sig_bytes = priv.sign(base_string.encode("utf-8"))
        sig_val = f"{label}=:{base64.b64encode(sig_bytes).decode('ascii')}:"
    except ImportError:
        mac = hmac.new(config.secret_key, base_string.encode("utf-8"), hashlib.sha256).digest()
        sig_val = f"{label}=:{base64.b64encode(mac).decode('ascii')}:"

    return {
        "signature-agent": agent_hdr,
        "signature-input": sig_input,
        "signature": sig_val,
    }

