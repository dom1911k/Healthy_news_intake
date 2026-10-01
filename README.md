# healthy_news_intake

A personal, **finite** gaming-news digest (ResetEra, optionally Reddit) plus an optional "Good news" section.
It runs on a schedule and produces exactly one digest. There is no on-demand refresh, no "load more" and no archive UI.
A short or empty digest is a valid outcome.

Status: **Step 0 — source probes.** The pipeline isn't built yet.

## Step 0: probe source access

Needs Python 3.12 or newer. The probe uses only the standard library, so there is nothing to install.

```bash
python main.py probe                       # everything
python main.py probe --only resetera
python main.py probe --era-forum "Gaming Forum" --era-forum "Gaming Hangouts"
python main.py probe --only reddit --subreddit Games --subreddit pcgaming
```

Reddit OAuth is only tested when all four environment variables are set:
`REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USERNAME`, `REDDIT_PASSWORD`
(a "script" app on your own account). Credentials go only to Reddit's token endpoint and are never printed.

What it checks:

- **ResetEra:** robots.txt rules and crawl delay; the list of forums (names and URLs for `config.yaml`); each forum's
  `index.rss` (item count, how far back it goes, reply count through `slash:comments`); reply counts on the forum
  listing and whether it can be sorted by replies; for the busiest thread, the posts and reaction counts on page 1,
  whether `?order=reaction_score` sorts posts, and the last page.
- **Reddit:** (a) the OAuth password grant and the `top` listing plus top comments; (b) the public
  `/r/<sub>/top/.rss?t=day` feed, fetched only if robots.txt allows it.
- **Good news:** Positive News, Reasons to be Cheerful, Guardian "The Upside", Fix the News and Good News Network.
  For each feed: whether it parses, how fresh it is, and whether it carries the full text.

Fetching is polite. Requests send a descriptive User-Agent and wait at least 2s per host, or longer if robots.txt
sets a Crawl-delay. Failed requests are retried with backoff. Responses are cached in `.cache/http/` for 1 hour, so
reruns don't hit the sites again.
