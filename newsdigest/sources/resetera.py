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
        """New threads (by start date) from each forum, newest first, paging back until `since`.

        The forum RSS is ordered by last reply and its dates are last-reply dates, so it is only
        used to borrow opening-post excerpts; discovery and start dates come from the listing.
        """
        since_ts = since.timestamp()
        items: dict[str, Item] = {}
        for name, forum_url in self._forum_urls():
            source = f"ResetEra · {name}"
            excerpts: dict[str, str] = {}
            try:
                resp = self.fetcher.get(forum_url + "index.rss")
                if resp.ok:
                    excerpts = {canonical_thread_url(e.link): (e.content or e.summary)
                                for e in parse_feed(resp.body).entries}
            except Exception as e:  # noqa: BLE001 - excerpts are a nice-to-have
                log.info("ResetEra RSS %s unavailable: %s", forum_url, e)

            for page in range(1, int(self.cfg.get("max_listing_pages", 10)) + 1):
                url = forum_url + (f"page-{page}" if page > 1 else "") + "?order=post_date&direction=desc"
                try:
                    resp = self.fetcher.get(url)
                except FetchError as e:
                    log.warning("ResetEra listing %s failed: %s", url, e)
                    break
                if not resp.ok:
                    log.warning("ResetEra listing %s -> HTTP %s", url, resp.status)
                    break
                threads = [t for t in parse_thread_list(resp.text(), BASE) if not t.sticky and t.started]
                if not threads:
                    if page == 1:
                        log.warning("ResetEra listing %s: no threads parsed (page layout changed?)", url)
                    break
                for t in threads:
                    if t.started < since_ts:
                        continue
                    turl = canonical_thread_url(t.url)
                    items.setdefault(turl, Item(
                        source=source, title=t.title, url=turl, author=t.author,
                        created_at=datetime.fromtimestamp(t.started, timezone.utc), score=None,
                        reply_count=t.replies, body_excerpt=excerpts.get(turl, "")[:2500], source_id=self.id))
                if min(t.started for t in threads) < since_ts:
                    break  # reached threads older than the window
        return list(items.values())

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
        # Rank replies by reactions, plus quotes by other posters (reactions may be hidden from guests).
        replies = [p for p in replies if len(p.text.strip()) >= 25]
        ranked = sorted(replies, key=lambda p: p.reactions + 3 * p.quoted, reverse=True)
        item.top_comments = [Comment(text=p.text[:700], score=p.reactions + 3 * p.quoted, author=p.author)
                             for p in ranked[:n]]
