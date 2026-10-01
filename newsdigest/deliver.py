"""Render the digest (HTML + plain text) and deliver it: a single HTML file, and email if configured."""
from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import ROOT
from .models import Digest
from .schedule import next_label

log = logging.getLogger("newsdigest.deliver")


def _date_label(d: Digest, cfg: dict) -> str:
    from zoneinfo import ZoneInfo
    tz = ZoneInfo((cfg.get("schedule") or {}).get("timezone", "Europe/Berlin"))
    local = d.created_at.astimezone(tz)
    return f"{local:%A}, {local.day} {local:%B %Y}"


def render_html(d: Digest, cfg: dict) -> str:
    env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                      autoescape=select_autoescape(["html"]))
    return env.get_template("digest.html").render(
        stories=d.stories, good_news=d.good_news, date_label=_date_label(d, cfg),
        next_label=next_label(cfg, d.created_at))


def render_text(d: Digest, cfg: dict) -> str:
    out = [f"GAMING DIGEST · {_date_label(d, cfg)}", ""]
    if not d.stories:
        out.append("Nothing notable today.")
    for n, s in enumerate(d.stories, 1):
        m = s.summary
        out += [f"{n}. {m['headline']}", f"   {m['what_happened']}",
                f"   Community: {m['community_consensus']}"]
        if m.get("dissent"):
            out.append(f"   Dissent: {m['dissent']}")
        out += [f"   {s.primary.source} · {s.primary.url}", ""]
    if d.good_news:
        out += ["", "GOOD NEWS", ""]
        for s in d.good_news:
            m = s.summary
            out += [f"* {m['headline']}", f"  {m['what_happened']}", f"  Why it matters: {m['why_it_matters']}",
                    f"  Evidence: {m['evidence_strength']} · {s.primary.source} · {s.primary.url}", ""]
    out += ["", f"That's everything. Next digest: {next_label(cfg, d.created_at)}."]
    return "\n".join(out)


def _subject(d: Digest, cfg: dict) -> str:
    n = len(d.stories)
    count = "nothing notable" if n == 0 else f"{n} {'story' if n == 1 else 'stories'}"
    extra = f" + {len(d.good_news)} good news" if d.good_news else ""
    return f"Gaming digest · {_date_label(d, cfg)} · {count}{extra}"


def send_email(d: Digest, cfg: dict, html: str, text: str) -> bool:
    user, password = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
    if not (user and password):
        log.info("email not configured (SMTP_USER / SMTP_PASSWORD missing); skipping email")
        return False
    host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
    port = int(os.environ.get("SMTP_PORT") or 465)
    msg = EmailMessage()
    msg["Subject"] = _subject(d, cfg)
    msg["From"] = user
    msg["To"] = os.environ.get("DIGEST_TO") or user
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=60) as s:
            s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=60) as s:
            s.starttls(context=ctx)
            s.login(user, password)
            s.send_message(msg)
    log.info("emailed digest to %s", msg["To"])
    return True


def deliver(d: Digest, cfg: dict) -> str:
    html, text = render_html(d, cfg), render_text(d, cfg)
    out_dir = ROOT / (cfg.get("delivery") or {}).get("output_dir", "out")
    out_dir.mkdir(parents=True, exist_ok=True)
    # One file, overwritten each time: no archive to browse.
    (out_dir / "digest.html").write_text(html, encoding="utf-8")
    via = ["file"]
    if (cfg.get("delivery") or {}).get("email", True) and send_email(d, cfg, html, text):
        via.append("email")
    return "+".join(via)
