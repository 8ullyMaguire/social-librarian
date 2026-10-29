# ADR 0001 — Ephemeral by default, archived before deleted

Status: accepted, 2026-10-29.

## Context

Every post written on three networks stays forever unless somebody remembers to delete it. The
owner's position is the opposite: the archive is not a public good, the feed is; the default state of
a post should be *temporary*, and only content that earned attention should survive longer. That
inverts the usual model, where deletion is a manual chore and retention is free.

Two constraints shaped the design. First, a wrong deletion is unrecoverable — the platform APIs do
not offer undelete — so the system must be conservative by construction. Second, "low engagement"
cannot be a fixed number: an account with 300 followers never reaches the threshold that an account
with 30 000 clears by accident, and a fixed threshold would either delete everything or nothing.

## Decision

- **Expiry is the default, not an option.** Every account is enrolled with a 12-month ceiling; there
  is no "keep forever" tier. Protection is expressed as a longer TTL, not as exemption.
- **Tiering is a per-account rank.** The bottom `low_engagement_quantile` (default 0.8) of an
  account's posts by weighted engagement expire at `low_engagement_ttl_days` (default 30); the rest
  at `ttl_days` (default 365). Each account — and therefore each instance — carries its own numbers,
  because reddthat.com and leftist.network have different audiences and different obligations.
- **Nothing is ranked before it has had a month.** `quantile_min_age_days = 30` means early posts are
  not judged on an empty scoreboard.
- **Archive first, delete second.** The JSON archive is written, fsynced, and only then does the API
  call happen. If the archive fails, the item survives and the failure is recorded. This is the one
  ordering that makes deletion defensible.
- **Deletion is capped.** `max_deletes_per_run` bounds how much a single run can destroy; a bug in
  the policy costs a bounded amount, not the archive.
- **One-way mirror.** Bluesky → Mastodon only. Mastodon posts are never copied back: the account
  there is a distribution channel, not a second authoring surface.
- **Mirrors die with their source.** A mirrored Mastodon status carries `mirrored_from` in the
  ledger, so expiring the Bluesky original removes the copy too — otherwise the ephemeral promise
  would be defeated by the mirror.

## Consequences

- The first live sweep of an established account will delete a lot at once. That is why the service
  ships with the sweep in dry-run mode and why `expire-now` exists: the operator sees the list before
  the policy touches anything.
- Lemmy only tombstones (`deleted: true`); the post body remains in the database. This is a protocol
  limitation, documented rather than hidden.
- The archive grows without bound. At a few hundred items a year of text it is trivial; if images are
  ever archived, a retention policy for the archive itself will be needed.
- Engagement is measured at sweep time, so a post that goes viral on day 40 is already gone on day 30.
  That is the intended bias: the system is designed to forget, not to be fair.
