"""Reddit via OAuth "script" app credentials only (no scraping, no extracted app keys).

Disabled unless enabled in config.yaml AND the four REDDIT_* env vars are set.
"""
from __future__ import annotations

import base64
import math
import os
import time
import urllib.parse
from datetime import datetime, timezone

from ..fetch import FetchError
from ..models import Comment, Item
from .base import Source, log

ENV = ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USERNAME", "REDDIT_PASSWORD"]


class Reddit(Source):
    id = "reddit"

    def __init__(self, cfg, fetcher):
        super().__init__(cfg, fetcher)
        self._token: str | None = None
        self._token_expiry = 0.0

    @staticmethod
    def credentials_present() -> bool:
        return all(os.environ.get(k) for k in ENV)

    def _headers(self) -> dict[str, str] | None:
        ua = f"script:healthy-news-intake:v0.1 (by /u/{os.environ['REDDIT_USERNAME']})"
        if self._token and time.time() < self._token_expiry - 60:
            return {"Authorization": f"bearer {self._token}", "User-Agent": ua}
        auth = base64.b64encode(f"{os.environ['REDDIT_CLIENT_ID']}:{os.environ['REDDIT_CLIENT_SECRET']}".encode()).decode()
        body = urllib.parse.urlencode({"grant_type": "password", "username": os.environ["REDDIT_USERNAME"],
                                       "password": os.environ["REDDIT_PASSWORD"]}).encode()
        resp = self.fetcher.post("https://www.reddit.com/api/v1/access_token", body, headers={
            "Authorization": f"Basic {auth}", "User-Agent": ua,
            "Content-Type": "application/x-www-form-urlencoded"})
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if not data.get("access_token"):
            log.warning("Reddit login failed (HTTP %s, %s); skipping Reddit", resp.status, data.get("error"))
            return None
        self._token = data["access_token"]
        self._token_expiry = time.time() + float(data.get("expires_in", 3600))
        return {"Authorization": f"bearer {self._token}", "User-Agent": ua}

    def _api(self, path: str):
        headers = self._headers()
        if headers is None:
            return None
        resp = self.fetcher.get("https://oauth.reddit.com" + path, headers=headers,
                                check_robots=False, use_cache=False)
        return resp.json() if resp.ok else None

    def fetch(self, since: datetime) -> list[Item]:
        if not self.credentials_present():
            log.info("Reddit enabled in config but REDDIT_* credentials are not set; skipping")
            return []
        items = []
        for sub in self.cfg.get("subreddits", []):
            try:
                data = self._api(f"/r/{sub}/top?t=day&limit=50&raw_json=1")
            except (FetchError, ValueError) as e:
                log.warning("Reddit r/%s failed: %s", sub, e)
                continue
            if not data:
                continue
            for c in data["data"]["children"]:
                p = c["data"]
                created = datetime.fromtimestamp(p["created_utc"], timezone.utc)
                if created < since or p.get("stickied"):
                    continue
                items.append(Item(
                    source=f"Reddit · r/{sub}", title=p["title"],
                    url="https://www.reddit.com" + p["permalink"], author=p.get("author"),
                    created_at=created, score=p.get("score"), reply_count=p.get("num_comments"),
                    body_excerpt=(p.get("selftext") or p.get("url_overridden_by_dest") or "")[:2500],
                    source_id=self.id))
        return items

    def passes_engagement(self, item: Item) -> bool:
        return (item.score or 0) >= int(self.cfg.get("min_score", 300))

    def engagement(self, item: Item) -> float:
        floor = max(2, int(self.cfg.get("min_score", 300)))
        return min(3.0, max(1.0, math.log(1 + (item.score or 0)) / math.log(1 + floor)))

    def enrich(self, item: Item) -> None:
        path = urllib.parse.urlsplit(item.url).path.rstrip("/")
        n = int(self.cfg.get("comments_per_thread", 10))
        try:
            data = self._api(f"{path}?sort=top&limit={n}&depth=1&raw_json=1")
        except (FetchError, ValueError) as e:
            log.warning("Reddit comments %s failed: %s", item.url, e)
            return
        if not data:
            return
        comments = [x["data"] for x in data[1]["data"]["children"] if x["kind"] == "t1"]
        item.top_comments = [Comment(text=c["body"][:700], score=c.get("score"), author=c.get("author"))
                             for c in comments[:n] if c.get("body") not in ("[deleted]", "[removed]")]
