"""Step 0: check which sources are actually reachable, and how, before building the pipeline.

Prints a report. Never sends credentials or tokens anywhere except Reddit's own OAuth endpoint,
and never prints them.
"""
from __future__ import annotations

import base64
import os
import urllib.parse
from datetime import datetime, timedelta, timezone

from .feeds import parse_feed
from .fetch import Fetcher, FetchError, RobotsDisallowed
from .xenforo import parse_forum_index, parse_thread_list, parse_thread_page

ERA = "https://www.resetera.com"
DEFAULT_ERA_FORUMS = ["Gaming Forum", "Gaming Hangouts"]
DEFAULT_SUBREDDITS = ["Games", "pcgaming"]

GOOD_NEWS_CANDIDATES = {
    "positive_news": ["https://www.positive.news/feed/"],
    "reasons_to_be_cheerful": ["https://reasonstobecheerful.world/feed/"],
    "guardian_the_upside": [
        "https://www.theguardian.com/world/series/the-upside/rss",
        "https://www.theguardian.com/world/the-upside/rss",
    ],
    "fix_the_news": ["https://fixthenews.com/feed", "https://fixthenews.com/rss", "https://www.fixthenews.com/feed"],
    "good_news_network": ["https://www.goodnewsnetwork.org/feed/"],
}

OK, WARN, FAIL, INFO = "[ OK ]", "[WARN]", "[FAIL]", "[info]"


class Report:
    def __init__(self):
        self.summary: list[tuple[str, str, str]] = []

    def section(self, title: str):
        print(f"\n=== {title} " + "=" * max(0, 66 - len(title)))

    def line(self, mark: str, msg: str):
        print(f"{mark} {msg}")

    def result(self, name: str, mark: str, msg: str):
        self.line(mark, msg)
        self.summary.append((name, mark, msg))


def _ago(d: datetime | None) -> str:
    if d is None:
        return "?"
    h = (datetime.now(timezone.utc) - d).total_seconds() / 3600
    return f"{h:.1f}h ago" if h < 48 else f"{h / 24:.1f}d ago"


def _get(f: Fetcher, rep: Report, url: str, **kw):
    """GET with robots check; logs and returns None on any failure."""
    try:
        resp = f.get(url, **kw)
    except RobotsDisallowed as e:
        rep.line(FAIL, str(e))
        return None
    except FetchError as e:
        rep.line(FAIL, f"network error: {e}")
        return None
    cached = " (cached)" if resp.from_cache else ""
    rep.line(OK if resp.ok else FAIL, f"HTTP {resp.status} {url}{cached}")
    return resp if resp.ok else None


# --------------------------------------------------------------------------- ResetEra

