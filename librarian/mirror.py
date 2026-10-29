"""Mirror: Bluesky posts and reposts → Mastodon, exactly once each."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .adapters import make
from .adapters.base import Item, now_iso
from .config import Config
from .store import Row, Store


def compose(item: Item, max_chars: int) -> str:
    """Mastodon text for a Bluesky item. Reposts are attributed, never passed off as original."""
    body = (item.text or "").strip()
    if not body:
        body = item.url or ""
    if item.kind == "repost":
        via = f" (via @{item.subject_author})" if item.subject_author else ""
        text = f"🔁 {body}{via}\n\n{item.url}"
    else:
        link = item.url or ""
        text = f"{body}\n\n{link}" if link and link not in body else body
    if len(text) > max_chars:
        keep = max_chars - len(text) + len(body)
        text = body[: max(0, keep - 2)].rstrip() + "…\n\n" + (item.url or "")
        text = text[:max_chars]
    return text.strip()


@dataclass
class MirrorReport:
    source: str
    new: int = 0
    mirrored: int = 0
    skipped: int = 0
    lines: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dry_run: bool = True

    def render(self) -> str:
        head = (
            f"mirror {self.source}: new={self.new} mirrored={self.mirrored} "
            f"skipped={self.skipped} ({'DRY' if self.dry_run else 'APPLIED'})"
        )
        return "\n".join([head] + self.lines + ["! " + e for e in self.errors])


def run(
    cfg: Config,
    store: Store,
    env: dict[str, str],
    apply: bool,
    limit: int = 50,
    backfill: bool = False,
    verbose: bool = False,
) -> MirrorReport:
    src_key = cfg.mirror.source
    if not cfg.mirror.enabled or not src_key or not cfg.mirror.targets:
        return MirrorReport(source=src_key or "none", dry_run=not apply)

    report = MirrorReport(source=src_key, dry_run=not apply)
    source = make(cfg.account(src_key), env)
    targets = [make(cfg.account(k), env) for k in cfg.mirror.targets]
    if not targets:
        return report

    watermark = store.state_get("mirror:since")
    if watermark is None:
        # First run: start now, so enabling the mirror does not repost an archive.
        watermark = now_iso() if not backfill else "1970-01-01T00:00:00Z"
        store.state_set("mirror:since", watermark)

    items = source.list_own(watermark)
    items = [i for i in items if i.created_at > watermark or backfill]
    items = [i for i in items if i.account not in cfg.mirror.ignore_handles][:limit]
    report.new = len(items)

    newest = watermark
    for item in items:
        existing = store.by_uri(item.remote_uri)
        if existing is not None and existing.mirrored_to:
            report.skipped += 1
            continue
        text = compose(item, cfg.mirror.max_chars)
        if not text:
            report.skipped += 1
            continue
        if verbose or not apply:
            report.lines.append(f"{item.created_at} {item.kind} {item.remote_id} → {text[:80]!r}")
        if not apply:
            continue
        posted = None
        for target in targets:
            try:
                url = target.post_status(text)
            except Exception as e:  # noqa: BLE001 - one target failing must not lose the item
                report.errors.append(f"{target.account.key}: {e}")
                continue
            if not url:
                continue
            posted = url
            # Record the mirror itself, so expiry can delete it with the original.
            mirror_id = url.rstrip("/").split("/")[-1] or url
            store.upsert(Row(
                platform=target.account.platform, instance=target.account.instance,
                account=target.account.handle, remote_id=mirror_id,
                remote_uri=url, kind="post", text=text, url=url,
                created_at=now_iso(), mirrored_from=item.remote_uri,
            ))
        if posted is None:
            continue
        store.upsert(Row(
            platform=item.platform, instance=item.instance, account=item.account,
            remote_id=item.remote_id, remote_uri=item.remote_uri, kind=item.kind,
            text=item.text, url=item.url, created_at=item.created_at,
            likes=item.metrics.likes, boosts=item.metrics.boosts, replies=item.metrics.replies,
            mirrored_to=posted,
        ))
        report.mirrored += 1
        if item.created_at > newest:
            newest = item.created_at

    if apply and newest > watermark:
        store.state_set("mirror:since", newest)
    return report
