from __future__ import annotations

from typing import Any, Callable, Dict

from config import MUSICIAN_PORTAL_DB_ID, MEETINGS_DB_ID, SITE_VISITS_DB_ID

from invites.musician_portal.handler import handle as handle_musician_portal
from invites.meetings.handler import handle as handle_meetings
from invites.site_visits.handler import handle as handle_site_visits

def _req(name: str, v: str | None) -> str:
    if not v:
        raise RuntimeError(f"{name} is required")
    return v

HandlerFn = Callable[[Dict[str, Any]], Dict[str, Any]]

HANDLERS: Dict[str, HandlerFn] = {
    _req("MUSICIAN_PORTAL_DB_ID", MUSICIAN_PORTAL_DB_ID): handle_musician_portal,
    _req("MEETINGS_DB_ID", MEETINGS_DB_ID): handle_meetings,
    _req("SITE_VISITS_DB_ID", SITE_VISITS_DB_ID): handle_site_visits,
}