def probe_resetera(f: Fetcher, rep: Report, forum_names: list[str]):
    rep.section("ResetEra (XenForo)")
    rp = f.robots(ERA)
    if rp.disallow_all:
        why = "unreachable (host blocked or down)" if ERA in f.robots_unreachable else "disallows everything"
        rep.result("resetera", FAIL, f"robots.txt {why}; stopping ResetEra probe")
        return
    crawl_delay = rp.crawl_delay(f.user_agent)
    rep.line(INFO, f"robots.txt crawl-delay for us: {crawl_delay or 'none'}")
    for path in ["/forums/", "/forums/x.1/index.rss", "/threads/x.1/", "/threads/x.1/page-2",
                 "/threads/x.1/?order=reaction_score", "/forums/x.1/?order=reply_count&direction=desc"]:
        rep.line(INFO, f"robots allows {path:<48} {f.allowed(ERA + path)}")

    resp = _get(f, rep, ERA + "/forums/")
    forums = parse_forum_index(resp.text(), ERA) if resp else []
    rep.line(INFO, f"discovered {len(forums)} forums: " + "; ".join(f"{x.name} -> {x.url}" for x in forums[:30]))

    best_thread = None
    for name in forum_names:
        forum = next((x for x in forums if x.name.lower() == name.lower()), None)
        if forum is None:
            rep.result(f"resetera:{name}", FAIL, f"forum '{name}' not found in forum index")
            continue
        rep.line(INFO, f"--- {forum.name}")

        # RSS feed
        rss = _get(f, rep, forum.url + "index.rss")
        if rss:
            try:
                feed = parse_feed(rss.body)
            except Exception as e:  # noqa: BLE001 - probe reports anything
                rep.result(f"resetera:{name}:rss", FAIL, f"RSS did not parse: {e}")
            else:
                dates = [e.published for e in feed.entries if e.published]
                with_counts = [e for e in feed.entries if e.comments is not None]
                day_ago = datetime.now(timezone.utc) - timedelta(hours=24)
                rep.result(
                    f"resetera:{name}:rss", OK,
                    f"RSS '{feed.title}': {len(feed.entries)} items, newest {_ago(max(dates, default=None))}, "
                    f"oldest {_ago(min(dates, default=None))}, {sum(d > day_ago for d in dates)} from last 24h, "
                    f"{len(with_counts)} with slash:comments reply count, "
                    f"{sum(bool(e.author) for e in feed.entries)} with author",
                )
                for e in sorted(feed.entries, key=lambda e: e.comments or 0, reverse=True)[:5]:
                    rep.line(INFO, f"   replies={e.comments} {_ago(e.published):>10} {e.title[:70]}")
                if with_counts:
                    top = max(with_counts, key=lambda e: e.comments)
                    if best_thread is None or top.comments > best_thread[1]:
                        best_thread = (top.link, top.comments, top.title)

        # Thread listing page (reply counts without RSS), and server-side sort by replies
        for suffix, label in [("", "listing"), ("?order=reply_count&direction=desc", "listing sorted by replies")]:
            page = _get(f, rep, forum.url + suffix)
            if page:
                threads = parse_thread_list(page.text(), ERA)
                counted = [t for t in threads if t.replies is not None]
                rep.result(f"resetera:{name}:{label}", OK if counted else WARN,
                           f"{label}: {len(threads)} threads parsed, {len(counted)} with reply counts; top: "
                           + "; ".join(f"{t.replies} {t.title[:40]}" for t in threads[:3]))
                if counted and best_thread is None:
                    t = max(counted, key=lambda t: t.replies)
                    best_thread = (t.url, t.replies, t.title)

    if best_thread is None:
        rep.result("resetera:thread", FAIL, "no thread available to probe thread pages")
        return

    url, replies, title = best_thread
    url = url.split("#")[0]
    for tail in ("unread", "latest"):
        if url.rstrip("/").endswith(tail):
            url = url.rstrip("/")[: -len(tail)]
    rep.line(INFO, f"--- thread probe: '{title[:60]}' ({replies} replies) {url}")
    p1 = _get(f, rep, url)
    if not p1:
        rep.result("resetera:thread", FAIL, "could not fetch thread page 1")
        return
    page1 = parse_thread_page(p1.text())
    reacted = [p for p in page1.posts if p.reactions]
    rep.result("resetera:thread:page1", OK if page1.posts else FAIL,
               f"page 1: {len(page1.posts)} posts parsed, {len(reacted)} with reaction counts, "
               f"last page = {page1.last_page}")
    if page1.posts:
        op = page1.posts[0]
        rep.line(INFO, f"   OP by {op.author}: {op.text[:160]!r}")
    for p in sorted(page1.posts[1:], key=lambda p: p.reactions, reverse=True)[:3]:
        rep.line(INFO, f"   +{p.reactions} ({p.reaction_text[:50]!r}) {p.author}: {p.text[:110]!r}")

    sorted_page = _get(f, rep, url + "?order=reaction_score")
    if sorted_page:
        sp = parse_thread_page(sorted_page.text())
        ids1, ids2 = [p.post_id for p in page1.posts], [p.post_id for p in sp.posts]
        counts = [p.reactions for p in sp.posts[1:]]
        is_sorted = ids1 != ids2 and counts == sorted(counts, reverse=True) and any(counts)
        rep.result("resetera:thread:sort", OK if is_sorted else WARN,
                   "?order=reaction_score " + ("WORKS (posts come back sorted by reactions)" if is_sorted
                                               else "has no effect -> fall back to sampling first + last page"))

    if page1.last_page > 1:
        last = _get(f, rep, f"{url.rstrip('/')}/page-{page1.last_page}")
        if last:
            lp = parse_thread_page(last.text())
            rep.result("resetera:thread:lastpage", OK if lp.posts else WARN,
                       f"last page: {len(lp.posts)} posts, {sum(1 for p in lp.posts if p.reactions)} with reactions")


# --------------------------------------------------------------------------- Reddit

