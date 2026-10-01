"""Minimal RSS 2.0 / Atom parser (stdlib only)."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/elements/1.1/",
    "slash": "http://purl.org/rss/1.0/modules/slash/",
    "content": "http://purl.org/rss/1.0/modules/content/",
}


@dataclass
class FeedEntry:
    title: str
    link: str
    published: datetime | None
    author: str | None
    summary: str  # plain text
    content: str  # plain text of full content, if the feed carries it
    comments: int | None  # slash:comments (XenForo includes this)


@dataclass
class Feed:
    kind: str  # "rss" | "atom"
    title: str
    entries: list[FeedEntry]


def strip_html(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"<(script|style)\b.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    from html import unescape
    return re.sub(r"\s+", " ", unescape(s)).strip()


def _date(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _text(el: ET.Element | None) -> str:
    return (el.text or "").strip() if el is not None else ""


def parse_feed(data: bytes | str) -> Feed:
    root = ET.fromstring(data)
    if root.tag == f"{{{NS['atom']}}}feed":
        entries = []
        for e in root.findall("atom:entry", NS):
            link_el = e.find("atom:link[@rel='alternate']", NS)
            if link_el is None:
                link_el = e.find("atom:link", NS)
            author = e.find("atom:author/atom:name", NS)
            entries.append(FeedEntry(
                title=strip_html(_text(e.find("atom:title", NS))),
                link=link_el.get("href", "") if link_el is not None else "",
                published=_date(_text(e.find("atom:published", NS)) or _text(e.find("atom:updated", NS))),
                author=_text(author) or None,
                summary=strip_html(_text(e.find("atom:summary", NS))),
                content=strip_html(_text(e.find("atom:content", NS))),
                comments=None,
            ))
        return Feed("atom", strip_html(_text(root.find("atom:title", NS))), entries)

    channel = root.find("channel")
    if channel is None:
        raise ValueError(f"not an RSS/Atom document (root <{root.tag}>)")
    entries = []
    for i in channel.findall("item"):
        comments = _text(i.find("slash:comments", NS))
        entries.append(FeedEntry(
            title=strip_html(_text(i.find("title"))),
            link=_text(i.find("link")),
            published=_date(_text(i.find("pubDate")) or _text(i.find("dc:date", NS))),
            author=_text(i.find("dc:creator", NS)) or _text(i.find("author")) or None,
            summary=strip_html(_text(i.find("description"))),
            content=strip_html(_text(i.find("content:encoded", NS))),
            comments=int(comments) if comments.isdigit() else None,
        ))
    return Feed("rss", strip_html(_text(channel.find("title"))), entries)
