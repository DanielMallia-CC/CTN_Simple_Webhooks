from __future__ import annotations

from typing import Any, Dict
import requests

from adapters.google_calendar import build_calendar_service, upsert_event
from adapters.notion_client import fetch_notion_user_email
from adapters.token_store import (
    get_db_item,
    get_google_credentials,
    update_db_notion_id,
)
from invites.meetings.parser import parse_meetings
from invites.meetings.notion_updates import persist_google_event_metadata
from logging_setup import logger


def handle(body: Dict[str, Any]) -> Dict[str, Any]:
    data = body.get("data") or {}
    properties: Dict[str, Any] = data.get("properties") or {}
    page_id = data.get("id")
    notion_url = data.get("url") or ""

    if not page_id:
        return {"statusCode": 400, "body": "Missing Notion page id"}

    notion_user_id = (body.get("source") or {}).get("user_id")
    if not notion_user_id:
        return {"statusCode": 400, "body": "Missing Notion user_id"}

    # 1) Resolve organizer email
    try:
        organizer_email = fetch_notion_user_email(notion_user_id)
    except requests.RequestException:
        logger.exception("Failed to fetch Notion user email for page %s", page_id)
        return {"statusCode": 502, "body": "notion_error"}
    if not organizer_email:
        return {"statusCode": 400, "body": "Unable to resolve organizer email"}

    # 2) Load OAuth record
    client_id = organizer_email.split("@")[0]
    record = get_db_item(client_id)
    if not record or not record.get("refresh_token"):
        return {"statusCode": 404, "body": f"No credentials for {organizer_email}"}

    if not record.get("notion_user_id"):
        update_db_notion_id(client_id, notion_user_id)

    # 3) Build Google Calendar service
    creds = get_google_credentials(record["refresh_token"])
    calendar_service = build_calendar_service(creds)

    # 4) Parse Meeting payload
    try:
        parsed = parse_meetings(
            properties=properties,
            notion_url=notion_url,
            organizer_email=organizer_email,
        )
    except ValueError as e:
        logger.warning("Invalid Notion payload for page %s: %s", page_id, str(e))
        return {"statusCode": 400, "body": str(e)}

    event_body = parsed["event_body"]
    existing_event_id = parsed["existing_event_id"]

    # 5) Upsert Google Calendar event
    try:
        result = upsert_event(
            calendar_service,
            organizer_email,
            event_body,
            event_id=existing_event_id,
            send_updates="all",
        )
    except Exception:
        logger.exception("Failed to upsert Google Calendar event")
        return {"statusCode": 502, "body": "calendar_error"}

    event_id = result.get("id")
    event_url = result.get("htmlLink")

    if not event_id or not event_url:
        logger.warning("Calendar upsert returned no id or url for page %s", page_id)
        return {"statusCode": 500, "body": "invalid_calendar_response"}

    # 6) Persist metadata back to Notion
    try:
        persist_google_event_metadata(
            page_id=page_id,
            event_id=event_id,
            event_url=event_url,
        )
    except requests.RequestException:
        logger.warning("Calendar event created but failed to update Notion for page %s", page_id, exc_info=True)

    return {
        "statusCode": 200,
        "body": event_url,
    }