def probe_reddit(f: Fetcher, rep: Report, subs: list[str]):
    rep.section("Reddit")
    keys = ["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USERNAME", "REDDIT_PASSWORD"]
    missing = [k for k in keys if not os.environ.get(k)]

    # (a) OAuth script app (password grant). API use is governed by Reddit's API terms, not robots.txt.
    if missing:
        rep.result("reddit:oauth", WARN, f"OAuth skipped: env vars not set: {', '.join(missing)}")
    else:
        username = os.environ["REDDIT_USERNAME"]
        api_ua = f"script:healthy-news-intake:v0.1 (by /u/{username})"
        auth = base64.b64encode(f"{os.environ['REDDIT_CLIENT_ID']}:{os.environ['REDDIT_CLIENT_SECRET']}".encode()).decode()
        body = urllib.parse.urlencode({"grant_type": "password", "username": username,
                                       "password": os.environ["REDDIT_PASSWORD"]}).encode()
        try:
            tok = f.post("https://www.reddit.com/api/v1/access_token", body,
                         headers={"Authorization": f"Basic {auth}", "User-Agent": api_ua,
                                  "Content-Type": "application/x-www-form-urlencoded"})
        except FetchError as e:
            rep.result("reddit:oauth", FAIL, f"token request network error: {e}")
            tok = None
        token = None
        if tok is not None:
            try:
                data = tok.json()
            except ValueError:
                data = {}
            token = data.get("access_token")
            if token:
                rep.line(OK, f"token granted (scope={data.get('scope')!r}, expires_in={data.get('expires_in')})")
            else:
                rep.result("reddit:oauth", FAIL, f"token request HTTP {tok.status}: error={data.get('error')!r} "
                                                 f"(body starts {tok.text()[:120]!r})")
        if token:
            hdrs = {"Authorization": f"bearer {token}", "User-Agent": api_ua}
            sub = subs[0]
            r = _get(f, rep, f"https://oauth.reddit.com/r/{sub}/top?t=day&limit=5&raw_json=1",
                     headers=hdrs, check_robots=False, use_cache=False)
            if r:
                posts = [c["data"] for c in r.json()["data"]["children"]]
                rep.result("reddit:oauth", OK, f"API works: r/{sub} top/day returned {len(posts)} posts "
                                               f"(ratelimit remaining={r.headers.get('x-ratelimit-remaining')})")
                for p in posts:
                    rep.line(INFO, f"   score={p['score']} comments={p['num_comments']} {p['title'][:70]}")
                if posts:
                    c = _get(f, rep, f"https://oauth.reddit.com/r/{sub}/comments/{posts[0]['id']}"
                                     f"?sort=top&limit=5&depth=1&raw_json=1",
                             headers=hdrs, check_robots=False, use_cache=False)
                    if c:
                        comments = [x["data"] for x in c.json()[1]["data"]["children"] if x["kind"] == "t1"]
                        rep.line(OK, f"top comments endpoint: {len(comments)} comments with scores")
                        for x in comments[:3]:
                            rep.line(INFO, f"   +{x['score']} {x['body'][:100]!r}")
            else:
                rep.result("reddit:oauth", FAIL, "token granted but API listing request failed")

    # (b) Public RSS. This is crawling, so robots.txt applies.
    f.robots("https://www.reddit.com/")
    for sub in subs:
        url = f"https://www.reddit.com/r/{sub}/top/.rss?t=day"
        if not f.allowed(url):
            why = ("robots.txt unreachable (host blocked or down)" if "https://www.reddit.com" in f.robots_unreachable
                   else "robots.txt disallows it for our User-Agent")
            rep.result(f"reddit:rss:{sub}", FAIL, f"{why} -> not fetching {url}")
            continue
        r = _get(f, rep, url)
        if not r:
            rep.result(f"reddit:rss:{sub}", FAIL, "RSS request failed")
            continue
        try:
            feed = parse_feed(r.body)
        except Exception as e:  # noqa: BLE001
            rep.result(f"reddit:rss:{sub}", FAIL, f"RSS did not parse ({e}); body starts {r.text()[:120]!r}")
            continue
        rep.result(f"reddit:rss:{sub}", OK if feed.entries else WARN,
                   f"RSS r/{sub}: {len(feed.entries)} entries (no score / comment count in RSS)")
        for e in feed.entries[:3]:
            rep.line(INFO, f"   {_ago(e.published):>10} {e.title[:70]}")


# --------------------------------------------------------------------------- Good news

def probe_good_news(f: Fetcher, rep: Report):
    rep.section("Good news feeds")
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    for name, urls in GOOD_NEWS_CANDIDATES.items():
        for url in urls:
            r = _get(f, rep, url)
            if not r:
                continue
            try:
                feed = parse_feed(r.body)
            except Exception as e:  # noqa: BLE001
                rep.line(FAIL, f"{url} did not parse as a feed: {e}")
                continue
            dates = [e.published for e in feed.entries if e.published]
            n_full = sum(len(e.content) > 1500 for e in feed.entries)
            avg = sum(len(e.content or e.summary) for e in feed.entries) // max(1, len(feed.entries))
            rep.result(f"good_news:{name}", OK if feed.entries else WARN,
                       f"{name}: {url} -> '{feed.title}', {feed.kind}, {len(feed.entries)} items, "
                       f"newest {_ago(max(dates, default=None))}, {sum(d > week_ago for d in dates)} in last 7d, "
                       f"{n_full} with full text, avg text {avg} chars")
            for e in feed.entries[:3]:
                rep.line(INFO, f"   {_ago(e.published):>10} {e.title[:80]}")
            break
        else:
            rep.result(f"good_news:{name}", FAIL, f"{name}: no working feed among {urls}")


# --------------------------------------------------------------------------- entry point

def run_probe(era_forums: list[str] | None = None, subreddits: list[str] | None = None,
              only: set[str] | None = None) -> int:
    f = Fetcher()
    rep = Report()
    print(f"User-Agent: {f.user_agent}\nmin interval {f.min_interval}s per host, cache {f.cache_ttl:.0f}s in {f.cache_dir}")
    if not only or "resetera" in only:
        probe_resetera(f, rep, era_forums or DEFAULT_ERA_FORUMS)
    if not only or "reddit" in only:
        probe_reddit(f, rep, subreddits or DEFAULT_SUBREDDITS)
    if not only or "goodnews" in only:
        probe_good_news(f, rep)

    rep.section("Summary")
    for name, mark, msg in rep.summary:
        print(f"{mark} {name:<34} {msg[:110]}")
    return 0
