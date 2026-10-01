# healthy_news_intake

A personal, **finite** gaming-news digest. Instead of browsing ResetEra and Reddit, you get one email
on a schedule. It holds up to 8 short stories, each with a summary of what happened and how the community
reacted, plus an optional weekly "Good news" section. Then it ends. There is no "load more", no archive and
no app to open. A short or empty digest is a normal result.

The digest runs on GitHub's servers through GitHub Actions. You don't need to install anything or keep
a computer on.

---

## Setup (about 10 minutes, no coding)

### 1. Get an Anthropic API key (needed)

The app uses Claude to decide what's relevant and to write the summaries.

1. Go to <https://console.anthropic.com>, sign in, and add a little credit under **Billing**.
   A daily digest costs a few cents per run.
2. Go to **API keys** → **Create key** and copy the key (it starts with `sk-ant-`).

### 2. Get an email app password (needed for email delivery)

The example below uses Gmail. Other providers work too; see the table below.

1. Your Google account needs 2-step verification turned on.
2. Go to <https://myaccount.google.com/apppasswords>, create an app password named "digest",
   and copy the 16-character password.

### 3. Store both as secrets in GitHub

In this repository on GitHub, go to **Settings** → **Secrets and variables** → **Actions**
→ **New repository secret**. Add each secret below, one at a time:

| Name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | the key from step 1 |
| `SMTP_USER` | your Gmail address |
| `SMTP_PASSWORD` | the app password from step 2 |
| `DIGEST_TO` | *(optional)* the address that receives the digest; defaults to `SMTP_USER` |
| `SMTP_HOST`, `SMTP_PORT` | *(optional, only for non-Gmail)* e.g. `smtp.mail.me.com` / `587` |

GitHub keeps secrets hidden. Nobody can read them back, not even you.

### 4. Try it

1. Open the **Actions** tab → **Digest** (left side) → **Run workflow**.
2. Choose a mode:
   - **dry-run** builds a digest without emailing it or remembering what it showed. When it finishes, open the run
     and download **digest** at the bottom of the page. That download is a zip with the page inside.
   - **run** builds the digest and emails it right away. Threads it shows are remembered and won't repeat.
   - **probe** checks which news sources are reachable. It doesn't need the API key.
3. A run takes about 2–5 minutes. A green tick means it worked. A red cross means something failed;
   click into the run to see what.

After this, the digest arrives by itself every day around **07:00 Berlin time**.

---

## Changing things

Everything is in two files, and you can edit both on GitHub: open the file, click the pencil icon, then **Commit changes**.

- **`interests.md`** says what you want and what to skip, in plain language. Add the games you play under WANT.
  This file has the biggest effect on what you get.
- **`config.yaml`** holds the settings:
  - `schedule` sets the time and days, e.g. `days: [mon, wed, fri]`.
  - `selection` sets how strict the filter is (`min_relevance`) and the most stories per digest (`max_stories`).
  - `resetera` lists which forums to read and the minimum reply count.
  - `good_news` turns the section on or off, sets it to `weekly` or `daily`, and lists its sources.
  - `reddit` stays off unless you have Reddit API credentials (see below).

## Reddit

Reddit no longer gives out API access freely, and its robots.txt asks automated tools not to crawl it.
The app therefore only uses Reddit with official API credentials from your own Reddit "script" app. If you
have them, add `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USERNAME` and `REDDIT_PASSWORD` as
secrets, then set `reddit: enabled: true` in `config.yaml`. Without them, the digest uses ResetEra only.

## How it works

1. **Fetch** recent threads from the ResetEra forums you list, plus recent articles from the good-news feeds.
2. **Skip** anything already seen in an earlier digest.
3. **Engagement filter:** drop threads with fewer than 50 replies (configurable).
4. **Relevance filter:** Claude Haiku scores each thread 0–10 against `interests.md`. Only threads scoring 6 or
   more are kept.
5. **Merge** threads that cover the same story.
6. **Summarize:** for the top stories, the app reads the opening post and the most-reacted replies, and
   Claude Sonnet writes a neutral summary: headline, what happened, the community's take and one dissenting view.
7. **Send** one email. The page also says when the next digest comes.

The fetcher is polite to the sites it reads. It sends a descriptive User-Agent, waits at least 2 seconds between
requests, follows robots.txt, retries carefully after errors, and caches pages for an hour.

## Running on your own computer (optional)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python main.py dry-run      # print a digest; send nothing
python main.py run          # build and deliver (writes out/digest.html, plus email if SMTP_* is set)
python main.py probe        # check source access
python -m unittest          # offline tests
```

With cron on Linux, `7 * * * * cd /path/to/repo && python main.py run --scheduled` is enough.
The app works out the local time and days from `config.yaml`, and sends at most once a day.

## Notes

- Memory of already-seen threads is kept in GitHub's Actions cache. If that cache is ever cleared,
  the next digest might repeat a story or two. Nothing else is affected.
- On a **public** repository, GitHub pauses scheduled runs after 60 days without any commits.
  Editing `interests.md` now and then is enough to keep it running.
- The **Run workflow** button is there for testing. To make the digest strictly scheduled, delete the
  `workflow_dispatch:` section from `.github/workflows/digest.yml`.
