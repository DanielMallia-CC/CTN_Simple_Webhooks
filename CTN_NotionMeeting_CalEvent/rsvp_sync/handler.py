"""RSVP Sync Handler — validation, extraction, and orchestration helpers."""

from __future__ import annotations

import datetime
import json
import logging
import re
import secrets
import uuid
from typing import Any, List

import config
from adapters import google_calendar, sync_state_store, token_store
from googleapiclient.errors import HttpError
from rsvp_sync import notion_rsvp
from rsvp_sync.models import AttendeeRecord

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_push(event: dict) -> bool:
    """Check secret slug in URL path and X-Goog-Channel-Token header.

    Both must match the values from config (env vars, no DynamoDB call).
    Returns True when valid, False otherwise.
    """
    headers = event.get("headers") or {}
    raw_path = event.get("rawPath", "") or event.get("requestContext", {}).get("http", {}).get("path", "")

    slug_ok = bool(config.RSVP_WEBHOOK_SLUG and config.RSVP_WEBHOOK_SLUG in raw_path)
    token_ok = headers.get("x-goog-channel-token") == config.RSVP_WEBHOOK_TOKEN

    if not slug_ok:
        log.warning("Push rejected: secret slug not found in path %s", raw_path)
    if not token_ok:
        log.warning("Push rejected: channel token mismatch")

    return slug_ok and token_ok


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

_NOTION_URL_RE = re.compile(r"notion\.so/\S*?([0-9a-f]{32})")


def _extract_notion_page_id(description: str | None) -> str | None:
    """Extract a Notion page ID from a Google Calendar event description."""
    if not description:
        return None
    m = _NOTION_URL_RE.search(description)
    if not m:
        return None
    raw = m.group(1)
    # Format as UUID with dashes
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


def _process_events(events: list[dict]) -> List[AttendeeRecord]:
    """Extract AttendeeRecord list from changed Google Calendar events.

    * Sets ``remove=True`` for all attendees of cancelled events.
    * Skips events with no ``attendees`` array.
    """
    records: list[AttendeeRecord] = []

    for ev in events:
        if "attendees" not in ev:
            continue

        cancelled = ev.get("status") == "cancelled"
        notion_page_id = _extract_notion_page_id(ev.get("description"))

        for att in ev["attendees"]:
            records.append(
                AttendeeRecord(
                    calendar_id=config.RSVP_CALENDAR_ID,
                    event_id=ev["id"],
                    event_name=ev.get("summary", ""),
                    attendee_email=att["email"],
                    display_name=att.get("displayName", att["email"]),
                    rsvp_status=att.get("responseStatus", "needsAction"),
                    is_organizer=att.get("organizer", False),
                    remove=cancelled,
                    notion_page_id=notion_page_id,
                )
            )

    return records


# ---------------------------------------------------------------------------
# Removed-attendee detection
# ---------------------------------------------------------------------------

def _trash_removed_attendees(events: list[dict]) -> None:
    """Trash Notion rows for attendees no longer on a non-cancelled event.

    For each non-cancelled changed event that has an attendees array, query
    Notion for all existing rows with that Event ID and trash any whose email
    is not in the current Google attendee list.
    """
    for ev in events:
        if ev.get("status") == "cancelled":
            continue
        if "attendees" not in ev:
            continue

        current_emails = {att["email"] for att in ev["attendees"]}
        existing_rows = notion_rsvp.query_by_event_id(ev["id"])

        for row in existing_rows:
            row_email = (
                row.get("properties", {})
                .get("Attendee Email", {})
                .get("email")
            )
            if row_email and row_email not in current_emails:
                log.info(
                    "Trashing removed attendee %s from event %s",
                    row_email,
                    ev["id"],
                )
                notion_rsvp.trash_rsvp_row(row["id"])


# ---------------------------------------------------------------------------
# Calendar service builder
# ---------------------------------------------------------------------------

def _build_calendar_service() -> Any:
    """Load credentials from DynamoDB via token_store and build the Calendar service.

    Uses the RSVP_CALENDAR_ID owner as the client_id lookup key.  The existing
    handlers derive client_id from the organizer email (``local-part`` before
    ``@``).  For the RSVP path the calendar owner is the configured
    ``RSVP_CALENDAR_ID`` — which is typically a full email address — so we
    apply the same ``split("@")[0]`` convention.
    """
    client_id = config.RSVP_CALENDAR_ID.split("@")[0]
    item = token_store.get_db_item(client_id)
    if not item or not item.get("refresh_token"):
        raise RuntimeError(
            f"No credentials found in DynamoDB for client_id={client_id}"
        )
    creds = token_store.get_google_credentials(item["refresh_token"])
    return google_calendar.build_calendar_service(creds)


# ---------------------------------------------------------------------------
# Internal sync helpers
# ---------------------------------------------------------------------------

