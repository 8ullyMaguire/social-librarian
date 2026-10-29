"""Adapter registry: one call site maps an account to its platform client."""

from __future__ import annotations

from ..bridge import Socialctl
from ..config import Account
from .base import Adapter, Item, Metrics, age_days, now_iso, parse_iso
from .bluesky import Bluesky
from .lemmy import Lemmy
from .mastodon import Mastodon

__all__ = ["Adapter", "Item", "Metrics", "age_days", "now_iso", "parse_iso", "make"]


def make(account: Account, env: dict[str, str], **kwargs) -> Adapter:
    identifier, secret = account.secret(env)
    if not secret:
        raise RuntimeError(
            f"{account.key}: no credential in the env file "
            f"(expected {_secret_name(account)})"
        )
    bridge = Socialctl.from_env(account, env)
    if account.platform == "bluesky":
        adapter: Adapter = Bluesky(account, identifier, secret, **kwargs)
    elif account.platform == "mastodon":
        adapter = Mastodon(account, identifier, secret, **kwargs)
    elif account.platform == "lemmy":
        adapter = Lemmy(account, identifier, secret, **kwargs)
    else:
        raise RuntimeError(f"no adapter for platform {account.platform}")
    adapter.bridge = bridge
    return adapter


def _secret_name(account: Account) -> str:
    inst = account.instance.upper().replace(".", "_").replace("-", "_")
    return {
        "bluesky": "BSKY_APP_PASSWORD",
        "mastodon": f"MASTODON_{inst}_TOKEN",
        "lemmy": f"LEMMY_{inst}_PASSWORD",
    }.get(account.platform, "a credential")
