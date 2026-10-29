# Verification — 2026-10-29

Every line below is output from a real run against the live accounts. Identifiers are quoted exactly
so they can be re-checked.

## 1. Credentials

```
$ socialctl check
bluesky-main           bluesky    ok  criticalnexus.bsky.social (did:plc:bnjxoctfnukack6evxsbexc4)
lemmy-reddthat         lemmy      ok  PumpkinDrama (3618760)
mastodon-leftist       mastodon   ok  jorvex609 (116702174611226104)
```

## 2. Mirror, through the Rust crate

A test post was created on Bluesky with `socialctl post --account bluesky-main`:

```
{"id":"3myyzltcbzz2z","provider":"bluesky","url":"https://bsky.app/profile/criticalnexus.bsky.social/post/3myyzltcbzz2z"}
```

`python3 -m librarian mirror --apply` reported `new=1 mirrored=1 skipped=0`, and the ledger held:

```
mastodon  117523658320098384  live  https://leftist.network/@jorvex609/117523658320098384
bluesky   3myyzltcbzz2z       live  (mirrored_to = the Mastodon URL above)
```

The Mastodon status existed publicly (`HTTP 200` at
`https://leftist.network/@jorvex609/117523658320098384`) with the source text and the Bluesky link.
A second `mirror --apply` added nothing (`new=0`), which is the dedupe index doing its job.

## 3. Expiry, through the same crate

```
$ python3 -m librarian expire-now bluesky:bsky.social:3myyzltcbzz2z --apply
archived …/archive/bluesky/bsky.social/2026-10/3myyzltcbzz2z.json
deleted  at://did:plc:bnjxoctfnukack6evxsbexc4/app.bsky.feed.post/3myyzltcbzz2z
archived …/archive/mastodon/leftist.network/2026-10/117523658320098384.json
deleted  https://leftist.network/@jorvex609/117523658320098384
```

Afterwards:

```
$ curl -s "https://bsky.social/xrpc/com.atproto.repo.getRecord?repo=did:plc:bnjx...&collection=app.bsky.feed.post&rkey=3myyzltcbzz2z"
{"error":"RecordNotFound", …}
$ curl -o /dev/null -w '%{http_code}' https://leftist.network/api/v1/statuses/117523658320098384
404
$ curl -o /dev/null -w '%{http_code}' https://leftist.network/@jorvex609/117523658320098384
404
```

The archive JSON contains the text (`bridge test 10:10:27 (mirrored by librarian through socialite)`),
the metrics, the tier and `mirror_url`. Note that Mastodon's web page reported `200` for about ten
seconds after the delete — the API and the page then both settled on `404`; the API is the source of
truth.

## 4. Crate round-trips without the librarian

Mastodon: `socialctl post` returned `117523648574589214`, the page was `200`, `socialctl delete`
succeeded, and the API and page became `404`. A second delete returned `Ok` (404 is treated as
success, so deletion is idempotent).

Bluesky: `socialctl post` returned `3myyzktiewg2s`, `socialctl delete` twice both succeeded, and
`getRecord` returned `RecordNotFound`.

## 5. Policy dry run on real data

`librarian sweep` (no `--apply`) listed items such as:

```
2026-06-29 mastodon:116835287975701783 low  age=122d like=1 boost=0 reply=0 -> low tier, 122d >= 21d
```

No delete call was made. The Mastodon account's own token has `write:statuses read:accounts` scopes
only, so reads fall back to the public timeline; this is why the adapter tries the token and retries
anonymously on 403.

## 6. What is NOT verified

- Lemmy deletion live: the account has no disposable post, and creating one requires choosing a
  community. The call shape (`PUT /api/v3/post {post_id, deleted:true}`) matches Postiz's and the
  login/site calls are verified, but the tombstone itself was not exercised.
- Telegram and Discord: no bot tokens were provided, so those adapters are request-shape only.
- The remaining 31 Postiz providers: identified, not implemented (`Error::Unsupported`).
- A full destructive sweep: deliberately not run. `sweep --apply` waits for the operator, and the
  systemd unit ships with `SOCIAL_LIBRARIAN_SWEEP_APPLY=0`.
