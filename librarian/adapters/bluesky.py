"""AT Protocol: posts, reposts, engagement, deletion."""

from __future__ import annotations

from .base import Adapter, Item, Metrics, now_iso

API = "https://bsky.social/xrpc"
POSTS = "app.bsky.feed.post"
REPOSTS = "app.bsky.feed.repost"


class Bluesky(Adapter):
    platform = "bluesky"

    def __init__(self, account, identifier: str, secret: str, api: str = API):
        super().__init__(account, identifier, secret)
        self.api = api.rstrip("/")
        self._session: dict | None = None

    # --- auth ------------------------------------------------------------
    def _login(self) -> dict:
        from ..http import request_json

        s = request_json(
            "POST",
            f"{self.api}/com.atproto.server.createSession",
            {"identifier": self.identifier, "password": self.secret},
        )
        self._session = s
        return s

    @property
    def session(self) -> dict:
        return self._session or self._login()

    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.session['accessJwt']}"}

    def _call(self, method: str, body: dict | None = None, params: dict | None = None) -> dict:
        """One XRPC call. Queries are GET with query params; procedures are POST with JSON."""
        from ..http import HttpError, request_json
        from urllib.parse import urlencode

        def do() -> dict:
            if params is not None:
                url = f"{self.api}/{method}?{urlencode(params, doseq=True)}"
                return request_json("GET", url, None, self._auth())
            return request_json("POST", f"{self.api}/{method}", body or {}, self._auth())

        try:
            return do()
        except HttpError as e:
            if e.status != 401:
                raise
            self._login()
            return do()

    def whoami(self) -> dict:
        s = self.session
        return {"handle": s.get("handle"), "did": s.get("did")}

    # --- reading ---------------------------------------------------------
    def _list_collection(self, collection: str, since: str | None,
                         older_than: str | None = None, limit: int = 100,
                         max_items: int = 20000) -> list[dict]:
        """Page the collection newest-first and stop once records are older than the horizon.

        The horizon is the point past which everything is due anyway, so paging further would only
        add rows that the sweep would classify and delete on the same run.
        """
        out: list[dict] = []
        cursor = None
        while True:
            params = {"repo": self.session["did"], "collection": collection, "limit": limit}
            if cursor:
                params["cursor"] = cursor
            res = self._call("com.atproto.repo.listRecords", params=params)
            records = res.get("records", [])
            for r in records:
                created = (r.get("value") or {}).get("createdAt")
                if not created:
                    continue
                if since and created < since:
                    continue
                out.append(r)
            created_dates = [ (r.get("value") or {}).get("createdAt", "") for r in records ]
            oldest = min(created_dates) if created_dates else None
            newest = max(created_dates) if created_dates else None
            cursor = res.get("cursor")
            if not cursor or len(records) < limit or len(out) >= max_items:
                break
            if older_than and oldest and oldest < older_than:
                break
            # Paging is newest-first: once a page is entirely older than the watermark there is
            # nothing left to mirror, and continuing would crawl the whole archive every 2 minutes.
            if since and newest and newest < since:
                break
        return out

    def list_own(self, since: str | None = None, older_than: str | None = None,
                 limit: int = 20000) -> list[Item]:
        items: list[Item] = []
        for r in self._list_collection(POSTS, since, older_than)[:limit]:
            v = r.get("value") or {}
            rkey = r["uri"].rsplit("/", 1)[-1]
            text = (v.get("text") or "").strip()
            if not text:
                text = _alt_text(v) or ""
            if v.get("reply"):
                kind = "reply"
            else:
                kind = "post"
            items.append(Item(
                platform=self.platform,
                instance=self.account.instance,
                account=self.account.handle,
                remote_id=rkey,
                remote_uri=r["uri"],
                kind=kind,
                text=text,
                url=f"https://bsky.app/profile/{self.session['handle']}/post/{rkey}",
                created_at=_iso(v.get("createdAt")),
            ))
        reposts = self._list_collection(REPOSTS, since, older_than)[:limit]
        subjects = [r["value"]["subject"]["uri"] for r in reposts if r.get("value", {}).get("subject")]
        resolved = self._posts(subjects) if subjects else {}
        for r in reposts:
            v = r.get("value") or {}
            subject = (v.get("subject") or {}).get("uri")
            if not subject:
                continue
            rkey = r["uri"].rsplit("/", 1)[-1]
            author, text = resolved.get(subject, (None, ""))
            items.append(Item(
                platform=self.platform,
                instance=self.account.instance,
                account=self.account.handle,
                remote_id=rkey,
                remote_uri=r["uri"],
                kind="repost",
                text=text,
                url=f"https://bsky.app/profile/{self.session['handle']}/post/{rkey}",
                created_at=_iso(v.get("createdAt")),
                subject_uri=subject,
                subject_author=author,
            ))
        items.sort(key=lambda i: i.created_at)
        return items

    def _posts(self, uris: list[str]) -> dict[str, tuple[str, str]]:
        out: dict[str, tuple[str, str]] = {}
        for i in range(0, len(uris), 25):
            chunk = uris[i:i + 25]
            res = self._call("app.bsky.feed.getPosts", params={"uris": chunk})
            for p in res.get("posts", []):
                author = (p.get("author") or {}).get("handle") or ""
                text = ((p.get("record") or {}).get("text") or "").strip()
                out[p.get("uri", "")] = (author, text)
        return out

    def metrics(self, items: list[Item]) -> dict[str, Metrics]:
        uris = [i.subject_uri or i.remote_uri for i in items if i.kind != "repost"]
        uris = [u for u in uris if u]
        raw: dict[str, Metrics] = {}
        for i in range(0, len(uris), 25):
            chunk = uris[i:i + 25]
            res = self._call("app.bsky.feed.getPosts", params={"uris": chunk})
            for p in res.get("posts", []):
                raw[p.get("uri", "")] = Metrics(
                    likes=int(p.get("likeCount") or 0),
                    boosts=int(p.get("repostCount") or 0),
                    replies=int(p.get("replyCount") or 0),
                )
        # A repost's reach is its subject's reach.
        out: dict[str, Metrics] = {}
        for i in items:
            src = i.subject_uri or i.remote_uri
            if src in raw:
                out[i.remote_uri] = raw[src]
        return out

    def delete(self, item) -> None:
        if self.bridge is not None:
            self.bridge.delete(item.remote_id)
            return
        collection = REPOSTS if item.kind == "repost" else POSTS
        self._call("com.atproto.repo.deleteRecord", {
            "repo": self.session["did"],
            "collection": collection,
            "rkey": item.remote_id,
        })

    # --- writing ---------------------------------------------------------
    def post_status(self, text: str) -> str:  # pragma: no cover - direction is Bsky -> Mastodon
        raise RuntimeError("bluesky is a mirror source, not a target")


def _iso(value: str | None) -> str:
    if not value:
        return now_iso()
    return value.replace("Z", "Z") if value.endswith("Z") else value + "Z"


def _alt_text(value: dict) -> str | None:
    embed = value.get("embed") or {}
    images = embed.get("images") or (embed.get("media") or {}).get("images") or []
    alts = [i.get("alt", "").strip() for i in images if i.get("alt")]
    return " / ".join(alts) if alts else None
