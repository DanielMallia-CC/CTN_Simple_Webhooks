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
from invites.musician_portal.parser import parse_musician_portal
from invites.musician_portal.notion_updates import persist_google_event_metadata
from logging_setup import logger
from rsvp_sync.handler import seed_rsvp_from_event


def handle(body: Dict[str, Any]) -> Dict[str, Any]:
    data = body.get("data") or {}
    properties: Dict[str, Any] = data.get("properties") or {}
    page_id = data.get("id")
    notion_url = data.get("url") or ""

    logger.info("[musician_portal] handler entered page_id=%s", page_id)

    if not page_id:
        logger.warning("[musician_portal] EXIT: missing page_id")
        return {"statusCode": 400, "body": "Missing Notion page id"}

    notion_user_id = (body.get("source") or {}).get("user_id")
    if not notion_user_id:
        logger.warning("[musician_portal] EXIT: missing notion_user_id, source=%s", body.get("source"))
        return {"statusCode": 400, "body": "Missing Notion user_id"}

    # 1) Resolve organizer email
    logger.info("[musician_portal] resolving email for notion_user_id=%s", notion_user_id)
    try:
        organizer_email = fetch_notion_user_email(notion_user_id)
    except requests.RequestException:
        logger.exception("[musician_portal] EXIT: failed to fetch Notion user email for page %s", page_id)
        return {"statusCode": 502, "body": "notion_error"}
    if not organizer_email:
        logger.warning("[musician_portal] EXIT: no email resolved for notion_user_id=%s", notion_user_id)
        return {"statusCode": 400, "body": "Unable to resolve organizer email"}
    logger.info("[musician_portal] organizer_email=%s", organizer_email)

    # 2) Load OAuth record
    client_id = organizer_email.split("@")[0]
    logger.info("[musician_portal] loading OAuth record for client_id=%s", client_id)
    record = get_db_item(client_id)
    if not record or not record.get("refresh_token"):
        logger.warning("[musician_portal] EXIT: no OAuth record for %s (client_id=%s)", organizer_email, client_id)
        return {"statusCode": 404, "body": f"No credentials for {organizer_email}"}
    logger.info("[musician_portal] OAuth record found")

    notion_user_id = (body.get("source") or {}).get("user_id")
    if notion_user_id and not record.get("notion_user_id"):
        update_db_notion_id(client_id, notion_user_id)

    # 3) Build Google Calendar service
    logger.info("[musician_portal] building Google Calendar service")
    creds = get_google_credentials(record["refresh_token"])
    calendar_service = build_calendar_service(creds)

    # 4) Parse MP payload
    logger.info("[musician_portal] parsing payload, property_keys=%s", list(properties.keys()))
    try:
        parsed = parse_musician_portal(
            properties=properties,
            notion_url=notion_url,
            organizer_email=organizer_email,
        )
    except ValueError as e:
        logger.warning("[musician_portal] EXIT: invalid payload for page %s: %s", page_id, str(e))
        return {"statusCode": 400, "body": str(e)}
    
    event_body = parsed["event_body"]
    existing_event_id = parsed["existing_event_id"]
    logger.info("[musician_portal] parsed OK, existing_event_id=%s, summary=%s",
                existing_event_id, event_body.get("summary"))

    # 5) Upsert Google Calendar event
    logger.info("[musician_portal] upserting calendar event, is_update=%s", bool(existing_event_id))
    try:
        result = upsert_event(
            calendar_service,
            organizer_email,
            event_body,
            event_id=existing_event_id,
            send_updates="all",
        )
    except Exception:
        logger.exception("[musician_portal] EXIT: failed to upsert Google Calendar event for page %s", page_id)
        return {"statusCode": 502, "body": "calendar_error"}

    event_id = result.get("id")
    event_url = result.get("htmlLink")
    logger.info("[musician_portal] upsert result: event_id=%s event_url=%s", event_id, event_url)

    if not event_id or not event_url:
        logger.warning("[musician_portal] EXIT: no id or url in result, keys=%s", list(result.keys()))
        return {"statusCode": 500, "body": "invalid_calendar_response"}

    # 6) Seed RSVP rows immediately so attendees appear in Notion without delay
    # For musician portal, page_id is the musician portal page — seed_rsvp_from_event
    # will pass it as notion_page_id; the RSVP sync's _resolve_gig_page_id will follow
    # the relation to the Gig page when processing push notifications later.
    logger.info("[musician_portal] seeding RSVP rows for event %s", event_id)
    seed_rsvp_from_event(result, notion_page_id=page_id)

    # 7) Persist metadata back to Notion
    logger.info("[musician_portal] persisting event metadata to Notion page %s", page_id)
    try:
        persist_google_event_metadata(
            page_id=page_id,
            event_id=event_id,
            event_url=event_url,
        )
        logger.info("[musician_portal] metadata persisted OK")
    except requests.RequestException:
        logger.warning("[musician_portal] calendar event created but failed to update Notion for page %s",
                        page_id, exc_info=True)

    logger.info("[musician_portal] SUCCESS page_id=%s event_url=%s", page_id, event_url)
    return {
        "statusCode": 200,
        "body": event_url,
    }
