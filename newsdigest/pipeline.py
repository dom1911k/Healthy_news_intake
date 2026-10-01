"""fetch -> drop seen -> engagement threshold -> relevance (Haiku) -> cluster -> rank/cap -> summarize (Sonnet)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from .db import DB
from .llm import LLM
from .models import Digest, Item, Story
from .sources import Source, build_gaming_sources, build_good_news_sources

log = logging.getLogger("newsdigest.pipeline")


def _collect(sources: list[Source], since: datetime, db: DB, stats: dict, prefix: str) -> list[tuple[Item, Source]]:
    out = []
    for src in sources:
        fetched = src.fetch(since)
        unseen = [i for i in fetched if not db.is_seen(i.key)]
        engaged = [i for i in unseen if src.passes_engagement(i)]
        stats[f"{prefix}{src.id}"] = f"{len(fetched)} fetched, {len(unseen)} new, {len(engaged)} past engagement threshold"
        log.info("%s: %s", src.id, stats[f"{prefix}{src.id}"])
        out += [(i, src) for i in engaged]
    return out


def _select(pairs: list[tuple[Item, Source]], llm: LLM, interests: str, kind: str,
            min_relevance: float, per_source_min: dict[str, float], cap: int) -> tuple[list[Story], list[Item]]:
    """Score, filter, cluster and rank. Returns (top stories, items that were scored)."""
    items = [i for i, _ in pairs]
    llm.score(items, interests, kind)
    scored = [i for i in items if i.relevance is not None]
    by_item = {id(i): s for i, s in pairs}
    kept = []
    for i in scored:
        if i.relevance >= per_source_min.get(i.source_id, min_relevance):
            i.engagement = by_item[id(i)].engagement(i)
            kept.append(i)
    log.info("%s: %d scored, %d kept", kind, len(scored), len(kept))
    for i in sorted(scored, key=lambda i: -i.relevance):
        log.debug("  %4.1f %s | %s | %s", i.relevance, i.source, i.title[:70], i.relevance_reason)

    rank = lambda i: i.relevance * i.engagement  # noqa: E731
    kept.sort(key=rank, reverse=True)
    kept = kept[: cap * 4]  # bound the clustering prompt
    stories = []
    for group in llm.cluster(kept):
        group.sort(key=rank, reverse=True)
        stories.append(Story(items=group, rank=rank(group[0])))
    stories.sort(key=lambda s: s.rank, reverse=True)
    return stories[:cap], scored


def _summarize(stories: list[Story], sources: dict[str, Source], summarize) -> list[Story]:
    out, errors = [], []
    for story in stories:
        for it in story.items:
            if it.source_id in sources:
                sources[it.source_id].enrich(it)
        try:
            story.summary = summarize(story)
        except Exception as e:  # noqa: BLE001 - one failed summary drops one story, not the digest
            log.warning("summary failed for %r: %s", story.primary.title, e)
            errors.append(e)
            continue
        out.append(story)
    if stories and not out:
        raise RuntimeError(f"every summary failed: {errors[0]}") from errors[0]
    return out


def good_news_due(cfg: dict, db: DB, now: datetime) -> bool:
    gcfg = cfg.get("good_news") or {}
    if not gcfg.get("enabled", True) or not gcfg.get("sources"):
        return False
    last = db.last_good_news_at()
    if last is None:
        return True
    gap = timedelta(hours=20) if gcfg.get("frequency", "weekly") == "daily" else timedelta(days=6, hours=12)
    return now - last >= gap


def build_digest(cfg: dict, interests: dict[str, str], db: DB, fetcher, llm: LLM, now: datetime,
                 force_good_news: bool = False) -> tuple[Digest, list[Item]]:
    """Returns the digest and the items to mark as seen if it gets delivered."""
    stats: dict = {}
    to_mark: list[Item] = []

    # Gaming
    gsources = build_gaming_sources(cfg, fetcher)
    since = now - timedelta(hours=float(cfg.get("lookback_hours", 36)))
    pairs = _collect(gsources, since, db, stats, "")
    sel = cfg.get("selection") or {}
    stories, scored = _select(pairs, llm, interests["gaming"], "gaming",
                              float(sel.get("min_relevance", 6)), {}, int(sel.get("max_stories", 8)))
    to_mark += scored
    stories = _summarize(stories, {s.id: s for s in gsources}, llm.summarize_gaming)

    # Good news
    good: list[Story] | None = None
    if force_good_news or good_news_due(cfg, db, now):
        gcfg = cfg["good_news"]
        nsources = build_good_news_sources(cfg, fetcher)
        days = 1.5 if gcfg.get("frequency", "weekly") == "daily" else 8
        npairs = _collect(nsources, now - timedelta(days=days), db, stats, "good_news:")
        per_source = {k: float(v["min_relevance"]) for k, v in gcfg["sources"].items() if "min_relevance" in v}
        good, nscored = _select(npairs, llm, interests["good_news"], "good_news",
                                float(gcfg.get("min_relevance", 7)), per_source, int(gcfg.get("max_stories", 3)))
        to_mark += nscored
        good = _summarize(good, {s.id: s for s in nsources}, llm.summarize_good_news)

    return Digest(created_at=now, stories=stories, good_news=good, stats=stats), to_mark
