"""The policy is arithmetic; it must be right before anything is deleted."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from librarian.config import Account, Weights  # noqa: E402
from librarian.sweep import classify, due_reason, engagement  # noqa: E402
from librarian.store import Row  # noqa: E402
from librarian.adapters.base import Metrics  # noqa: E402

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
W = Weights()


def row(i: int, likes: int = 0, boosts: int = 0, replies: int = 0, days_old: int = 40) -> Row:
    created = f"2026-08-01T00:00:00Z"
    if days_old != 40:
        from datetime import timedelta
        created = (NOW - timedelta(days=days_old)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return Row(
        platform="bluesky", instance="bsky.social", account="a", remote_id=str(i),
        remote_uri=f"at://x/{i}", kind="post", text=f"t{i}", url=None, created_at=created,
        likes=likes, boosts=boosts, replies=replies,
    )


def account(**kw) -> Account:
    base = dict(
        platform="bluesky", instance="bsky.social", handle="a",
        ttl_days=365, low_engagement_ttl_days=30, low_engagement_quantile=0.8,
        quantile_min_age_days=30, min_age_days=1, max_deletes_per_run=25, weights=W,
    )
    base.update(kw)
    return Account(**base)


class ClassifyTest(unittest.TestCase):
    def test_bottom_eighty_percent_is_exactly_eight_of_ten(self):
        rows = [row(i, likes=i) for i in range(10)]
        tiers = classify(rows, 0.8, W)
        low = [u for u, t in tiers.items() if t == "low"]
        high = [u for u, t in tiers.items() if t == "high"]
        self.assertEqual(len(low), 8, tiers)
        self.assertEqual(len(high), 2, tiers)
        # The two best survive.
        self.assertEqual(sorted(high), ["at://x/8", "at://x/9"])

    def test_all_zero_engagement_still_splits_deterministically(self):
        rows = [row(i, likes=0) for i in range(10)]
        a = classify(rows, 0.8, W)
        b = classify(list(reversed(rows)), 0.8, W)
        self.assertEqual(a, b)
        self.assertEqual(sum(1 for t in a.values() if t == "high"), 2)

    def test_quantile_one_puts_everything_in_low(self):
        rows = [row(i, likes=i) for i in range(5)]
        self.assertTrue(all(t == "low" for t in classify(rows, 1.0, W).values()))

    def test_single_item_quantile_point_eight(self):
        # ceil(1 * 0.2) = 1 high: a lone post is not punished for having no peers.
        self.assertEqual(classify([row(1)], 0.8, W), {"at://x/1": "high"})

    def test_engagement_weights_are_applied(self):
        m = Metrics(likes=2, boosts=3, replies=1)
        self.assertEqual(engagement(m, W), 2 * 1 + 3 * 2 + 1 * 2)


class DueTest(unittest.TestCase):
    def test_low_tier_expires_at_thirty_days_not_before(self):
        a = account()
        self.assertIsNone(due_reason(row(1, days_old=29), "low", a, NOW))
        self.assertIsNotNone(due_reason(row(1, days_old=30), "low", a, NOW))
        self.assertIn("low tier", due_reason(row(1, days_old=31), "low", a, NOW))

    def test_high_tier_lives_to_a_year(self):
        a = account()
        self.assertIsNone(due_reason(row(1, days_old=200), "high", a, NOW))
        self.assertIsNotNone(due_reason(row(1, days_old=366), "high", a, NOW))

    def test_per_instance_override_changes_verdict_at_the_same_age(self):
        short = account(instance="leftist.network", low_engagement_ttl_days=21)
        long = account(instance="reddthat.com", low_engagement_ttl_days=30)
        item = row(1, days_old=25)
        self.assertIsNotNone(due_reason(item, "low", short, NOW))
        self.assertIsNone(due_reason(item, "low", long, NOW))

    def test_min_age_guard_prevents_same_day_deletion(self):
        a = account(low_engagement_ttl_days=1, ttl_days=1)
        self.assertIsNone(due_reason(row(1, days_old=0), "low", a, NOW))

    def test_unclassified_only_expires_on_the_long_ttl(self):
        a = account()
        self.assertIsNone(due_reason(row(1, days_old=100), None, a, NOW))
        self.assertIsNotNone(due_reason(row(1, days_old=400), None, a, NOW))


if __name__ == "__main__":
    unittest.main()
