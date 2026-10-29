# social-librarian

Two jobs, run on a schedule against your own accounts:

1. **Mirror** every new Bluesky post and repost to Mastodon, exactly once.
2. **Expire** old content everywhere, on a per-instance policy: the bottom 80 % of an account's
   posts by engagement disappear after 30 days, the rest after a year — and the text is archived
   locally before anything is deleted.

Plain Python 3.11+, standard library only. No dependencies to rot.

## Commands

```bash
librarian check                     # authenticate every account (read-only)
librarian status                    # what the ledger holds, per account
librarian mirror --apply            # Bluesky → Mastodon, once per item
librarian sweep                     # dry run: what would be deleted, and why
librarian sweep --apply             # actually archive + delete
librarian expire-now <platform:instance:remote_id> --apply   # one item, for testing
```

`mirror` and `sweep` do nothing destructive without `--apply`. Both flags work before or after the
subcommand.

## The policy

Set in `~/.config/social-librarian/config.toml` (mode 0600), with `[defaults]` and per-account
overrides — per platform *and* per instance:

```toml
[defaults]
ttl_days = 365                    # everything goes within a year
low_engagement_ttl_days = 30      # the bottom 80 % goes after a month
low_engagement_quantile = 0.8
quantile_min_age_days = 30        # nothing is ranked before it has had a month
min_age_days = 1                  # never delete something posted today
max_deletes_per_run = 25          # rate limit on destruction
```

Engagement is a weighted sum (like 1, boost 2, reply 2, comment 2, Lemmy score 1) — configurable per
account. Classification is a **rank**, not a threshold: for each account, its own posts are sorted by
engagement and the bottom `low_engagement_quantile` are the expiring tier. An account with modest
numbers still keeps its best 20 %.

Secrets live in `~/.config/social-librarian/env` (mode 0600), never in the config:

```
BSKY_IDENTIFIER=…
BSKY_APP_PASSWORD=…
MASTODON_LEFTIST_NETWORK_TOKEN=…
LEMMY_REDDTHAT_COM_PASSWORD=…
```

## What "delete" means per platform

| Platform | Call | Result |
|---|---|---|
| Mastodon | `DELETE /api/v1/statuses/:id` | gone, 404 afterwards |
| Bluesky | `com.atproto.repo.deleteRecord` | gone, `RecordNotFound` afterwards |
| Lemmy (reddthat) | `PUT /api/v3/post {deleted:true}` | the most the protocol offers: a tombstone |

Mirrored Mastodon statuses are deleted **with** their source item: the ledger stores
`mirrored_from`, so expiring a Bluesky post takes down the copy that was made of it.

## Crawl behaviour

Each account keeps a `sync:since:<platform>:<instance>` watermark, so a nightly sweep fetches only
what is new and finishes in seconds. The first run for an account backfills back to the expiry
horizon (`ttl_days + quantile_min_age_days`) — past that point nothing can still be alive, so paging
deeper would only add rows destined for deletion on the same run.

## Archive

`~/.local/share/social-librarian/archive/<platform>/<instance>/<YYYY-MM>/<remote_id>.json`, written
and fsynced **before** the delete call. If the archive cannot be written, the item is not deleted and
the error is recorded. The JSON holds the text, metrics, timestamps, tier, reason and mirror URL.

## Transport: the `socialite` crate

Writes and deletes are delegated to the Rust crate (`~/code-local/rust/socialite`) when it is
configured, so there is one implementation of each network's API rather than one per project. Add to
`~/.config/social-librarian/env`:

```
SOCIALCTL=/personal/projects/socialite/target/release/socialctl
SOCIALCTL_ACCOUNT_MASTODON_LEFTIST_NETWORK=mastodon-leftist
SOCIALCTL_ACCOUNT_BLUESKY_BSKY_SOCIAL=bluesky-main
SOCIALCTL_ACCOUNT_LEMMY_REDDTHAT_COM=lemmy-reddthat
```

With those set, `post_status` and every `delete` go through `socialctl`; the ledger, the policy and
the archive stay in Python. Without them the adapters fall back to their own HTTP calls, which is what
lets the crate be adopted one platform at a time.

## Running it

`deploy/install.sh` installs `social-librarian.service`, which mirrors every 120 s and sweeps once a
day after 04:00. **The sweep starts in dry-run mode** (`SOCIAL_LIBRARIAN_SWEEP_APPLY=0`) so the first
sweeps only write logs; read them, then flip the flag in the unit.

## Rules the code enforces

- Never delete something younger than `min_age_days`.
- Never delete more than `max_deletes_per_run` items per account per run.
- Never delete if the archive write failed.
- Never mirror the same source record twice (unique index on `mirrored_from`).
- Never touch content the account does not own.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

23 tests: quantile selection (exactly 8 of 10), tie determinism, TTL boundaries, per-instance
overrides, min-age guard, config validation, ledger idempotency, archive ordering and path shape,
mirror text composition and dedupe.
