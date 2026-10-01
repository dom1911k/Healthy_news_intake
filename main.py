"""CLI: `run`, `dry-run`, `probe`."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone


def _build(args):
    from newsdigest.config import ROOT, load_config, load_interests
    from newsdigest.db import DB

    cfg = load_config(args.config)
    db = DB(ROOT / (cfg.get("state_db") or "state/digest.sqlite3"))
    return cfg, load_interests(), db


def _make_digest(cfg, interests, db, now, force_good_news):
    from newsdigest.fetch import Fetcher
    from newsdigest.llm import LLM
    from newsdigest.pipeline import build_digest

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set. See README.md, 'Setup'.")
    fetcher = Fetcher(min_interval=float((cfg.get("fetch") or {}).get("min_interval_seconds", 2)))
    return build_digest(cfg, interests, db, fetcher, LLM(cfg.get("llm") or {}), now, force_good_news)


def cmd_run(args) -> int:
    from newsdigest.deliver import deliver
    from newsdigest.schedule import is_due

    cfg, interests, db = _build(args)
    now = datetime.now(timezone.utc)
    if args.scheduled:
        due, reason = is_due(cfg, now, db.last_digest_at())
        if not due:
            print(f"Not sending a digest now: {reason}.")
            return 0
    digest, to_mark = _make_digest(cfg, interests, db, now, args.force_good_news)
    via = deliver(digest, cfg)
    db.mark_seen(to_mark)
    db.record_digest(len(digest.stories), None if digest.good_news is None else len(digest.good_news), via)
    print(f"Digest delivered via {via}: {len(digest.stories)} stories"
          + ("" if digest.good_news is None else f", {len(digest.good_news)} good news"))
    return 0


def cmd_dry_run(args) -> int:
    from newsdigest.config import ROOT
    from newsdigest.deliver import render_html, render_text

    cfg, interests, db = _build(args)
    digest, _ = _make_digest(cfg, interests, db, datetime.now(timezone.utc), args.force_good_news)
    print("\n" + render_text(digest, cfg))
    print("\n--- pipeline stats ---")
    for k, v in digest.stats.items():
        print(f"{k}: {v}")
    out = ROOT / (cfg.get("delivery") or {}).get("output_dir", "out")
    out.mkdir(parents=True, exist_ok=True)
    (out / "dry-run.html").write_text(render_html(digest, cfg), encoding="utf-8")
    print(f"\n(HTML preview written to {out / 'dry-run.html'}; nothing sent, nothing marked as seen)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="newsdigest", description="Finite scheduled gaming + good-news digest.")
    ap.add_argument("--config", help="path to config.yaml")
    ap.add_argument("-v", "--verbose", action="store_true", help="show every scored item")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="full pipeline + deliver")
    r.add_argument("--scheduled", action="store_true",
                   help="only send if today is a digest day, it's past the configured time, and none was sent today")
    d = sub.add_parser("dry-run", help="print digest to terminal; don't mark seen, don't send")
    for p in (r, d):
        p.add_argument("--force-good-news", action="store_true", help="include the Good news section even if not due")
    p = sub.add_parser("probe", help="check which sources are reachable and how")
    p.add_argument("--era-forum", action="append", help="ResetEra forum name (repeatable)")
    p.add_argument("--subreddit", action="append", help="subreddit without r/ (repeatable)")
    p.add_argument("--only", action="append", choices=["resetera", "reddit", "goodnews"])
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if args.cmd == "probe":
        from newsdigest.probe import run_probe
        return run_probe(args.era_forum, args.subreddit, set(args.only or []))
    if args.cmd == "run":
        return cmd_run(args)
    return cmd_dry_run(args)


if __name__ == "__main__":
    sys.exit(main())
