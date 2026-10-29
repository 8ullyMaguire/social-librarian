"""Archive before deletion: if the JSON cannot be written, nothing is deleted."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .store import Row


def path_for(root: Path, row: Row) -> Path:
    month = row.created_at[:7] or "unknown"
    safe = row.remote_id.replace("/", "_")
    return root / row.platform / row.instance / month / f"{safe}.json"


def write(root: Path, row: Row, mirror_url: str | None, now: str) -> Path:
    """Write the item as JSON, durably, and return the path.

    Order matters: temp file, fsync the file, rename, fsync the directory. A crash mid-way leaves
    either the old state or nothing, never a truncated archive that looks complete.
    """
    target = path_for(root, row)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "archived_at": now,
        "reason": row.reason,
        "item": {
            "platform": row.platform,
            "instance": row.instance,
            "account": row.account,
            "remote_id": row.remote_id,
            "remote_uri": row.remote_uri,
            "kind": row.kind,
            "text": row.text,
            "url": row.url,
            "created_at": row.created_at,
            "metrics": {
                "likes": row.likes,
                "boosts": row.boosts,
                "replies": row.replies,
                "comments": row.comments,
                "score": row.score,
                "measured_at": row.metrics_at,
            },
            "tier": row.tier,
            "mirrored_from": row.mirrored_from,
            "mirrored_to": row.mirrored_to,
        },
        "mirror_url": mirror_url,
    }
    tmp = target.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, target)
    dir_fd = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return target
