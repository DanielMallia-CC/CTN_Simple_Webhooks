"""
Lambda handler for the CTN Feedback webhook.

Receives a Notion automation webhook payload when a new feedback page
is created, and creates a corresponding action item in the Actions database.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from notion_service import publish

logger = logging.getLogger(__name__)
logging.getLogger().setLevel(logging.INFO)


def _parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the JSON body from an API Gateway or direct invoke event."""
    raw = event.get("body")
    if raw is None:
        return event
    if isinstance(raw, dict):
        return raw
    return json.loads(raw)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """AWS Lambda entry point for feedback webhook."""
    logger.info("[feedback] lambda invoked")
    logger.info("[feedback] raw event keys: %s", list(event.keys()))

    try:
        body = _parse_body(event)
    except Exception:
        logger.exception("Invalid JSON payload")
        return {"statusCode": 400, "body": "invalid_json"}

    data = body.get("data", {})
    page_id = data.get("id")
    properties = data.get("properties", {})

    logger.info("[feedback] processing feedback page_id=%s", page_id)
    logger.info("[feedback] body keys: %s, data keys: %s", list(body.keys()), list(data.keys()))
    logger.info("[feedback] properties keys: %s", list(properties.keys()))
    logger.info("[feedback] properties: %s", json.dumps(properties, default=str))

    # Log the page name (Feedback Title)
    title_prop = properties.get("Feedback Title", {})
    title_parts = title_prop.get("title", [])
    feedback_name = "".join(part.get("plain_text", "") for part in title_parts).strip()
    logger.info("[feedback] feedback name: %s", feedback_name or "(no title)")

    return publish(body)
