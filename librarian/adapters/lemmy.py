"""Lemmy: log in for a JWT, list own posts, tombstone them on expiry."""

from __future__ import annotations

from .base import Adapter, Item, Metrics


class Lemmy(Adapter):
    platform = "lemmy"

    def __init__(self, account, identifier: str, secret: str, base: str | None = None):
        super().__init__(account, identifier, secret)
        self.base = (base or f"https://{account.instance}").rstrip("/")
        self._jwt: str | None = None
        self._person_id: int | None = None

    # --- auth ------------------------------------------------------------
    def _login(self) -> str:
        from ..http import request_json

        res = request_json("POST", f"{self.base}/api/v3/user/login",
                           {"username_or_email": self.identifier, "password": self.secret})
        jwt = res.get("jwt")
        if not jwt:
            raise RuntimeError("lemmy login returned no jwt")
        self._jwt = jwt
        return jwt

    @property
    def jwt(self) -> str:
        return self._jwt or self._login()

    def _h(self) -> dict:
        return {"Authorization": f"Bearer {self.jwt}"}

    def whoami(self) -> dict:
        from ..http import request_json

        site = request_json("GET", f"{self.base}/api/v3/site", None, self._h())
        person = (((site.get("my_user") or {}).get("local_user_view") or {}).get("person") or {})
        if not person.get("id"):
            raise RuntimeError("lemmy site call returned no local user")
        self._person_id = int(person["id"])
        return {"name": person.get("name"), "id": self._person_id, "version": site.get("version")}

    # --- reading ---------------------------------------------------------
    def list_own(self, since: str | None = None) -> list[Item]:
        from ..http import request_json

        if self._person_id is None:
            self.whoami()
        items: list[Item] = []
        for page in range(1, 21):  # hard stop: 20 * 50 = 1000 posts
            url = (f"{self.base}/api/v3/user?person_id={self._person_id}"
                   f"&limit=50&sort=New&page={page}")
            res = request_json("GET", url, None, self._h())
            rows = (res or {}).get("posts") or []
            if not rows:
                break
            for r in rows:
                p = r.get("post") or {}
                counts = r.get("counts") or {}
                if not p.get("id"):
                    continue
                created = p.get("published", "")
                if since and created < since:
                    continue
                items.append(Item(
                    platform=self.platform,
                    instance=self.account.instance,
                    account=self.account.handle,
                    remote_id=str(p["id"]),
                    remote_uri=p.get("ap_id") or f"{self.base}/post/{p['id']}",
                    kind="post",
                    text=(p.get("name") or "") + (("\n\n" + (p.get("body") or "")) if p.get("body") else ""),
                    url=p.get("ap_id"),
                    created_at=created,
                    metrics=Metrics(
                        score=float(counts.get("score") or 0),
                        comments=int(counts.get("comments") or 0),
                        likes=int(counts.get("upvotes") or 0),
                    ),
                ))
            if len(rows) < 50:
                break
        return items

    def metrics(self, items: list[Item]) -> dict[str, Metrics]:
        """Lemmy has no batch endpoint for arbitrary post ids; re-list and match."""
        fresh = {i.remote_id: i.metrics for i in self.list_own(None)}
        return {i.remote_uri: fresh[i.remote_id] for i in items if i.remote_id in fresh}

    # --- writing ---------------------------------------------------------
    def delete(self, item) -> None:
        from ..http import request_json

        request_json("PUT", f"{self.base}/api/v3/post",
                     {"post_id": int(item.remote_id), "deleted": True}, self._h())

    def post_status(self, text: str) -> str:  # pragma: no cover - not a mirror target today
        raise RuntimeError("lemmy is not a mirror target")
