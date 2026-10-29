"""Mastodon: read own statuses, measure them, post mirrors, delete."""

from __future__ import annotations

from .base import Adapter, Item, Metrics

MAX_CHARS = 500


class Mastodon(Adapter):
    platform = "mastodon"

    def __init__(self, account, identifier: str, secret: str, base: str | None = None):
        super().__init__(account, identifier, secret)
        self.base = (base or f"https://{account.instance}").rstrip("/")
        self._me: dict | None = None

    def _h(self) -> dict:
        return {"Authorization": f"Bearer {self.secret}"}

    def whoami(self) -> dict:
        from ..http import request_json

        me = request_json("GET", f"{self.base}/api/v1/accounts/verify_credentials", None, self._h())
        self._me = me
        return {"acct": me.get("acct"), "id": me.get("id"), "statuses": me.get("statuses_count")}

    @property
    def me(self) -> dict:
        return self._me or self.whoami() or {}

    def list_own(self, since: str | None = None) -> list[Item]:
        from ..http import request_json

        aid = self.me.get("id")
        items: list[Item] = []
        max_id = None
        for _ in range(20):  # hard stop: 20 pages * 40 = 800 statuses
            url = f"{self.base}/api/v1/accounts/{aid}/statuses?limit=40&exclude_replies=false&exclude_reblogs=false"
            if max_id:
                url += f"&max_id={max_id}"
            page = request_json("GET", url, None, self._h())
            if not page:
                break
            for s in page:
                created = s.get("created_at", "")
                if since and created < since:
                    continue
                kind = "repost" if s.get("reblog") else ("reply" if s.get("in_reply_to_id") else "post")
                text = _plain(s.get("content", "")) if s.get("content") else ""
                items.append(Item(
                    platform=self.platform,
                    instance=self.account.instance,
                    account=self.account.handle,
                    remote_id=str(s["id"]),
                    remote_uri=s.get("uri") or f"{self.base}/statuses/{s['id']}",
                    kind=kind,
                    text=text,
                    url=s.get("url"),
                    created_at=created,
                    metrics=Metrics(
                        likes=int(s.get("favourites_count") or 0),
                        boosts=int(s.get("reblogs_count") or 0),
                        replies=int(s.get("replies_count") or 0),
                    ),
                ))
            max_id = page[-1]["id"]
            if len(page) < 40:
                break
        return items

    def metrics(self, items: list[Item]) -> dict[str, Metrics]:
        from ..http import request_json

        out: dict[str, Metrics] = {}
        for i in items:
            try:
                s = request_json("GET", f"{self.base}/api/v1/statuses/{i.remote_id}", None, self._h())
            except Exception:
                continue
            out[i.remote_uri] = Metrics(
                likes=int(s.get("favourites_count") or 0),
                boosts=int(s.get("reblogs_count") or 0),
                replies=int(s.get("replies_count") or 0),
            )
        return out

    def post_status(self, text: str) -> str:
        from ..http import request_json

        if len(text) > MAX_CHARS:
            text = text[: MAX_CHARS - 1] + "…"
        s = request_json("POST", f"{self.base}/api/v1/statuses",
                         {"status": text, "visibility": "public"}, self._h())
        return s.get("url") or s.get("uri", "")

    def delete(self, item) -> None:
        from ..http import HttpError, request_json

        try:
            request_json("DELETE", f"{self.base}/api/v1/statuses/{item.remote_id}", None, self._h())
        except HttpError as e:
            if e.status == 404:
                return  # already gone: the desired state
            raise


def _plain(html: str) -> str:
    """Strip the tags Mastodon puts in `content` without pulling in a parser."""
    out, inside = [], False
    for ch in html.replace("<br />", "\n").replace("</p>", "\n"):
        if ch == "<":
            inside = True
        elif ch == ">":
            inside = False
        elif not inside:
            out.append(ch)
    import html as htmlmod

    return htmlmod.unescape("".join(out)).strip()
