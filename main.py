"""CLI: `run`, `dry-run`, `probe`."""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="newsdigest", description="Finite scheduled gaming + good-news digest.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="full pipeline + deliver")
    sub.add_parser("dry-run", help="print digest to terminal; don't mark seen, don't send")
    p = sub.add_parser("probe", help="Step 0: check which sources are reachable and how")
    p.add_argument("--era-forum", action="append", help="ResetEra forum name (repeatable)")
    p.add_argument("--subreddit", action="append", help="subreddit without r/ (repeatable)")
    p.add_argument("--only", action="append", choices=["resetera", "reddit", "goodnews"])
    args = ap.parse_args(argv)

    if args.cmd == "probe":
        from newsdigest.probe import run_probe
        return run_probe(args.era_forum, args.subreddit, set(args.only or []))
    print(f"'{args.cmd}' is not implemented yet (Step 0 only so far).", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
