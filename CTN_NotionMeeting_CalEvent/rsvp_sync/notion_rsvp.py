"""Notion RSVP database writer — query, create, update, trash attendee rows."""

from __future__ import annotations

import logging
from typing import Optional

import requests
from backoff import on_exception, expo

from adapters.notion_client import _sess
from config import NOTION_RSVP_DATASOURCE_ID
from rsvp_sync.models import AttendeeRecord

log = logging.getLogger(__name__)

_DB_QUERY_URL = f"https://api.notion.com/v1/data_sources/{NOTION_RSVP_DATASOURCE_ID}/query"
_PAGES_URL = "https://api.notion.com/v1/pages"


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

@on_exception(expo, requests.RequestException, max_tries=3, max_time=30)
def query_by_row_key(row_key: str) -> Optional[dict]:
    """Return the first Notion page matching *row_key*, or ``None``."""
    payload = {
        "filter": {
            "property": "Row Key",
            "rich_text": {"equals": row_key},
        }
    }
    resp = _sess().post(_DB_QUERY_URL, json=payload, timeout=10)
    if not resp.ok:
        log.error("query_by_row_key failed: %s %s", resp.status_code, resp.text)
    resp.raise_for_status()
    results = resp.json().get("results", [])
    return results[0] if results else None


@on_exception(expo, requests.RequestException, max_tries=3, max_time=30)
def query_by_event_id(event_id: str) -> list[dict]:
    """Return all Notion pages whose *Event ID* matches ``event_id``."""
    payload = {
        "filter": {
            "property": "Event ID",
            "rich_text": {"equals": event_id},
        }
    }
    resp = _sess().post(_DB_QUERY_URL, json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json().get("results", [])


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------

def _build_properties(record: AttendeeRecord) -> dict:
    """Build the Notion properties payload from an ``AttendeeRecord``."""
    return {
        "Name": {"title": [{"text": {"content": record.display_name}}]},
        "Row Key": {"rich_text": [{"text": {"content": record.row_key}}]},
        "Event ID": {"rich_text": [{"text": {"content": record.event_id}}]},
        "Event Name": {"rich_text": [{"text": {"content": record.event_name}}]},
        "Attendee Email": {"email": record.attendee_email},
        "RSVP Status": {"select": {"name": record.rsvp_status}},
        "Is Organizer": {"checkbox": record.is_organizer},
    }


@on_exception(expo, requests.RequestException, max_tries=3, max_time=30)
def create_rsvp_row(record: AttendeeRecord) -> str:
    """Create a new RSVP row in Notion and return the page ID."""
    payload = {
        "parent": {"data_source_id": NOTION_RSVP_DATASOURCE_ID},
        "properties": _build_properties(record),
    }
    resp = _sess().post(_PAGES_URL, json=payload, timeout=10)
    resp.raise_for_status()
    page_id = resp.json()["id"]
    log.info("Created RSVP row %s for %s", page_id, record.row_key)
    return page_id


@on_exception(expo, requests.RequestException, max_tries=3, max_time=30)
def update_rsvp_row(page_id: str, record: AttendeeRecord) -> None:
    """Update an existing RSVP row's properties."""
    payload = {"properties": _build_properties(record)}
    resp = _sess().patch(f"{_PAGES_URL}/{page_id}", json=payload, timeout=10)
    resp.raise_for_status()
    log.info("Updated RSVP row %s for %s", page_id, record.row_key)


@on_exception(expo, requests.RequestException, max_tries=3, max_time=30)
def trash_rsvp_row(page_id: str) -> None:
    """Trash an RSVP row (Notion API 2025-09-03 ``in_trash`` flag)."""
    payload = {"in_trash": True}
    resp = _sess().patch(f"{_PAGES_URL}/{page_id}", json=payload, timeout=10)
    resp.raise_for_status()
    log.info("Trashed RSVP row %s", page_id)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def upsert_or_trash(record: AttendeeRecord) -> None:
    """Query by row_key, then create / update / trash as needed.

    Decision matrix:
    - ``record.remove=True``  + existing row  → trash
    - ``record.remove=True``  + no row         → no-op
    - ``record.remove=False`` + no row         → create
    - ``record.remove=False`` + existing row, different status → update
    - ``record.remove=False`` + existing row, same status      → no-op
    """
    existing = query_by_row_key(record.row_key)

    if record.remove:
        if existing:
            trash_rsvp_row(existing["id"])
        else:
            log.debug("No existing row to trash for %s", record.row_key)
        return

    if existing is None:
        create_rsvp_row(record)
        return

    # Compare current RSVP status with the stored value.
    existing_status = (
        existing.get("properties", {})
        .get("RSVP Status", {})
        .get("select", {})
        .get("name")
    )
    if existing_status != record.rsvp_status:
        update_rsvp_row(existing["id"], record)
    else:
        log.debug("No change for %s (status=%s)", record.row_key, record.rsvp_status)
