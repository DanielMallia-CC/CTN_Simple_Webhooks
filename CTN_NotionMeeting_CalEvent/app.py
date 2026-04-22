from __future__ import annotations

import json
from typing import Any, Dict

from invites import get_handler
from logging_setup import logger
from rsvp_sync import handler as rsvp_handler


def _parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    raw = event.get("body")
    if raw is None:
        # Direct Lambda invoke or test event
        return event
    if isinstance(raw, dict):
        return raw
    return json.loads(raw)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    logger.info("")

    # 1. Check for Google push notification (X-Goog-* headers)
    headers = event.get("headers") or {}
    if headers.get("x-goog-channel-id") and headers.get("x-goog-resource-id"):
        return rsvp_handler.handle_push_notification(event)

    # 2. Check for EventBridge scheduler jobs (read directly from event,
    #    not _parse_body — EventBridge sends payload as the top-level object)
    job_type = event.get("job_type")
    if job_type == "bootstrap":
        return rsvp_handler.handle_bootstrap()
    if job_type == "renew_channel":
        return rsvp_handler.handle_renew_channel()
    if job_type == "reconcile":
        return rsvp_handler.handle_reconciliation_sync()

    # 3. Existing Notion webhook routing (unchanged)
    try:
        body = _parse_body(event)
    except Exception:
        logger.exception("Invalid JSON payload")
        return {"statusCode": 400, "body": "invalid_json"}

    data = body.get("data") or {}
    parent = data.get("parent") or {}
    database_id = parent.get("database_id")

    logger.info("Lambda invoked database_id=%s page_id=%s", database_id, data.get("id"))

    if not database_id:
        logger.warning("No database_id in payload, keys present: %s", list(data.keys()))
        return {"statusCode": 400, "body": "Missing data.parent.database_id"}

    handler = get_handler(database_id)
    if not handler:
        logger.warning("No handler for database_id=%s (normalized=%s)", database_id, database_id.replace("-", ""))
        return {
            "statusCode": 400,
            "body": f"Unsupported database_id: {database_id}",
        }

    return handler(body)
