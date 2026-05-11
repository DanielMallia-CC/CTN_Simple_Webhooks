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

log = logging.getLogger()
log.setLevel(logging.INFO)


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
        log.debug("No Notion URL found in description: %.200s", description)
        return None
    raw = m.group(1)
    # Format as UUID with dashes
    page_id = f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
    log.info("Extracted Notion page_id=%s from event description", page_id)
    return page_id


def _resolve_gig_page_id(musician_portal_page_id: str | None) -> str | None:
    """Follow the Musician Portal page → Gig (Management) relation to get the Gig page ID."""
    if not musician_portal_page_id:
        return None
    try:
        from adapters.notion_client import _sess

        # First get the page to find the property ID for Gig (Management)
        resp = _sess().get(
            f"https://api.notion.com/v1/pages/{musician_portal_page_id}",
            timeout=10,
        )
        resp.raise_for_status()
        page = resp.json()
        gig_prop = page.get("properties", {}).get(config.GIG_RELATION_PROP, {})
        prop_id = gig_prop.get("id")
        relations = gig_prop.get("relation", [])

        # If relation is empty but has_more is true, fetch via property item endpoint
        if not relations and gig_prop.get("has_more") and prop_id:
            log.info("Fetching paginated relation property %s for page %s", prop_id, musician_portal_page_id)
            prop_resp = _sess().get(
                f"https://api.notion.com/v1/pages/{musician_portal_page_id}/properties/{prop_id}",
                timeout=10,
            )
            prop_resp.raise_for_status()
            prop_data = prop_resp.json()
            relations = prop_data.get("results", [])

        if relations:
            gig_id = relations[0].get("id")
            # results from property endpoint have {"relation": {"id": "..."}} structure
            if not gig_id and "relation" in relations[0]:
                gig_id = relations[0]["relation"].get("id")
            log.info("Resolved Gig page_id=%s from Musician Portal page %s", gig_id, musician_portal_page_id)
            return gig_id
        log.info("No Gig relation found on Musician Portal page %s", musician_portal_page_id)
        return None
    except Exception:
        log.exception("Failed to resolve Gig from Musician Portal page %s", musician_portal_page_id)
        return None


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
        musician_portal_id = _extract_notion_page_id(ev.get("description"))
        notion_page_id = _resolve_gig_page_id(musician_portal_id)
        log.info(
            "Processing event %s (%s): cancelled=%s, musician_portal_id=%s, gig_page_id=%s, attendees=%d",
            ev["id"], ev.get("summary", ""), cancelled, musician_portal_id, notion_page_id,
            len(ev.get("attendees", [])),
        )

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
# Immediate seeding from invite handlers
# ---------------------------------------------------------------------------

def seed_rsvp_from_event(calendar_event: dict, notion_page_id: str | None = None) -> None:
    """Immediately seed RSVP rows from a freshly created/updated Google Calendar event.

    Called by invite handlers right after ``upsert_event`` so that attendee rows
    with ``needsAction`` status appear in the Notion RSVP database instantly,
    without waiting for the next push notification or reconciliation job.

    The upsert logic in ``notion_rsvp.upsert_or_trash`` is idempotent — when the
    push notification arrives seconds later and runs an incremental sync, it will
    find the rows already exist with the same status and skip them.

    Args:
        calendar_event: The full event dict returned by the Google Calendar API.
        notion_page_id: The Notion page ID to link the RSVP rows to (Gig page for
                        meetings/site_visits; Musician Portal page for musician_portal —
                        the RSVP sync will resolve the Gig relation on subsequent syncs).
    """
    attendees = calendar_event.get("attendees")
    if not attendees:
        log.info("seed_rsvp_from_event: no attendees on event %s, skipping", calendar_event.get("id"))
        return

    event_id = calendar_event.get("id", "")
    event_name = calendar_event.get("summary", "")
    cancelled = calendar_event.get("status") == "cancelled"

    records: list[AttendeeRecord] = []
    for att in attendees:
        records.append(
            AttendeeRecord(
                calendar_id=config.RSVP_CALENDAR_ID,
                event_id=event_id,
                event_name=event_name,
                attendee_email=att["email"],
                display_name=att.get("displayName", att["email"]),
                rsvp_status=att.get("responseStatus", "needsAction"),
                is_organizer=att.get("organizer", False),
                remove=cancelled,
                notion_page_id=notion_page_id,
            )
        )

    log.info(
        "seed_rsvp_from_event: seeding %d attendee records for event %s",
        len(records),
        event_id,
    )
    for record in records:
        try:
            notion_rsvp.upsert_or_trash(record)
        except Exception:
            log.exception(
                "seed_rsvp_from_event: failed to upsert record %s — continuing",
                record.row_key,
            )


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
    log.info("Bootstrap: fetching events from %s for calendar %s", time_min, config.RSVP_CALENDAR_ID)
    events, sync_token = google_calendar.list_events_full(
        service, config.RSVP_CALENDAR_ID, time_min=time_min
    )
    log.info("Bootstrap: fetched %d events, sync_token=%s", len(events), sync_token[:20] if sync_token else None)

    # Process events and upsert to Notion.
    records = _process_events(events)
    log.info("Bootstrap: %d attendee records to upsert", len(records))
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
