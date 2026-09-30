"""The arithmetic of per-user limits. PURE: no DB, no settings.

A limit of 0 means off. A limit of N allows N actions: the check runs BEFORE
the action, so the (N+1)th is the first refused. The day window is UTC so
every client shares one reset time the message can name.
"""

from datetime import datetime, timedelta, timezone


def is_over(used: int, limit: int) -> bool:
    return limit > 0 and used >= limit


def day_start(now: datetime) -> datetime:
    utc = now.astimezone(timezone.utc)
    return utc.replace(hour=0, minute=0, second=0, microsecond=0)


def next_reset(now: datetime) -> datetime:
    return day_start(now) + timedelta(days=1)
