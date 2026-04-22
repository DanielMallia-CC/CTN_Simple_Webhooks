from __future__ import annotations

import os
from typing import Any, Dict
import requests

from adapters.google_calendar import build_calendar_service, upsert_event
from adapters.token_store import (
    get_db_item,
    get_google_credentials,
    update_db_notion_id,
)
from invites.site_visits.parser import parse_site_visits
from invites.site_visits.notion_updates import persist_google_event_metadata

from logging_setup import logger


def handle(body: Dict[str, Any]) -> Dict[str, Any]:
    data = body.get("data") or {}
    properties: Dict[str, Any] = data.get("properties") or {}
    page_id = data.get("id")
    notion_url = data.get("url") or ""

    logger.info("[site_visits] handler entered page_id=%s", page_id)

    if not page_id:
        logger.warning("[site_visits] EXIT: missing page_id")
        return {"statusCode": 400, "body": "Missing Notion page id"}

    # 1) Resolve organizer email from environment (calendar owner)
    organizer_email = "chutneynomad@gmail.com"  # TEMP: hardcoded for testing
    logger.info("[site_visits] organizer_email=%s", organizer_email)

    # 2) Load OAuth record
    client_id = organizer_email.split("@")[0]
    logger.info("[site_visits] loading OAuth record for client_id=%s", client_id)
    record = get_db_item(client_id)
    if not record or not record.get("refresh_token"):
        logger.warning("[site_visits] EXIT: no OAuth record for %s (client_id=%s)", organizer_email, client_id)
        return {"statusCode": 404, "body": f"No credentials for {organizer_email}"}
    logger.info("[site_visits] OAuth record found")

    notion_user_id = (body.get("source") or {}).get("user_id")
    if notion_user_id and not record.get("notion_user_id"):
        update_db_notion_id(client_id, notion_user_id)

    # 3) Build Google Calendar service
    logger.info("[site_visits] building Google Calendar service")
    creds = get_google_credentials(record["refresh_token"])
    calendar_service = build_calendar_service(creds)

    # 4) Parse Site Visits payload
    logger.info("[site_visits] parsing payload, property_keys=%s", list(properties.keys()))
    try:
        parsed = parse_site_visits(
            properties=properties,
            notion_url=notion_url,
            organizer_email=organizer_email,
        )
    except ValueError as e:
        logger.warning("[site_visits] EXIT: invalid payload for page %s: %s", page_id, str(e))
        return {"statusCode": 400, "body": str(e)}
    
    event_body = parsed["event_body"]
    existing_event_id = parsed["existing_event_id"]
    logger.info("[site_visits] parsed OK, existing_event_id=%s, summary=%s",
                existing_event_id, event_body.get("summary"))

    # 5) Upsert Google Calendar event
    logger.info("[site_visits] upserting calendar event, is_update=%s", bool(existing_event_id))
    try:
        result = upsert_event(
            calendar_service,
            organizer_email,
            event_body,
            event_id=existing_event_id,
            send_updates="all",
        )
    except Exception:
        logger.exception("[site_visits] EXIT: failed to upsert Google Calendar event for page %s", page_id)
        return {"statusCode": 502, "body": "calendar_error"}

    event_id = result.get("id")
    event_url = result.get("htmlLink")
    logger.info("[site_visits] upsert result: event_id=%s event_url=%s", event_id, event_url)

    if not event_id or not event_url:
        logger.warning("[site_visits] EXIT: no id or url in result, keys=%s", list(result.keys()))
        return {"statusCode": 500, "body": "invalid_calendar_response"}

    # 6) Persist metadata back to Notion
    logger.info("[site_visits] persisting event metadata to Notion page %s", page_id)
    try:
        persist_google_event_metadata(
            page_id=page_id,
            event_id=event_id,
            event_url=event_url,
        )
        logger.info("[site_visits] metadata persisted OK")
    except requests.RequestException:
        logger.warning("[site_visits] calendar event created but failed to update Notion for page %s",
                        page_id, exc_info=True)

    logger.info("[site_visits] SUCCESS page_id=%s event_url=%s", page_id, event_url)
    return {
        "statusCode": 200,
        "body": event_url,
    }
