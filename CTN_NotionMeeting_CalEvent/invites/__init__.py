from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from config import MUSICIAN_PORTAL_DB_ID, MEETINGS_DB_ID, SITE_VISITS_DB_ID

from invites.musician_portal.handler import handle as handle_musician_portal
from invites.meetings.handler import handle as handle_meetings
from invites.site_visits.handler import handle as handle_site_visits


def _normalize_uuid(value: str) -> str:
    """Strip dashes so both '173a49d2-541c-...' and '173a49d2541c...' match."""
    return value.replace("-", "")


def _req(name: str, v: str | None) -> str:
    if not v:
        raise RuntimeError(f"{name} is required")
    return v

HandlerFn = Callable[[Dict[str, Any]], Dict[str, Any]]

_RAW_HANDLERS: Dict[str, HandlerFn] = {
    _req("MUSICIAN_PORTAL_DB_ID", MUSICIAN_PORTAL_DB_ID): handle_musician_portal,
    _req("MEETINGS_DB_ID", MEETINGS_DB_ID): handle_meetings,
    _req("SITE_VISITS_DB_ID", SITE_VISITS_DB_ID): handle_site_visits,
}

# Normalized lookup: keys are dash-free UUIDs
HANDLERS: Dict[str, HandlerFn] = {
    _normalize_uuid(k): v for k, v in _RAW_HANDLERS.items()
}


def get_handler(database_id: str) -> Optional[HandlerFn]:
    """Look up a handler by database_id, tolerating dashed/undashed UUIDs."""
    return HANDLERS.get(_normalize_uuid(database_id))
