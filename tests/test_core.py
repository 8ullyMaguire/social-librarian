"""Config, ledger, archive ordering and mirror composition."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from librarian import archive, mirror  # noqa: E402
from librarian.adapters.base import Item, Metrics  # noqa: E402
from librarian.config import ConfigError, parse  # noqa: E402
from librarian.store import Row, Store  # noqa: E402

MIN = """
[defaults]
ttl_days = 365
low_engagement_ttl_days = 30
low_engagement_quantile = 0.8

[[account]]
platform = "bluesky"
instance = "bsky.social"
handle = "criticalnexus.bsky.social"

[[account]]
platform = "mastodon"
instance = "leftist.network"
handle = "jorvex609"
low_engagement_ttl_days = 21

[mirror]
from = "bluesky:bsky.social"
to = ["mastodon:leftist.network"]
"""


def cfg(text: str = MIN):
    import tomllib
    return parse(tomllib.loads(text), Path("/tmp/config.toml"))


class ConfigTest(unittest.TestCase):
    def test_toml_parses_and_overrides_are_per_instance(self):
        c = cfg()
        self.assertEqual(c.account("bluesky:bsky.social").low_engagement_ttl_days, 30)
        self.assertEqual(c.account("mastodon:leftist.network").low_engagement_ttl_days, 21)
        self.assertEqual(c.mirror.targets, ("mastodon:leftist.network",))

    def test_bad_quantile_is_rejected(self):
        with self.assertRaises(ConfigError):
            cfg(MIN.replace("low_engagement_quantile = 0.8", "low_engagement_quantile = 1.5"))

    def test_low_ttl_cannot_exceed_ttl(self):
        with self.assertRaises(ConfigError):
            cfg(MIN.replace("low_engagement_ttl_days = 30", "low_engagement_ttl_days = 900", 1))

    def test_unknown_platform_is_named(self):
        with self.assertRaises(ConfigError) as cm:
            cfg(MIN.replace('platform = "bluesky"', 'platform = "myspace"'))
        self.assertIn("myspace", str(cm.exception))

    def test_mirror_target_must_be_configured(self):
        with self.assertRaises(ConfigError):
            cfg(MIN.replace('to = ["mastodon:leftist.network"]', 'to = ["mastodon:elsewhere.example"]'))


class StoreTest(unittest.TestCase):
    def test_upsert_is_idempotent_and_state_moves(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(Path(d) / "l.db")
            r = Row(platform="mastodon", instance="leftist.network", account="j",
                    remote_id="1", remote_uri="https://x/1", kind="post", text="hi",
                    url=None, created_at="2026-10-01T00:00:00Z")
            s.upsert(r)
            s.upsert(r)
            self.assertEqual(len(s.items()), 1)
            s.mark_deleted("https://x/1", "/tmp/a.json", "2026-10-29T00:00:00Z")
            got = s.by_uri("https://x/1")
            self.assertEqual(got.state, "deleted")
            self.assertEqual(got.archived_path, "/tmp/a.json")
            s.close()

    def test_mirror_rows_cannot_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(Path(d) / "l.db")
            m = Row(platform="mastodon", instance="leftist.network", account="j",
                    remote_id="9", remote_uri="https://x/9", kind="post", text="m",
                    url=None, created_at="2026-10-01T00:00:00Z",
                    mirrored_from="at://did/app.bsky.feed.post/abc")
            s.upsert(m)
            with self.assertRaises(Exception):
                s.db.execute(
                    "insert into items (platform,instance,account,remote_id,remote_uri,kind,text,created_at,mirrored_from)"
                    " values ('mastodon','leftist.network','j','10','https://x/10','post','m','2026-10-01T00:00:00Z',"
                    "'at://did/app.bsky.feed.post/abc')")
            s.close()


class ArchiveTest(unittest.TestCase):
    def test_archive_is_written_before_any_delete(self):
        with tempfile.TemporaryDirectory() as d:
            r = Row(platform="bluesky", instance="bsky.social", account="a", remote_id="abc",
                    remote_uri="at://x/abc", kind="post", text="keep me", url="https://b/abc",
                    created_at="2026-09-01T00:00:00Z", likes=3, tier="low")
            p = archive.write(Path(d), r, "https://mastodon/1", "2026-10-29T00:00:00Z")
            data = json.loads(p.read_text())
            self.assertEqual(data["item"]["text"], "keep me")
            self.assertEqual(data["item"]["metrics"]["likes"], 3)
            self.assertEqual(data["mirror_url"], "https://mastodon/1")
            self.assertTrue(str(p).endswith("bluesky/bsky.social/2026-09/abc.json"), str(p))

    def test_archive_path_is_stable(self):
        r = Row(platform="lemmy", instance="reddthat.com", account="p", remote_id="7",
                remote_uri="https://r/post/7", kind="post", text="t", url=None,
                created_at="2026-07-15T10:00:00Z")
        a = archive.path_for(Path("/root"), r)
        b = archive.path_for(Path("/root"), r)
        self.assertEqual(a, b)
        self.assertIn("2026-07", str(a))


class ComposeTest(unittest.TestCase):
    def test_original_post_keeps_text_and_link(self):
        it = Item(platform="bluesky", instance="bsky.social", account="a", remote_id="r",
                  remote_uri="at://x/r", kind="post", text="hello world",
                  url="https://bsky.app/profile/a/post/r", created_at="2026-10-01T00:00:00Z")
        text = mirror.compose(it, 500)
        self.assertIn("hello world", text)
        self.assertIn("https://bsky.app/profile/a/post/r", text)

    def test_repost_is_marked_and_attributed(self):
        it = Item(platform="bluesky", instance="bsky.social", account="a", remote_id="r",
                  remote_uri="at://x/r", kind="repost", text="their words",
                  url="https://bsky.app/profile/a/post/r", created_at="2026-10-01T00:00:00Z",
                  subject_author="someone.bsky.social")
        text = mirror.compose(it, 500)
        self.assertTrue(text.startswith("🔁"))
        self.assertIn("@someone.bsky.social", text)

    def test_long_post_is_capped_and_keeps_the_link(self):
        it = Item(platform="bluesky", instance="bsky.social", account="a", remote_id="r",
                  remote_uri="at://x/r", kind="post", text="x" * 900,
                  url="https://bsky.app/profile/a/post/r", created_at="2026-10-01T00:00:00Z")
        text = mirror.compose(it, 500)
        self.assertLessEqual(len(text), 500)
        self.assertIn("https://bsky.app/profile/a/post/r", text)

    def test_textless_post_falls_back_to_the_link(self):
        it = Item(platform="bluesky", instance="bsky.social", account="a", remote_id="r",
                  remote_uri="at://x/r", kind="post", text="",
                  url="https://bsky.app/profile/a/post/r", created_at="2026-10-01T00:00:00Z")
        self.assertIn("https://bsky.app/profile/a/post/r", mirror.compose(it, 500))


if __name__ == "__main__":
    unittest.main()
