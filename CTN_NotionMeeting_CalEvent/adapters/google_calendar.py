# src/adapters/google_calendar.py

from __future__ import annotations

from typing import Any, Dict, Optional

from backoff import expo, on_exception
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials


def build_calendar_service(creds: Credentials) -> Any:
    """
    Build a Google Calendar API service client.
    cache_discovery=False avoids writing discovery cache in Lambda.
    """
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


@on_exception(expo, HttpError, max_tries=3, max_time=20)
def insert_event(
    service: Any,
    calendar_id: str,
    event_body: Dict[str, Any],
    send_updates: str = "all",
) -> Dict[str, Any]:
    return (
        service.events()
        .insert(calendarId=calendar_id, body=event_body, sendUpdates=send_updates)
        .execute()
    )


@on_exception(expo, HttpError, max_tries=3, max_time=20)
def update_event(
    service: Any,
    calendar_id: str,
    event_id: str,
    event_body: Dict[str, Any],
    send_updates: str = "all",
) -> Dict[str, Any]:
    return (
        service.events()
        .update(
            calendarId=calendar_id,
            eventId=event_id,
            body=event_body,
            sendUpdates=send_updates,
        )
        .execute()
    )


def upsert_event(
    service: Any,
    calendar_id: str,
    event_body: Dict[str, Any],
    *,
    event_id: Optional[str] = None,
    send_updates: str = "all",
) -> Dict[str, Any]:
    """
    Idempotency strategy: if event_id is known, update it.
    If update returns 404, fall back to insert.

    Callers should store the returned event's `id` and `htmlLink` in Notion.
    """
    if not event_id:
        return insert_event(service, calendar_id, event_body, send_updates=send_updates)

    try:
        return update_event(
            service,
            calendar_id,
            event_id=event_id,
            event_body=event_body,
            send_updates=send_updates,
        )
    except HttpError as e:
        status = getattr(getattr(e, "resp", None), "status", None)
        if status == 404:
            return insert_event(service, calendar_id, event_body, send_updates=send_updates)
        raise
