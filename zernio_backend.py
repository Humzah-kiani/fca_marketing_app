"""
zernio_backend.py  -  publish through Zernio instead of Meta/LinkedIn developer apps.
Put this file next to direct_social_publisher.py.

Secrets (.streamlit/secrets.toml):
    ZERNIO_API_KEY = "sk_..."
    # optional, only if you have 2+ accounts of the same platform:
    ZERNIO_ACCOUNT_INSTAGRAM = "acc_..."
    ZERNIO_ACCOUNT_FACEBOOK  = "acc_..."
    ZERNIO_ACCOUNT_LINKEDIN  = "acc_..."
"""
from __future__ import annotations

from typing import Any

import requests

BASE = "https://zernio.com/api/v1"
PLATFORM_MAP = {"Instagram": "instagram", "Facebook Page": "facebook", "LinkedIn": "linkedin"}


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _check(r: requests.Response) -> Any:
    if not r.ok:
        raise RuntimeError(f"Zernio HTTP {r.status_code}: {r.text[:1200]}")
    try:
        return r.json()
    except Exception:
        return {"raw": r.text[:500]}


def list_accounts(key: str) -> list[dict[str, Any]]:
    """Returns the accounts you connected in the Zernio dashboard."""
    data = _check(requests.get(f"{BASE}/accounts", headers=_headers(key), timeout=30))
    if isinstance(data, dict):
        data = data.get("accounts") or data.get("data") or []
    return [a for a in data if isinstance(a, dict)]


def _account_id(a: dict[str, Any]) -> str:
    return str(a.get("_id") or a.get("id") or a.get("accountId") or "")


def find_account(key: str, platform: str, secrets: dict[str, Any]) -> str:
    z = PLATFORM_MAP[platform]
    pinned = secrets.get(f"ZERNIO_ACCOUNT_{z.upper()}")
    if pinned:
        return str(pinned)
    matches = [a for a in list_accounts(key) if str(a.get("platform", "")).lower() == z]
    if not matches:
        raise RuntimeError(f"No {platform} account connected in Zernio. Connect it in the dashboard.")
    return _account_id(matches[0])


def test_zernio(key: str) -> str:
    accts = list_accounts(key)
    if not accts:
        return "Key works, but no accounts are connected yet."
    names = [f"{a.get('platform')}:{a.get('username') or a.get('name') or _account_id(a)}" for a in accts]
    return "OK: " + ", ".join(names)


def publish_zernio(key: str, platform: str, fmt: str, media: list[dict[str, str]],
                   caption: str, secrets: dict[str, Any] | None = None) -> dict[str, Any]:
    secrets = secrets or {}
    z = PLATFORM_MAP[platform]
    entry: dict[str, Any] = {"platform": z, "accountId": find_account(key, platform, secrets)}

    # NOTE: story flag name comes from Zernio's platform-specific settings.
    # If stories fail, check docs.zernio.com for the exact field and adjust here.
    if fmt == "Story":
        entry["platformSpecificData"] = {"contentType": "story"}

    body: dict[str, Any] = {
        "content": caption,
        "platforms": [entry],
        "mediaItems": [{"type": m["type"], "url": m["url"]} for m in media],
        "publishNow": True,   # your local queue decides WHEN; Zernio just posts
    }
    return _check(requests.post(f"{BASE}/posts", headers=_headers(key), json=body, timeout=180))
