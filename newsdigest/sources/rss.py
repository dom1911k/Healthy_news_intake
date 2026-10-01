"""Generic RSS/Atom adapter, used for the good-news feeds."""
from __future__ import annotations

from datetime import datetime

from ..feeds import parse_feed
from ..models import Item
from .base import Source, log


class RSSFeed(Source):
    kind = "good_news"

    def __init__(self, source_id: str, cfg: dict, fetcher):
        super().__init__(cfg, fetcher)
        self.id = source_id
        self.name = cfg.get("name", source_id)

    def fetch(self, since: datetime) -> list[Item]:
        try:
            resp = self.fetcher.get(self.cfg["url"])
            if not resp.ok:
                log.warning("%s feed -> HTTP %s", self.name, resp.status)
                return []
            entries = parse_feed(resp.body).entries
        except Exception as e:  # noqa: BLE001 - one broken feed must not kill the digest
            log.warning("%s feed failed: %s", self.name, e)
            return []
        return [
            Item(source=self.name, title=e.title, url=e.link, author=e.author, created_at=e.published,
                 score=None, reply_count=None, body_excerpt=(e.content or e.summary)[:4000],
                 kind="good_news", source_id=self.id)
            for e in entries
            if e.published is None or e.published >= since
        ]
