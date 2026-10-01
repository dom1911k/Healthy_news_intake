"""Common data types shared by sources, pipeline and delivery."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Comment:
    text: str
    score: int | None = None
    author: str | None = None


@dataclass
class Item:
    source: str  # display name, e.g. "ResetEra · Gaming Forum"
    title: str
    url: str
    author: str | None
    created_at: datetime | None
    score: int | None  # upvotes / reactions on the post itself, if known
    reply_count: int | None
    body_excerpt: str
    top_comments: list[Comment] = field(default_factory=list)
    kind: str = "gaming"  # "gaming" | "good_news"
    source_id: str = ""  # config key of the source, e.g. "resetera", "good_news_network"
    # filled in by the pipeline
    relevance: float | None = None
    relevance_reason: str = ""
    engagement: float = 1.0

    @property
    def key(self) -> str:
        return self.url.split("#")[0].rstrip("/")


@dataclass
class Story:
    """One digest entry: a cluster of items about the same thing, plus its summary."""
    items: list[Item]
    rank: float = 0.0
    summary: dict = field(default_factory=dict)

    @property
    def primary(self) -> Item:
        return self.items[0]


@dataclass
class Digest:
    created_at: datetime
    stories: list[Story]
    good_news: list[Story] | None  # None = section not due this time
    stats: dict = field(default_factory=dict)
