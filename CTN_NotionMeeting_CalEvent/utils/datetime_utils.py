from __future__ import annotations

from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo


def is_date_only(iso_str: Optional[str]) -> bool:
    return bool(iso_str) and "T" not in iso_str


def iso_to_tz(iso_str: str, tz: str) -> datetime:
    # Notion returns ISO 8601 strings. datetime.fromisoformat supports offsets.
    # If the string is naive (rare), assume it is already in tz.
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz))
    return dt.astimezone(ZoneInfo(tz))
