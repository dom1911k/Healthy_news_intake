"""Load config.yaml and interests.md."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: Path | str | None = None) -> dict:
    path = Path(path) if path else ROOT / "config.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_interests(path: Path | str | None = None) -> dict[str, str]:
    """Split interests.md into its '# Gaming' and '# Good news' parts."""
    path = Path(path) if path else ROOT / "interests.md"
    text = Path(path).read_text(encoding="utf-8")
    parts = re.split(r"^#\s+", text, flags=re.M)
    out = {"gaming": "", "good_news": ""}
    for part in parts:
        heading, _, body = part.partition("\n")
        h = heading.strip().lower()
        if h.startswith("gaming"):
            out["gaming"] = body.strip()
        elif h.startswith("good news"):
            out["good_news"] = body.strip()
    return out
