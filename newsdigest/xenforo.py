"""Parsers for XenForo 2 HTML (ResetEra). Stdlib only, tolerant of markup drift."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser


def parse_count(s: str) -> int | None:
    """'1,234' -> 1234, '1.2K' -> 1200, '3M' -> 3000000."""
    s = s.strip().replace(",", "")
    m = re.fullmatch(r"([\d.]+)\s*([KkMm]?)", s)
    if not m:
        return None
    n = float(m.group(1))
    n *= {"": 1, "k": 1_000, "m": 1_000_000}[m.group(2).lower()]
    return int(n)


@dataclass
class ForumLink:
    name: str
    url: str  # absolute


def parse_forum_index(html: str, base: str) -> list[ForumLink]:
    seen: dict[str, ForumLink] = {}
    for href, name in re.findall(r'<a href="(/forums/[a-z0-9-]+\.\d+/)"[^>]*>([^<]+)</a>', html):
        name = unescape(name).strip()
        if name and href not in seen:
            seen[href] = ForumLink(name, base.rstrip("/") + href)
    return list(seen.values())


@dataclass
class ThreadListing:
    title: str
    url: str
    replies: int | None


def parse_thread_list(html: str, base: str) -> list[ThreadListing]:
    out = []
    for chunk in re.split(r'(?=<div class="structItem structItem--thread)', html)[1:]:
        m = re.search(r'<div class="structItem-title">.*?<a href="(/threads/[^"]+?)"[^>]*>([^<]+)</a>', chunk, re.S)
        if not m:
            continue
        r = re.search(r"<dt>Replies</dt>\s*<dd>([^<]+)</dd>", chunk)
        out.append(ThreadListing(unescape(m.group(2)).strip(), base.rstrip("/") + m.group(1),
                                 parse_count(r.group(1)) if r else None))
    return out


@dataclass
class Post:
    post_id: str
    author: str
    text: str = ""
    reactions: int = 0
    reaction_text: str = ""


@dataclass
class ThreadPage:
    posts: list[Post] = field(default_factory=list)
    last_page: int = 1


class _ThreadParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.posts: list[Post] = []
        self.post: Post | None = None
        self.article_depth = 0
        self.bb_depth = 0  # >0 while inside div.bbWrapper
        self.quote_depth = 0
        self.skip_depth = 0  # script/style
        self.in_reactions = False
        self.reaction_names = 0
        self._text: list[str] = []
        self._rtext: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag == "article":
            if self.post is None and "message--post" in cls:
                pid = (a.get("data-content") or a.get("id") or "").rsplit("-", 1)[-1]
                self.post = Post(post_id=pid, author=a.get("data-author", ""))
                self.article_depth = 1
                self._text, self._rtext, self.reaction_names = [], [], 0
            elif self.post is not None:
                self.article_depth += 1
            return
        if self.post is None:
            return
        if tag in ("script", "style"):
            self.skip_depth += 1
        elif tag == "div":
            if self.bb_depth:
                self.bb_depth += 1
            elif "bbWrapper" in cls.split():
                self.bb_depth = 1
        elif tag == "blockquote" and self.bb_depth:
            self.quote_depth += 1
        elif tag == "a" and "reactionsBar-link" in cls:
            self.in_reactions = True
        elif tag == "bdi" and self.in_reactions:
            self.reaction_names += 1
        elif tag in ("br", "p") and self.bb_depth and not self.quote_depth:
            self._text.append("\n")

    def handle_endtag(self, tag):
        if self.post is None:
            return
        if tag == "article":
            self.article_depth -= 1
            if self.article_depth == 0:
                self._finish()
        elif tag in ("script", "style"):
            self.skip_depth = max(0, self.skip_depth - 1)
        elif tag == "div" and self.bb_depth:
            self.bb_depth -= 1
        elif tag == "blockquote" and self.quote_depth:
            self.quote_depth -= 1
        elif tag == "a" and self.in_reactions:
            self.in_reactions = False

    def handle_data(self, data):
        if self.post is None or self.skip_depth:
            return
        if self.in_reactions:
            self._rtext.append(data)
        elif self.bb_depth and not self.quote_depth:
            self._text.append(data)

    def _finish(self):
        p = self.post
        p.text = re.sub(r"[ \t]+", " ", "".join(self._text))
        p.text = re.sub(r"\s*\n\s*", "\n", p.text).strip()
        p.reaction_text = re.sub(r"\s+", " ", "".join(self._rtext)).strip()
        others = re.search(r"and\s+([\d,.]+[KkMm]?)\s+others?", p.reaction_text)
        p.reactions = self.reaction_names + ((parse_count(others.group(1)) or 0) if others else 0)
        self.posts.append(p)
        self.post = None


def parse_thread_page(html: str) -> ThreadPage:
    parser = _ThreadParser()
    parser.feed(html)
    pages = [int(n) for n in re.findall(r'href="[^"]*/page-(\d+)[^"]*"', html)]
    return ThreadPage(parser.posts, max(pages, default=1))
