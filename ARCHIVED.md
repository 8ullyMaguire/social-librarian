# ARCHIVED — 2026-11-06

This project is **archived**: read-only in intent, and no further work is planned.

## What it is

Two scheduled jobs against your own accounts: mirror every new Bluesky post and repost to
Mastodon exactly once, and expire old content everywhere on a per-instance policy — the
bottom 80 % of an account's posts by engagement disappear after 30 days, the rest after a
year, with the text archived locally before anything is deleted. Plain Python 3.11+,
standard library only.

## What was built, measured

- 27 tracked files: `librarian/` (adapters for Bluesky, Mastodon, Lemmy; `mirror`,
  `sweep`, `bridge`, `archive`, `store`, `http`, `cli`, `config`), `deploy/` (a systemd
  unit, `install.sh`, `run.sh`, an example config), and two ADR/verification docs.
- **23 tests** in `tests/test_core.py` (13) and `tests/test_policy.py` (10).
- A full-archive sweep was run against the live accounts and its numbers are in
  `docs/VERIFICATION.md`; it is also where the delete-cap backlog was recorded.

## Why archived

It is dormant, not wrong. The one operational reason to run it — automatic cross-posting
plus engagement-based expiry — is a *policy* the estate has not settled, and the code is a
faithful implementation of a policy that is no longer certain to be the one wanted. Nothing
scheduled it: no systemd timer, no cron entry, no deployed copy on this host (checked
2026-11-06).

Archiving does not delete the decision: `docs/ADR-0001-ephemeral-by-default.md` is the
argument for expiry-by-default, and it is the part worth keeping if the policy is revisited.

## What would revive it

A decision on the expiry policy, and one account willing to have its posts expire. Both
are owner calls; neither is engineering. The sibling crate `socialite` (publishing) is
**not** archived — `shortwave` depends on it and uses it today.
