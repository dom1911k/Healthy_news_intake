"""Source registry. To add a source: write an adapter module and register it here."""
from __future__ import annotations

from ..fetch import Fetcher
from .base import Source, log
from .reddit import Reddit
from .resetera import ResetEra
from .rss import RSSFeed

GAMING_SOURCES = {"resetera": ResetEra, "reddit": Reddit}


def build_gaming_sources(cfg: dict, fetcher: Fetcher) -> list[Source]:
    out = []
    for key, cls in GAMING_SOURCES.items():
        scfg = cfg.get(key) or {}
        if scfg.get("enabled", False):
            out.append(cls(scfg, fetcher))
    return out


def build_good_news_sources(cfg: dict, fetcher: Fetcher) -> list[Source]:
    feeds = (cfg.get("good_news") or {}).get("sources") or {}
    return [RSSFeed(key, scfg, fetcher) for key, scfg in feeds.items() if scfg.get("enabled", True)]
