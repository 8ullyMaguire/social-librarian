"""Optional transport: hand writes and deletes to the `socialite` Rust crate via `socialctl`.

The policy engine (what to keep, what to expire, how to archive) stays here. The platform calls go
through the crate when it is configured, so one implementation of each network's API serves every
project — the librarian, shortwave, and anything else that depends on `socialite`.

Configuration, in ~/.config/social-librarian/env:

    SOCIALCTL=/personal/projects/socialite/target/release/socialctl
    SOCIALCTL_ACCOUNT_MASTODON_LEFTIST_NETWORK=mastodon-leftist
    SOCIALCTL_ACCOUNT_BLUESKY_BSKY_SOCIAL=bluesky-main
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .config import Account


def env_key(account: Account) -> str:
    inst = account.instance.upper().replace(".", "_").replace("-", "_")
    return f"SOCIALCTL_ACCOUNT_{account.platform.upper()}_{inst}"


class Socialctl:
    def __init__(self, binary: str, name: str):
        self.binary = binary
        self.name = name

    @classmethod
    def from_env(cls, account: Account, env: dict[str, str]) -> "Socialctl | None":
        binary = env.get("SOCIALCTL", "").strip()
        name = env.get(env_key(account), "").strip()
        if not binary or not name:
            return None
        return cls(binary, name)

    def _run(self, args: list[str]) -> str:
        proc = subprocess.run([self.binary, *args], capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise RuntimeError(f"socialctl {' '.join(args[:2])}: {proc.stderr.strip() or proc.stdout.strip()}")
        return proc.stdout

    def post(self, text: str, visibility: str = "public") -> tuple[str, str]:
        out = self._run(["post", "--account", self.name, "--text", text,
                         "--visibility", visibility])
        payload = json.loads(out.strip().splitlines()[-1])
        return str(payload.get("id", "")), str(payload.get("url") or "")

    def delete(self, remote_id: str) -> None:
        self._run(["delete", "--account", self.name, "--id", remote_id])


def accounts_file() -> Path:
    return Path.home() / ".config/socialite/accounts.toml"
