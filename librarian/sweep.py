"""The expiry policy: a per-account rank, not a global threshold.

Pure functions first (`engagement`, `classify`, `due_reason`) so the policy can be tested against
real numbers without a network, then `run`, which applies it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import archive
from .adapters import make
from .adapters.base import Metrics, age_days, now_iso
from .config import Account, Config, Weights
from .store import Row, Store


def engagement(m: Metrics, w: Weights) -> float:
    return (
        m.likes * w.like
        + m.boosts * w.boost
        + m.replies * w.reply
        + m.comments * w.comment
        + m.score * w.score
    )


def classify(rows: list[Row], quantile: float, w: Weights) -> dict[str, str]:
    """Rank by engagement; the bottom `quantile` are 'low'.

    Ties break on `created_at` ascending, so an account whose posts all have zero engagement still
    gets a deterministic 80/20 split rather than an arbitrary one.
    """
    if not rows:
        return {}
    # Deterministic even when every item has the same score and the same timestamp: remote_uri is
    # the last tiebreaker, so two runs over the same set cannot disagree about who is "low".
    scored = sorted(
        rows,
        key=lambda r: (
            -engagement(Metrics(r.likes, r.boosts, r.replies, r.comments, r.score), w),
            r.created_at,
            r.remote_uri,
        ),
    )
    n = len(scored)
    high = math.ceil(n * (1 - quantile))
    if quantile >= 1.0:
        high = 0
    out: dict[str, str] = {}
    for i, r in enumerate(scored):
        out[r.remote_uri] = "high" if i < high else "low"
    return out


def due_reason(row: Row, tier: str | None, account: Account, now: datetime | None = None) -> str | None:
    """Why this item should be deleted now, or None to keep it."""
    age = age_days(row.created_at, now)
    if age < account.min_age_days:
        return None
    if tier == "low" and age >= account.low_engagement_ttl_days:
        return f"low tier, {age:.0f}d >= {account.low_engagement_ttl_days}d"
    if age >= account.ttl_days:
        return f"age {age:.0f}d >= {account.ttl_days}d"
    return None


@dataclass
class AccountReport:
    key: str
    synced: int = 0
    live: int = 0
    low: int = 0
    high: int = 0
    unclassified: int = 0
    due: int = 0
    deleted: int = 0
    capped: bool = False
    errors: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)


@dataclass
class Report:
    accounts: list[AccountReport] = field(default_factory=list)
    dry_run: bool = True

    @property
    def deleted(self) -> int:
        return sum(a.deleted for a in self.accounts)

    def render(self) -> str:
        out = []
        for a in self.accounts:
            state = "DRY" if self.dry_run else "APPLIED"
            out.append(
                f"{a.key:<28} {state:<8} synced={a.synced} live={a.live} "
                f"low={a.low} high={a.high} unclassified={a.unclassified} "
                f"due={a.due} deleted={a.deleted}" + (" CAPPED" if a.capped else "")
            )
            for line in a.lines:
                out.append("    " + line)
            for err in a.errors:
                out.append("    ! " + err)
        return "\n".join(out)


def run(
    cfg: Config,
    store: Store,
    env: dict[str, str],
    apply: bool,
    only: str | None = None,
    verbose: bool = False,
    state_dir: Path | None = None,
    run_id: int | None = None,
) -> Report:
    report = Report(dry_run=not apply)
    now_dt = datetime.now(timezone.utc)
    now = now_iso()
    archive_root = (state_dir or Path.home() / ".local/share/social-librarian") / "archive"

    for account in cfg.enabled_accounts(only):
        rep = AccountReport(key=account.key)
        try:
            adapter = make(account, env)
            # Past this date everything is due on the long TTL, so paging further is wasted work.
            horizon_days = account.ttl_days + account.quantile_min_age_days
            horizon = (now_dt - timedelta(days=horizon_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
            items = adapter.list_own(None, older_than=horizon)
        except Exception as e:  # noqa: BLE001 - one account failing must not stop the others
            rep.errors.append(f"sync failed: {e}")
            report.accounts.append(rep)
            continue

        # 1. Persist what exists, so metrics and text stay current.
        for it in items:
            store.upsert(Row(
                platform=it.platform, instance=it.instance, account=it.account,
                remote_id=it.remote_id, remote_uri=it.remote_uri, kind=it.kind,
                text=it.text, url=it.url, created_at=it.created_at,
                likes=it.metrics.likes, boosts=it.metrics.boosts, replies=it.metrics.replies,
                comments=it.metrics.comments, score=it.metrics.score, metrics_at=now,
            ))
        rep.synced = len(items)

        rows = [r for r in store.items(account.platform, account.instance, state="live")]
        # Engagement decays slowly after the first month; refreshing it weekly keeps a full crawl
        # from turning into thousands of API calls every night.
        stale = [r for r in rows if r.metrics_at is None or age_days(r.metrics_at, now_dt) >= 7]
        stale_uris = {r.remote_uri for r in stale}
        try:
            fresh = adapter.metrics([i for i in items if i.remote_uri in stale_uris])
        except Exception as e:  # noqa: BLE001
            rep.errors.append(f"metrics refresh failed: {e}")
            fresh = {}
        for uri, m in fresh.items():
            row = store.by_uri(uri)
            if row is None:
                continue
            row.likes, row.boosts, row.replies = m.likes, m.boosts, m.replies
            row.comments, row.score = m.comments, m.score
            row.metrics_at = now
            store.set_metrics(row)

        # 2. Classify everything old enough to have earned an audience.
        rows = store.items(account.platform, account.instance, state="live")
        eligible = [r for r in rows if age_days(r.created_at, now_dt) >= account.quantile_min_age_days]
        tiers = classify(eligible, account.low_engagement_quantile, account.weights)
        for r in rows:
            tier = tiers.get(r.remote_uri)
            if tier != r.tier:
                store.set_tier(r.remote_uri, tier, r.reason)
            r.tier = tier
        rep.live = len(rows)
        rep.low = sum(1 for r in rows if r.tier == "low")
        rep.high = sum(1 for r in rows if r.tier == "high")
        rep.unclassified = sum(1 for r in rows if r.tier is None)

        # 3. Decide, oldest first, capped.
        due: list[tuple[Row, str]] = []
        for r in sorted(rows, key=lambda x: x.created_at):
            reason = due_reason(r, r.tier, account, now_dt)
            if reason:
                due.append((r, reason))
        rep.due = len(due)
        if len(due) > account.max_deletes_per_run:
            due = due[: account.max_deletes_per_run]
            rep.capped = True

        # 4. Archive, then delete.
        for r, reason in due:
            if verbose or not apply:
                rep.lines.append(
                    f"{r.created_at[:10]} {r.platform}:{r.remote_id} {r.tier or '-':<4} "
                    f"age={age_days(r.created_at, now_dt):.0f}d "
                    f"like={r.likes} boost={r.boosts} reply={r.replies} "
                    f"-> {reason}"
                )
            if not apply:
                continue
            try:
                path = archive.write(archive_root, r, r.mirrored_to, now)
            except Exception as e:  # noqa: BLE001 - archive failure forbids deletion
                store.mark_failed(r.remote_uri, f"archive failed: {e}")
                rep.errors.append(f"{r.remote_id}: archive failed: {e}")
                continue
            try:
                adapter.delete(r)
            except Exception as e:  # noqa: BLE001
                store.mark_failed(r.remote_uri, f"delete failed: {e}")
                rep.errors.append(f"{r.remote_id}: delete failed: {e}")
                continue
            store.mark_deleted(r.remote_uri, str(path), now)
            rep.deleted += 1

        report.accounts.append(rep)

    if run_id is not None:
        store.run_finish(run_id, now_iso(), report.render())
    return report
