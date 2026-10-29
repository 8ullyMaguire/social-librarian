"""Configuration: one TOML file, every threshold overridable per platform *and* per instance."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

PLATFORMS = ("bluesky", "mastodon", "lemmy")


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Weights:
    like: float = 1.0
    boost: float = 2.0
    reply: float = 2.0
    comment: float = 2.0
    score: float = 1.0


@dataclass(frozen=True)
class Defaults:
    ttl_days: int = 365
    low_engagement_ttl_days: int = 30
    low_engagement_quantile: float = 0.8
    quantile_min_age_days: int = 30
    min_age_days: int = 1
    max_deletes_per_run: int = 25
    weights: Weights = field(default_factory=Weights)


@dataclass(frozen=True)
class Account:
    platform: str
    instance: str
    handle: str
    ttl_days: int
    low_engagement_ttl_days: int
    low_engagement_quantile: float
    quantile_min_age_days: int
    min_age_days: int
    max_deletes_per_run: int
    weights: Weights
    enabled: bool = True

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.instance}"

    def secret(self, env: dict[str, str]) -> tuple[str, str]:
        """(identifier, password/token) for this account, from the env file."""
        inst = self.instance.upper().replace(".", "_").replace("-", "_")
        if self.platform == "bluesky":
            return env.get("BSKY_IDENTIFIER", self.handle), env.get("BSKY_APP_PASSWORD", "")
        if self.platform == "mastodon":
            return self.handle, env.get(f"MASTODON_{inst}_TOKEN", "")
        if self.platform == "lemmy":
            return self.handle, env.get(f"LEMMY_{inst}_PASSWORD", "")
        return self.handle, ""


@dataclass(frozen=True)
class Mirror:
    source: str | None = None
    targets: tuple[str, ...] = ()
    enabled: bool = True
    max_chars: int = 500
    ignore_handles: tuple[str, ...] = ()


@dataclass(frozen=True)
class Config:
    defaults: Defaults
    accounts: tuple[Account, ...]
    mirror: Mirror
    path: Path

    def account(self, key: str) -> Account:
        for a in self.accounts:
            if a.key == key:
                return a
        raise ConfigError(f"no account configured for {key}")

    def enabled_accounts(self, only: str | None = None) -> list[Account]:
        rows = [a for a in self.accounts if a.enabled]
        if only:
            rows = [a for a in rows if a.key == only]
        return rows


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def _num(section: dict, key: str, default, cast):
    value = section.get(key, default)
    try:
        return cast(value)
    except (TypeError, ValueError) as e:
        raise ConfigError(f"{key} must be a number, got {value!r}") from e


def _account(raw: dict, defaults: Defaults) -> Account:
    platform = str(raw.get("platform", "")).lower()
    if platform not in PLATFORMS:
        raise ConfigError(f"unknown platform {platform!r}; expected one of {', '.join(PLATFORMS)}")
    instance = str(raw.get("instance", "")).strip()
    handle = str(raw.get("handle", "")).strip()
    if not instance or not handle:
        raise ConfigError(f"account for {platform} needs both instance and handle")
    w = raw.get("engagement_weights") or {}
    weights = Weights(
        like=float(w.get("like", defaults.weights.like)),
        boost=float(w.get("boost", defaults.weights.boost)),
        reply=float(w.get("reply", defaults.weights.reply)),
        comment=float(w.get("comment", defaults.weights.comment)),
        score=float(w.get("score", defaults.weights.score)),
    )
    return Account(
        platform=platform,
        instance=instance,
        handle=handle,
        ttl_days=_num(raw, "ttl_days", defaults.ttl_days, int),
        low_engagement_ttl_days=_num(raw, "low_engagement_ttl_days", defaults.low_engagement_ttl_days, int),
        low_engagement_quantile=_num(raw, "low_engagement_quantile", defaults.low_engagement_quantile, float),
        quantile_min_age_days=_num(raw, "quantile_min_age_days", defaults.quantile_min_age_days, int),
        min_age_days=_num(raw, "min_age_days", defaults.min_age_days, int),
        max_deletes_per_run=_num(raw, "max_deletes_per_run", defaults.max_deletes_per_run, int),
        weights=weights,
        enabled=bool(raw.get("enabled", True)),
    )


def parse(raw: dict, path: Path) -> Config:
    d = raw.get("defaults", {})
    w = d.get("engagement_weights", {})
    defaults = Defaults(
        ttl_days=_num(d, "ttl_days", 365, int),
        low_engagement_ttl_days=_num(d, "low_engagement_ttl_days", 30, int),
        low_engagement_quantile=_num(d, "low_engagement_quantile", 0.8, float),
        quantile_min_age_days=_num(d, "quantile_min_age_days", 30, int),
        min_age_days=_num(d, "min_age_days", 1, int),
        max_deletes_per_run=_num(d, "max_deletes_per_run", 25, int),
        weights=Weights(
            like=float(w.get("like", 1.0)),
            boost=float(w.get("boost", 2.0)),
            reply=float(w.get("reply", 2.0)),
            comment=float(w.get("comment", 2.0)),
            score=float(w.get("score", 1.0)),
        ),
    )
    if not 0 < defaults.low_engagement_quantile <= 1:
        raise ConfigError("low_engagement_quantile must be in (0, 1]")
    if defaults.ttl_days < 1 or defaults.low_engagement_ttl_days < 1:
        raise ConfigError("ttl_days and low_engagement_ttl_days must be at least 1")
    if defaults.low_engagement_ttl_days > defaults.ttl_days:
        raise ConfigError("low_engagement_ttl_days cannot exceed ttl_days")

    accounts = tuple(_account(a, defaults) for a in raw.get("account", []))
    if not accounts:
        raise ConfigError("no [[account]] blocks: nothing to do")

    for a in accounts:
        if not 0 < a.low_engagement_quantile <= 1:
            raise ConfigError(f"{a.key}: low_engagement_quantile must be in (0, 1]")
        if a.low_engagement_ttl_days > a.ttl_days:
            raise ConfigError(f"{a.key}: low_engagement_ttl_days cannot exceed ttl_days")

    m = raw.get("mirror", {})
    source = m.get("from")
    targets = tuple(m.get("to", []))
    keys = {a.key for a in accounts}
    if source and source not in keys:
        raise ConfigError(f"[mirror] from = {source!r} is not a configured account")
    for t in targets:
        if t not in keys:
            raise ConfigError(f"[mirror] to includes {t!r}, which is not a configured account")
    mirror = Mirror(
        source=source,
        targets=targets,
        enabled=bool(m.get("enabled", True)),
        max_chars=int(m.get("max_chars", 500)),
        ignore_handles=tuple(m.get("ignore_handles", [])),
    )
    return Config(defaults=defaults, accounts=accounts, mirror=mirror, path=path)


def load(path: Path, override_env: dict[str, str] | None = None) -> Config:
    if not path.exists():
        raise ConfigError(f"no config at {path}")
    raw = tomllib.loads(path.read_text())
    cfg = parse(raw, path)
    # Keep the environment for callers that want it; secrets never enter the Config object.
    if override_env is not None:
        override_env.update(load_env(path.parent / "env"))
    return cfg
