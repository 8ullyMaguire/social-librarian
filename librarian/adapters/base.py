"""The shape every platform adapter speaks."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    """Parse the several ISO-8601 dialects these APIs emit, always returning UTC."""
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    # Bluesky emits microseconds; Lemmy emits none; Mastodon emits Z with milliseconds.
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        dt = datetime.strptime(v[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def age_days(created_at: str, now: datetime | None = None) -> float:
    ref = now or datetime.now(timezone.utc)
    return (ref - parse_iso(created_at)).total_seconds() / 86400.0


@dataclass
class Metrics:
    likes: int = 0
    boosts: int = 0
    replies: int = 0
    comments: int = 0
    score: float = 0.0


@dataclass
class Item:
    platform: str
    instance: str
    account: str
    remote_id: str
    remote_uri: str
    kind: str
    text: str
    url: str | None
    created_at: str
    metrics: Metrics = field(default_factory=Metrics)
    subject_uri: str | None = None
    subject_author: str | None = None


class Adapter:
    """Every method may raise; the caller isolates failures per account."""

    platform = "base"

    def __init__(self, account, identifier: str, secret: str):
        self.account = account
        self.identifier = identifier
        self.secret = secret
        # Set by the registry when the Rust `socialite` transport is configured.
        self.bridge = None

    def whoami(self) -> dict:
        raise NotImplementedError

    def list_own(self, since: str | None = None) -> list[Item]:
        raise NotImplementedError

    def metrics(self, items: list[Item]) -> dict[str, Metrics]:
        """Return {remote_uri: Metrics}. Adapters may return a subset."""
        raise NotImplementedError

    def delete(self, item) -> None:
        raise NotImplementedError

    def post_status(self, text: str) -> str:
        raise NotImplementedError(f"{self.platform} cannot post statuses")
