"""Source adapter interface. Adding a source = one new module + one line in sources/__init__.py."""
from __future__ import annotations

import logging
from datetime import datetime

from ..fetch import Fetcher
from ..models import Item

log = logging.getLogger("newsdigest.sources")


class Source:
    id: str = ""
    kind: str = "gaming"  # "gaming" | "good_news"

    def __init__(self, cfg: dict, fetcher: Fetcher):
        self.cfg = cfg
        self.fetcher = fetcher

    def fetch(self, since: datetime) -> list[Item]:
        """Items created after `since`. Must not raise for network problems: log and return []."""
        raise NotImplementedError

    def passes_engagement(self, item: Item) -> bool:
        return True

    def engagement(self, item: Item) -> float:
        """Multiplier >= 1 used for ranking (relevance x engagement)."""
        return 1.0

    def enrich(self, item: Item) -> None:
        """Load body text and top comments for an item that survived filtering. Optional."""
