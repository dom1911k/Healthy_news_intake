"""ResetEra (XenForo 2): forum RSS + listing pages for discovery, thread pages for comments."""
from __future__ import annotations

import math
from datetime import datetime, timezone

from ..feeds import parse_feed
from ..fetch import FetchError
from ..models import Comment, Item
from ..xenforo import canonical_thread_url, parse_forum_index, parse_thread_list, parse_thread_page
from .base import Source, log

BASE = "https://www.resetera.com"


class ResetEra(Source):
    id = "resetera"

    def _forum_urls(self) -> list[tuple[str, str]]:
        forums = self.cfg.get("forums") or [{"name": "Gaming Forum"}]
        need_lookup = [f for f in forums if not f.get("url")]
        index = []
        if need_lookup:
            try:
                resp = self.fetcher.get(BASE + "/forums/")
                index = parse_forum_index(resp.text(), BASE) if resp.ok else []
            except FetchError as e:
                log.warning("ResetEra forum index unavailable: %s", e)
        out = []
        for f in forums:
            url = f.get("url")
            if not url:
                match = next((x for x in index if x.name.lower() == f["name"].lower()), None)
                if match is None:
                    log.warning("ResetEra forum %r not found; set its url in config.yaml", f["name"])
                    continue
                url = match.url
            out.append((f["name"], url if url.endswith("/") else url + "/"))
        return out

    def fetch(self, since: datetime) -> list[Item]:
        items: dict[str, Item] = {}
        for name, forum_url in self._forum_urls():
            source = f"ResetEra · {name}"
            # 1) RSS: titles, authors, creation dates, opening-post text, reply counts
            try:
                resp = self.fetcher.get(forum_url + "index.rss")
                entries = parse_feed(resp.body).entries if resp.ok else []
                if not resp.ok:
                    log.warning("ResetEra RSS %s -> HTTP %s", forum_url, resp.status)
            except Exception as e:  # noqa: BLE001 - one broken feed must not kill the digest
                log.warning("ResetEra RSS %s failed: %s", forum_url, e)
                entries = []
            for e in entries:
                url = canonical_thread_url(e.link)
                items[url] = Item(source=source, title=e.title, url=url, author=e.author,
                                  created_at=e.published, score=None, reply_count=e.comments,
                                  body_excerpt=(e.content or e.summary)[:2500], source_id=self.id)
            # 2) Listing pages: reply counts for more threads than the RSS carries
            for page in range(1, int(self.cfg.get("listing_pages", 2)) + 1):
                url = forum_url + (f"page-{page}" if page > 1 else "")
                try:
                    resp = self.fetcher.get(url)
                except FetchError as e:
                    log.warning("ResetEra listing %s failed: %s", url, e)
                    break
                if not resp.ok:
                    break
                for t in parse_thread_list(resp.text(), BASE):
                    turl = canonical_thread_url(t.url)
                    created = datetime.fromtimestamp(t.started, timezone.utc) if t.started else None
                    if turl in items:
                        if t.replies is not None:
                            items[turl].reply_count = t.replies
                    else:
                        items[turl] = Item(source=source, title=t.title, url=turl, author=t.author,
                                           created_at=created, score=None, reply_count=t.replies,
                                           body_excerpt="", source_id=self.id)
        return [i for i in items.values() if i.created_at is None or i.created_at >= since]

    def passes_engagement(self, item: Item) -> bool:
        return (item.reply_count or 0) >= int(self.cfg.get("min_replies", 50))

    def engagement(self, item: Item) -> float:
        floor = max(2, int(self.cfg.get("min_replies", 50)))
        return min(3.0, max(1.0, math.log(1 + (item.reply_count or 0)) / math.log(1 + floor)))

    def enrich(self, item: Item) -> None:
        n = int(self.cfg.get("comments_per_thread", 10))
        try:
            resp = self.fetcher.get(item.url)
            if not resp.ok:
                return
            first = parse_thread_page(resp.text())
            posts = list(first.posts)
            if first.last_page > 1:
                last = self.fetcher.get(f"{item.url}page-{first.last_page}")
                if last.ok:
                    posts += parse_thread_page(last.text()).posts
        except FetchError as e:
            log.warning("ResetEra thread %s failed: %s", item.url, e)
            return
        if not posts:
            return
        op, replies = posts[0], posts[1:]
        if op.text:
            item.body_excerpt = op.text[:2500]
        item.score = op.reactions
        replies = [p for p in replies if p.text.strip()]
        ranked = sorted(replies, key=lambda p: p.reactions, reverse=True)
        item.top_comments = [Comment(text=p.text[:700], score=p.reactions, author=p.author) for p in ranked[:n]]