def _run_full_sync() -> list[dict]:
    """Perform a full sync (no sync token) and persist the initial token."""
    service = _build_calendar_service()
    events, sync_token = google_calendar.list_events_full(
        service, config.RSVP_CALENDAR_ID
    )
    sync_state_store.update_sync_token(sync_token)
    return events


def _run_incremental_sync() -> list[dict]:
    """Perform an incremental sync; fall back to full sync on 410 Gone."""
    state = sync_state_store.get_sync_state()
    service = _build_calendar_service()

    try:
        events, new_token = google_calendar.list_events_incremental(
            service, config.RSVP_CALENDAR_ID, state.sync_token
        )
    except HttpError as exc:
        status = getattr(getattr(exc, "resp", None), "status", None)
        if status == 410:
            log.warning("Sync token expired (410 Gone), falling back to full sync")
            return _run_full_sync()
        raise

    sync_state_store.update_sync_token(new_token)
    return events


# ---------------------------------------------------------------------------
# Public orchestration functions
# ---------------------------------------------------------------------------

def handle_push_notification(event: dict) -> dict:
    """Handle an incoming Google Calendar push notification."""
    headers = event.get("headers") or {}

    # Google sends a "sync" ping when the channel is first created — ack it.
    if headers.get("x-goog-resource-state") == "sync":
        return {"statusCode": 200, "body": "sync_ping_ack"}

    if not _validate_push(event):
        return {"statusCode": 403, "body": "forbidden"}

    events = _run_incremental_sync()
    records = _process_events(events)

    for record in records:
        notion_rsvp.upsert_or_trash(record)

    _trash_removed_attendees(events)

    return {"statusCode": 200, "body": "ok"}


def handle_reconciliation_sync() -> dict:
    """Handle a scheduled reconciliation job (EventBridge every 30 min)."""
    events = _run_incremental_sync()
    records = _process_events(events)

    for record in records:
        notion_rsvp.upsert_or_trash(record)

    _trash_removed_attendees(events)

    return {"statusCode": 200, "body": "reconciliation_complete"}


def handle_renew_channel() -> dict:
    """Handle a scheduled channel renewal job (EventBridge every 12 h)."""
    state = sync_state_store.get_sync_state()
    service = _build_calendar_service()

    # Try to stop the existing channel; ignore 404 (already expired).
    if state and state.channel_id and state.resource_id:
        try:
            google_calendar.stop_watch_channel(
                service, state.channel_id, state.resource_id
            )
        except HttpError as exc:
            log.warning(
                "Failed to stop old channel %s: %s — continuing",
                state.channel_id,
                exc,
            )

    channel_id = str(uuid.uuid4())
    channel_token = secrets.token_urlsafe(32)

    response = google_calendar.create_watch_channel(
        service,
        config.RSVP_CALENDAR_ID,
        channel_id,
        config.RSVP_FUNCTION_URL,
        channel_token,
        config.RSVP_CHANNEL_TTL_SECONDS,
    )

    sync_state_store.update_channel_state(
        channel_id,
        response["resourceId"],
        int(response["expiration"]),
        channel_token,
    )

    return {"statusCode": 200, "body": "channel_renewed"}


def handle_bootstrap() -> dict:
    """Bootstrap: full sync + watch channel creation + persist all state."""
    service = _build_calendar_service()
    state = sync_state_store.get_sync_state()

    # Stop any existing channel.
    if state and state.channel_id and state.resource_id:
        try:
            google_calendar.stop_watch_channel(
                service, state.channel_id, state.resource_id
            )
        except HttpError as exc:
            log.warning(
                "Failed to stop existing channel %s: %s — continuing",
                state.channel_id,
                exc,
            )

    # Full sync — only future events.
    time_min = datetime.datetime.now(datetime.timezone.utc).isoformat()
    events, sync_token = google_calendar.list_events_full(
        service, config.RSVP_CALENDAR_ID, time_min=time_min
    )

    # Process events and upsert to Notion.
    records = _process_events(events)
    for record in records:
        notion_rsvp.upsert_or_trash(record)

    _trash_removed_attendees(events)

    # Create a new watch channel.
    channel_id = str(uuid.uuid4())
    channel_token = secrets.token_urlsafe(32)

    response = google_calendar.create_watch_channel(
        service,
        config.RSVP_CALENDAR_ID,
        channel_id,
        config.RSVP_FUNCTION_URL,
        channel_token,
        config.RSVP_CHANNEL_TTL_SECONDS,
    )

    # Persist everything in one shot.
    sync_state_store.update_full_state(
        sync_token,
        channel_id,
        response["resourceId"],
        int(response["expiration"]),
        channel_token,
    )

    return {
        "statusCode": 200,
        "body": json.dumps({
            "events_fetched": len(events),
            "channel_id": channel_id,
        }),
    }
