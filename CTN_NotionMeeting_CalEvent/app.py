from __future__ import annotations

import json
from typing import Any, Dict

from invites import HANDLERS
from logging_setup import logger


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
        return {"statusCode": 400, "body": "Missing data.parent.database_id"}

    handler = HANDLERS.get(database_id)
    if not handler:
        logger.warning("No handler for database_id %s", database_id)
        return {
            "statusCode": 400,
            "body": f"Unsupported database_id: {database_id}",
        }

    return handler(body)
