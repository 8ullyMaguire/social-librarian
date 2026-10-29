"""Command line: the three verbs a human needs, plus two for operators."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, archive, mirror as mirror_mod, sweep as sweep_mod
from .adapters import make, now_iso
from .config import Config, ConfigError, load
from .store import Store

DEFAULT_CONFIG = Path.home() / ".config/social-librarian/config.toml"
DEFAULT_STATE = Path.home() / ".local/share/social-librarian"


def _cfg(args) -> tuple[Config, dict[str, str]]:
    path = Path(args.config).expanduser()
    env: dict[str, str] = {}
    cfg = load(path, env)
    return cfg, env


def _store(args) -> Store:
    state = Path(args.state_dir).expanduser() if args.state_dir else DEFAULT_STATE
    return Store(state / "ledger.db")


def cmd_check(args) -> int:
    cfg, env = _cfg(args)
    failures = 0
    print(f"config {cfg.path}")
    print(f"accounts {len(cfg.accounts)} | mirror {cfg.mirror.source or 'off'} → "
          f"{', '.join(cfg.mirror.targets) if cfg.mirror.targets else 'none'}")
    for a in cfg.accounts:
        ident, secret = a.secret(env)
        if not secret:
            print(f"  {a.key:<28} NO CREDENTIAL ({a.platform})")
            failures += 1
            continue
        if args.parse_only:
            print(f"  {a.key:<28} parsed (network skipped)")
            continue
        try:
            who = make(a, env).whoami()
            print(f"  {a.key:<28} ok {who}")
        except Exception as e:  # noqa: BLE001
            print(f"  {a.key:<28} FAILED {e}")
            failures += 1
    store = _store(args)
    print(f"ledger {store.path} ({sum(store.counts(a.platform, a.instance).values())} items)")
    store.close()
    return 1 if failures else 0


def cmd_mirror(args) -> int:
    cfg, env = _cfg(args)
    store = _store(args)
    run_id = store.run_start("mirror", now_iso())
    report = mirror_mod.run(cfg, store, env, apply=args.apply, limit=args.limit,
                            backfill=args.backfill, verbose=args.verbose)
    print(report.render())
    store.run_finish(run_id, now_iso(), report.render())
    store.close()
    return 1 if report.errors and report.mirrored == 0 else 0


def cmd_sweep(args) -> int:
    cfg, env = _cfg(args)
    store = _store(args)
    run_id = store.run_start("sweep", now_iso())
    report = sweep_mod.run(cfg, store, env, apply=args.apply, only=args.only,
                           verbose=args.verbose,
                           state_dir=Path(args.state_dir).expanduser() if args.state_dir else DEFAULT_STATE,
                           run_id=run_id)
    print(report.render())
    store.close()
    if args.json:
        print(json.dumps([a.__dict__ for a in report.accounts], default=str, indent=2))
    failed_all = all(a.errors and not a.synced for a in report.accounts) if report.accounts else True
    return 1 if failed_all else 0


def cmd_metrics(args) -> int:
    cfg, env = _cfg(args)
    store = _store(args)
    total = 0
    for account in cfg.enabled_accounts(args.only):
        try:
            adapter = make(account, env)
            items = adapter.list_own(None)
            fresh = adapter.metrics(items)
        except Exception as e:  # noqa: BLE001
            print(f"{account.key:<28} metrics failed: {e}")
            continue
        for uri, m in fresh.items():
            row = store.by_uri(uri)
            if row is None:
                continue
            row.likes, row.boosts, row.replies = m.likes, m.boosts, m.replies
            row.comments, row.score, row.metrics_at = m.comments, m.score, now_iso()
            store.set_metrics(row)
            total += 1
        print(f"{account.key:<28} refreshed {len(fresh)} items")
    print(f"metrics updated: {total}")
    store.close()
    return 0


def cmd_status(args) -> int:
    cfg, _env = _cfg(args)
    store = _store(args)
    now = datetime.now(timezone.utc)
    rows = []
    for a in cfg.accounts:
        items = store.items(a.platform, a.instance)
        live = [r for r in items if r.state == "live"]
        low = sum(1 for r in live if r.tier == "low")
        high = sum(1 for r in live if r.tier == "high")
        deleted = sum(1 for r in items if r.state == "deleted")
        failed = sum(1 for r in items if r.state == "failed")
        due = sum(1 for r in live if sweep_mod.due_reason(r, r.tier, a, now))
        newest = max((r.created_at for r in live), default="-")
        rows.append({
            "account": a.key, "live": len(live), "low": low, "high": high,
            "due": due, "deleted": deleted, "failed": failed,
            "ttl_days": a.ttl_days, "low_ttl_days": a.low_engagement_ttl_days,
            "quantile": a.low_engagement_quantile, "newest": newest[:10],
        })
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        head = f"{'account':<28}{'live':>5}{'low':>5}{'high':>5}{'due':>5}{'del':>5}{'err':>4}  {'ttl':>4}/{'low':<4} q   newest"
        print(head)
        for r in rows:
            print(f"{r['account']:<28}{r['live']:>5}{r['low']:>5}{r['high']:>5}{r['due']:>5}"
                  f"{r['deleted']:>5}{r['failed']:>4}  {r['ttl_days']:>4}/{r['low_ttl_days']:<4} "
                  f"{r['quantile']:<3} {r['newest']}")
    store.close()
    return 0


def cmd_expire_now(args) -> int:
    """Debug path: expire one item regardless of TTL, dependants first."""
    cfg, env = _cfg(args)
    store = _store(args)
    platform, instance, remote_id = args.target.split(":", 2)
    account = cfg.account(f"{platform}:{instance}")
    rows = [r for r in store.items(platform, instance) if r.remote_id == remote_id]
    if not rows:
        print(f"no ledger row for {args.target}")
        return 2
    row = rows[0]
    now = now_iso()
    root = (Path(args.state_dir).expanduser() if args.state_dir else DEFAULT_STATE) / "archive"
    # Dependants (mirrors of this item) go first, so no orphan stays public.
    dependants = [r for r in store.items(state="live") if r.mirrored_from == row.remote_uri]
    targets = [(row, account)] + [
        (d, cfg.account(f"{d.platform}:{d.instance}")) for d in dependants
    ]
    for r, acct in targets:
        adapter = make(acct, env)
        path = archive.write(root, r, r.mirrored_to, now)
        print(f"archived {r.remote_uri} → {path}")
        if not args.apply:
            print("DRY would delete")
            continue
        adapter.delete(r)
        store.mark_deleted(r.remote_uri, str(path), now)
        print(f"deleted {r.remote_uri}")
    store.close()
    return 0


def _common() -> argparse.ArgumentParser:
    """Flags accepted before *and* after the subcommand, because both are natural to type."""
    c = argparse.ArgumentParser(add_help=False)
    c.add_argument("--config", default=str(DEFAULT_CONFIG))
    c.add_argument("--state-dir", default=None)
    c.add_argument("--apply", dest="apply", action="store_true", help="actually do it")
    c.add_argument("--dry-run", dest="apply", action="store_false", help="(default) show only")
    c.add_argument("-v", "--verbose", action="store_true")
    c.add_argument("--json", action="store_true")
    c.set_defaults(apply=False)
    return c


def build_parser() -> argparse.ArgumentParser:
    common = _common()
    p = argparse.ArgumentParser(prog="librarian", description="Mirror once, expire on schedule.",
                                parents=[common])
    p.add_argument("--version", action="version", version=f"social-librarian {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("check", parents=[common], help="authenticate every account")
    c.add_argument("--parse-only", action="store_true")
    c.set_defaults(func=cmd_check)

    m = sub.add_parser("mirror", parents=[common], help="Bluesky → Mastodon")
    m.add_argument("--limit", type=int, default=50)
    m.add_argument("--backfill", action="store_true", help="start from the beginning, not now")
    m.set_defaults(func=cmd_mirror)

    s = sub.add_parser("sweep", parents=[common], help="classify and expire")
    s.add_argument("--only", default=None, help="platform:instance")
    s.set_defaults(func=cmd_sweep)

    mt = sub.add_parser("metrics", parents=[common], help="refresh engagement only")
    mt.add_argument("--only", default=None)
    mt.set_defaults(func=cmd_metrics)

    st = sub.add_parser("status", parents=[common], help="what the ledger holds")
    st.set_defaults(func=cmd_status)

    ex = sub.add_parser("expire-now", parents=[common], help="debug: expire one item now")
    ex.add_argument("target", help="platform:instance:remote_id")
    ex.set_defaults(func=cmd_expire_now)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
