"""The ledger: what exists, what it earned, what happened to it."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = """
create table if not exists items (
  id            integer primary key autoincrement,
  platform      text not null,
  instance      text not null,
  account       text not null,
  remote_id     text not null,
  remote_uri    text not null unique,
  kind          text not null default 'post',
  text          text not null default '',
  url           text,
  created_at    text not null,
  likes         integer not null default 0,
  boosts        integer not null default 0,
  replies       integer not null default 0,
  comments      integer not null default 0,
  score         real not null default 0,
  metrics_at    text,
  tier          text,
  state         text not null default 'live',
  reason        text,
  mirrored_from text,
  mirrored_to   text,
  archived_path text,
  deleted_at    text,
  error         text
);
create unique index if not exists items_mirror_once on items(mirrored_from)
  where mirrored_from is not null;
create index if not exists items_account_state on items(platform, instance, state);
create index if not exists items_created on items(created_at);

create table if not exists state (key text primary key, value text);
create table if not exists runs (
  id integer primary key autoincrement,
  started_at text not null,
  finished_at text,
  command text not null,
  summary text
);
"""


@dataclass
class Row:
    """One ledger item. Mirrors `librarian.adapters.base.Item` plus bookkeeping."""

    platform: str
    instance: str
    account: str
    remote_id: str
    remote_uri: str
    kind: str
    text: str
    url: str | None
    created_at: str
    likes: int = 0
    boosts: int = 0
    replies: int = 0
    comments: int = 0
    score: float = 0.0
    metrics_at: str | None = None
    tier: str | None = None
    state: str = "live"
    reason: str | None = None
    mirrored_from: str | None = None
    mirrored_to: str | None = None
    archived_path: str | None = None
    deleted_at: str | None = None
    error: str | None = None
    id: int | None = None

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.instance}:{self.remote_id}"


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("pragma journal_mode=WAL")
        self.db.execute("pragma busy_timeout=8000")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # --- items -----------------------------------------------------------
    def upsert(self, row: Row) -> None:
        self.db.execute(
            """
            insert into items (platform, instance, account, remote_id, remote_uri, kind, text, url,
                               created_at, likes, boosts, replies, comments, score, metrics_at,
                               tier, state, reason, mirrored_from, mirrored_to)
            values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            on conflict(remote_uri) do update set
                text = excluded.text, url = excluded.url, likes = excluded.likes,
                boosts = excluded.boosts, replies = excluded.replies,
                comments = excluded.comments, score = excluded.score,
                metrics_at = excluded.metrics_at, state = excluded.state,
                mirrored_to = coalesce(excluded.mirrored_to, items.mirrored_to)
            """,
            (
                row.platform, row.instance, row.account, row.remote_id, row.remote_uri,
                row.kind, row.text, row.url, row.created_at, row.likes, row.boosts,
                row.replies, row.comments, row.score, row.metrics_at, row.tier,
                row.state, row.reason, row.mirrored_from, row.mirrored_to,
            ),
        )

    def items(self, platform: str | None = None, instance: str | None = None,
              state: str | None = None) -> list[Row]:
        sql = "select * from items where 1=1"
        args: list = []
        if platform:
            sql += " and platform = ?"
            args.append(platform)
        if instance:
            sql += " and instance = ?"
            args.append(instance)
        if state:
            sql += " and state = ?"
            args.append(state)
        sql += " order by created_at asc"
        out = []
        for r in self.db.execute(sql, args):
            r = dict(r)
            rid = r.pop("id")
            out.append(Row(**r, id=rid))
        return out

    def by_uri(self, uri: str) -> Row | None:
        cur = self.db.execute("select * from items where remote_uri = ?", (uri,))
        r = cur.fetchone()
        if not r:
            return None
        r = dict(r)
        rid = r.pop("id")
        return Row(**r, id=rid)

    def set_metrics(self, row: Row) -> None:
        self.db.execute(
            "update items set likes=?, boosts=?, replies=?, comments=?, score=?, metrics_at=? where remote_uri=?",
            (row.likes, row.boosts, row.replies, row.comments, row.score, row.metrics_at, row.remote_uri),
        )

    def set_tier(self, uri: str, tier: str | None, reason: str | None) -> None:
        self.db.execute("update items set tier=?, reason=? where remote_uri=?", (tier, reason, uri))

    def mark_deleted(self, uri: str, archived_path: str, when: str) -> None:
        self.db.execute(
            "update items set state='deleted', deleted_at=?, archived_path=?, error=null where remote_uri=?",
            (when, archived_path, uri),
        )

    def mark_failed(self, uri: str, error: str) -> None:
        self.db.execute("update items set state='failed', error=? where remote_uri=?", (error[:500], uri))

    def set_mirror(self, uri: str, mirrored_to: str) -> None:
        self.db.execute("update items set mirrored_to=? where remote_uri=?", (mirrored_to, uri))

    def counts(self, platform: str, instance: str) -> dict[str, int]:
        rows = self.db.execute(
            "select state, count(*) c from items where platform=? and instance=? group by state",
            (platform, instance),
        )
        return {r["state"]: r["c"] for r in rows}

    # --- state -----------------------------------------------------------
    def state_get(self, key: str) -> str | None:
        r = self.db.execute("select value from state where key=?", (key,)).fetchone()
        return r["value"] if r else None

    def state_set(self, key: str, value: str) -> None:
        self.db.execute(
            "insert into state (key, value) values (?,?) on conflict(key) do update set value=excluded.value",
            (key, value),
        )

    # --- runs ------------------------------------------------------------
    def run_start(self, command: str, when: str) -> int:
        cur = self.db.execute(
            "insert into runs (started_at, command) values (?,?)", (when, command)
        )
        return int(cur.lastrowid or 0)

    def run_finish(self, run_id: int, when: str, summary: str) -> None:
        self.db.execute(
            "update runs set finished_at=?, summary=? where id=?", (when, summary[:2000], run_id)
        )
