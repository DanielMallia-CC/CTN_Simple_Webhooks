from __future__ import annotations

from typing import Any, Dict

from adapters.notion_client import update_page_properties
from config import GOOGLE_EVENT_ID_PROP, GOOGLE_EVENT_URL_PROP


def persist_google_event_metadata(
    *,
    page_id: str,
    event_id: str,
    event_url: str,
) -> None:
    """
    Persist Google Calendar metadata onto the Site Visits page.

    - Google_Event_ID   -> rich_text
    - Google_Event_URL  -> url
    """
    properties: Dict[str, Any] = {
        GOOGLE_EVENT_ID_PROP: {
            "rich_text": [
                {"type": "text", "text": {"content": event_id}}
            ]
        },
        GOOGLE_EVENT_URL_PROP: {
            "url": event_url
        },
    }

    update_page_properties(page_id=page_id, properties=properties)
