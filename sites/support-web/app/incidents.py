"""What is broken right now, and since when -- read from the health check's state.

`ktp-data-server-health.sh` runs hourly as root and is the only thing that
observes a transition. Its log is root-only; its state file is world-readable
and, since it began carrying `since`, holds everything this page needs. So this
module reads a file and never asks systemd or the database anything -- the
poller's sandbox forbids the former and a status surface must not depend on
the latter.

Same discipline as status.py: staleness is a state. The health check writes
every hour; a document older than STALE_AFTER means the check itself has
stopped, and a page that kept rendering its last down-set as current would be
confidently wrong about the one thing it exists to answer.
"""

from __future__ import annotations

from datetime import datetime

# Hourly cron. Two missed runs is a slow box; three means the check is gone.
STALE_AFTER = 3 * 3600
TS = "%Y-%m-%d %H:%M:%S"
MISSING = "incident state is unavailable right now"


def _parse(ts) -> datetime | None:
    try:
        return datetime.strptime(str(ts), TS)
    except (TypeError, ValueError):
        return None


def age(since: datetime, now: datetime) -> str:
    secs = max(int((now - since).total_seconds()), 0)
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 48 * 3600:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d {(secs % 86400) // 3600}h"


def view(doc: dict | None, now: datetime | None = None) -> dict:
    """What the template renders. Never raises, always answers.

    The list is `rows`, not `items`: in Jinja, `incidents.items` resolves to
    the dict method before the key, and renders as a bound method.
    """
    now = now or datetime.now()
    updated = _parse((doc or {}).get("updated_at"))
    if updated is None:
        return {"freshness": "missing", "rows": [], "message": MISSING, "updated": None, "age": None}
    if (now - updated).total_seconds() > STALE_AFTER:
        # Items are dropped, not greyed: a stale list still looks like a list.
        return {"freshness": "stale", "rows": [], "message": MISSING,
                "updated": updated.strftime(TS), "age": age(updated, now)}

    since_map = doc.get("since") if isinstance(doc.get("since"), dict) else {}
    detail_map = doc.get("detail") if isinstance(doc.get("detail"), dict) else {}
    items = []
    for key in doc.get("down") or []:
        if not isinstance(key, str):
            continue
        since = _parse(since_map.get(key))
        items.append({
            "key": key,
            "detail": detail_map.get(key) or "",
            # A state file written before `since` existed carries none; say so
            # rather than inventing a start time.
            "since": since.strftime(TS) if since else None,
            "age": age(since, now) if since else "unknown",
            "_sort": since or now,
        })
    # Longest-open first: the thing nobody has acted on is the one to read.
    items.sort(key=lambda i: i["_sort"])
    for i in items:
        del i["_sort"]
    return {"freshness": "fresh", "rows": items, "message": None,
            "updated": updated.strftime(TS), "age": age(updated, now)}
