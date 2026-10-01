"""Decide whether a scheduled run should produce a digest now (local time, configured days)."""
from __future__ import annotations

from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _cfg(cfg: dict) -> tuple[ZoneInfo, time, list[int]]:
    s = cfg.get("schedule") or {}
    tz = ZoneInfo(s.get("timezone", "Europe/Berlin"))
    hh, mm = (int(x) for x in str(s.get("time", "07:00")).split(":"))
    days = s.get("days", "daily")
    idx = list(range(7)) if days == "daily" else [DAYS.index(d.lower()[:3]) for d in days]
    return tz, time(hh, mm), idx


def is_due(cfg: dict, now: datetime, last_digest: datetime | None) -> tuple[bool, str]:
    tz, at, days = _cfg(cfg)
    local = now.astimezone(tz)
    if local.weekday() not in days:
        return False, f"{DAYS[local.weekday()]} is not a digest day"
    if local.time() < at:
        return False, f"too early ({local:%H:%M} < {at:%H:%M} {tz.key})"
    if last_digest and last_digest.astimezone(tz).date() == local.date():
        return False, "today's digest was already sent"
    return True, "due"


def next_label(cfg: dict, now: datetime) -> str:
    tz, at, days = _cfg(cfg)
    local = now.astimezone(tz)
    for n in range(1, 8):
        d = local + timedelta(days=n)
        if d.weekday() in days:
            return "tomorrow" if n == 1 else f"{d:%A}"
    return "next week"
