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


def list_events_incremental(
    service: Any,
    calendar_id: str,
    sync_token: str,
) -> tuple[list[dict], str]:
    """
    Paginated events.list with syncToken.
    Returns (events, new_sync_token).

    No retry decorator — the caller needs to catch HttpError 410 directly
    to trigger a full-sync fallback.
    """
    all_events: list[dict] = []
    page_token: Optional[str] = None

    while True:
        response = (
            service.events()
            .list(calendarId=calendar_id, syncToken=sync_token, pageToken=page_token)
            .execute()
        )
        all_events.extend(response.get("items", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    new_sync_token: str = response["nextSyncToken"]
    return all_events, new_sync_token


@on_exception(expo, HttpError, max_tries=3, max_time=20)
def list_events_full(
    service: Any,
    calendar_id: str,
) -> tuple[list[dict], str]:
    """
    Paginated events.list without syncToken.
    Returns (events, initial_sync_token).
    """
    all_events: list[dict] = []
    page_token: Optional[str] = None

    while True:
        response = (
            service.events()
            .list(calendarId=calendar_id, pageToken=page_token)
            .execute()
        )
        all_events.extend(response.get("items", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    initial_sync_token: str = response["nextSyncToken"]
    return all_events, initial_sync_token


@on_exception(expo, HttpError, max_tries=3, max_time=20)
def create_watch_channel(
    service: Any,
    calendar_id: str,
    channel_id: str,
    webhook_url: str,
    token: str,
    ttl: int,
) -> Dict[str, Any]:
    """Call events.watch to register a push notification channel."""
    body = {
        "id": channel_id,
        "type": "web_hook",
        "address": webhook_url,
        "token": token,
        "params": {"ttl": str(ttl)},
    }
    return service.events().watch(calendarId=calendar_id, body=body).execute()


@on_exception(expo, HttpError, max_tries=3, max_time=20)
def stop_watch_channel(
    service: Any,
    channel_id: str,
    resource_id: str,
) -> None:
    """Call channels.stop to tear down a push notification channel."""
    body = {
        "id": channel_id,
        "resourceId": resource_id,
    }
    service.channels().stop(body=body).execute()
