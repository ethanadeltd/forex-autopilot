from __future__ import annotations

from datetime import datetime, timezone


# Rough session windows in UTC
SESSIONS_UTC = {
    "sydney": (21, 6),
    "tokyo": (0, 9),
    "london": (7, 16),
    "newyork": (12, 21),
}


def in_trading_session(allowed: list[str], now: datetime | None = None) -> bool:
    if not allowed:
        return True
    now = now or datetime.now(timezone.utc)
    hour = now.hour
    for name in allowed:
        key = name.lower().strip()
        if key not in SESSIONS_UTC:
            continue
        start, end = SESSIONS_UTC[key]
        if start < end:
            if start <= hour < end:
                return True
        else:
            # wraps midnight
            if hour >= start or hour < end:
                return True
    return False
