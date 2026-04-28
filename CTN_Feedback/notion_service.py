"""
Notion integration service for the CTN Feedback domain.

When a feedback page is created in the Feedback database (via Notion
automation webhook), this service creates a corresponding action item
in the Actions database.

Flow:
1. Parse the webhook payload for Type, Priority, and Feedback Title
2. Query sprints DB for the current sprint
3. Create an action page with properties (no body content)

Notion API failures are non-blocking — caught and logged, never re-raised.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from config import (
    NOTION_ACTIONS_DATABASE_ID,
    NOTION_SPRINTS_DATABASE_ID,
    NOTION_PROJECT_PAGE_ID,
    NOTION_ASSIGNEE_PAGE_ID,
    NOTION_BUG_TEMPLATE_ID,
    NOTION_ADMIN_TEMPLATE_ID,
)
from notion_client import create_page, query_database, get_database

logger = logging.getLogger(__name__)

# Valid feedback types
VALID_TYPES = {"Bug/Fix", "Change Request", "Idea", "Question"}

# Map feedback type to Notion Action Type select value
ACTION_TYPE_MAP: Dict[str, str] = {
    "Bug/Fix": "Bug",
    "Change Request": "Feature",
    "Idea": "Feature",
    "Question": "Admin",
}

# Map feedback type to template ID
TEMPLATE_ID_MAP: Dict[str, str] = {
    "Bug/Fix": NOTION_BUG_TEMPLATE_ID,
    "Change Request": NOTION_ADMIN_TEMPLATE_ID,
    "Idea": NOTION_ADMIN_TEMPLATE_ID,
    "Question": NOTION_ADMIN_TEMPLATE_ID,
}

# Types that get "Important" appended to the title
IMPORTANT_TYPES = {"Bug/Fix", "Question"}


# ------------------------------------------------------------------
# Payload extraction helpers
# ------------------------------------------------------------------

def _extract_select(properties: Dict[str, Any], prop_name: str) -> Optional[str]:
    """Extract the name from a Notion select property."""
    prop = properties.get(prop_name, {})
    select = prop.get("select")
    if select and isinstance(select, dict):
        return select.get("name")
    return None


def _extract_title_text(properties: Dict[str, Any], prop_name: str = "Feedback Title") -> str:
    """Extract plain text from a Notion title property."""
    prop = properties.get(prop_name, {})
    title_parts = prop.get("title", [])
    return "".join(part.get("plain_text", "") for part in title_parts).strip()


def parse_feedback_payload(body: Dict[str, Any]) -> Dict[str, Any]:
    """Parse the Notion automation webhook payload.

    Returns a dict with: feedback_title, feedback_type, priority, page_id, status.
    Raises ValueError if required fields are missing.
    """
    data = body.get("data", {})
    properties = data.get("properties", {})
    page_id = data.get("id")

    if not page_id:
        raise ValueError("Missing page id in payload")

    feedback_title = _extract_title_text(properties)
    feedback_type = _extract_select(properties, "Type")
    priority = _extract_select(properties, "Priority")

    if not feedback_type:
        raise ValueError("Missing Type property in feedback payload")

    return {
        "feedback_title": feedback_title or "Untitled Feedback",
        "feedback_type": feedback_type,
        "priority": priority or "Medium",
        "page_id": page_id,
    }


# ------------------------------------------------------------------
# Title formatting
# ------------------------------------------------------------------

def format_title(feedback_type: str, priority: str) -> str:
    """Format the action page title.

    Pattern: CTN: [FEEDBACK - {Type}] - {Priority}
    Appends ' - Important' for Bug/Fix and Question types.
    """
    title = f"CTN: [FEEDBACK - {feedback_type}] - {priority}"
    if feedback_type in IMPORTANT_TYPES:
        title += " - Important"
    return title


# ------------------------------------------------------------------
# Sprint lookup
# ------------------------------------------------------------------

def _find_current_sprint_id() -> Optional[str]:
    """Query the sprints database for the current sprint.

    Returns the page ID of the most recently created sprint with
    Sprint Status = Current, or None if not found / not configured.
    """
    if not NOTION_SPRINTS_DATABASE_ID:
        return None

    try:
        # Resolve data source ID from the database
        db_data = get_database(NOTION_SPRINTS_DATABASE_ID)
        data_sources = db_data.get("data_sources", [])
        if not data_sources:
            logger.warning("No data sources found for sprints database")
            return None
        data_source_id = data_sources[0]["id"]

        # Query for current sprint via data source
        from notion_client import _sess
        payload = {
            "filter": {
                "property": "Sprint Status",
                "status": {"equals": "Current"},
            },
            "sorts": [{"timestamp": "created_time", "direction": "descending"}],
            "page_size": 1,
        }
        resp = _sess().post(
            f"https://api.notion.com/v1/data_sources/{data_source_id}/query",
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return results[0]["id"] if results else None
    except Exception as e:
        logger.warning("Failed to query sprints database: %s", str(e))
        return None


# ------------------------------------------------------------------
# Properties builder
# ------------------------------------------------------------------

def _build_properties(
    title: str,
    feedback_type: str,
    sprint_id: Optional[str],
) -> Dict[str, Any]:
    """Build the Notion page properties dict for the action item."""
    action_type = ACTION_TYPE_MAP.get(feedback_type, "Admin")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    properties: Dict[str, Any] = {
        "Name": {
            "title": [{"text": {"content": title}}]
        },
        "Action Type": {
            "select": {"name": action_type}
        },
        "Status": {
            "status": {"name": "Not Started"}
        },
        "Due Date": {
            "date": {"start": today}
        },
    }

    if sprint_id:
        properties["Sprints"] = {
            "relation": [{"id": sprint_id}]
        }

    if NOTION_PROJECT_PAGE_ID:
        properties["Project"] = {
            "relation": [{"id": NOTION_PROJECT_PAGE_ID}]
        }

    if NOTION_ASSIGNEE_PAGE_ID:
        properties["Assigned To"] = {
            "people": [{"object": "user", "id": NOTION_ASSIGNEE_PAGE_ID}]
        }

    return properties


# ------------------------------------------------------------------
# Public entry point
# ------------------------------------------------------------------

def publish(body: Dict[str, Any]) -> Dict[str, Any]:
    """Create a Notion action item from a feedback webhook payload.

    Returns a dict with statusCode and body for the Lambda response.
    Catches ALL exceptions — Notion failure must not block the response.
    """
    try:
        parsed = parse_feedback_payload(body)
    except ValueError as e:
        logger.warning("Invalid feedback payload: %s", str(e))
        return {"statusCode": 400, "body": str(e)}

    page_id = parsed["page_id"]
    feedback_type = parsed["feedback_type"]
    priority = parsed["priority"]

    logger.info(
        "[feedback] processing page_id=%s type=%s priority=%s",
        page_id, feedback_type, priority,
    )

    try:
        if not NOTION_ACTIONS_DATABASE_ID:
            logger.warning("FEEDBACK_ACTIONS_DB_ID not configured")
            return {"statusCode": 500, "body": "actions database not configured"}

        logger.info(
            "[feedback] config: actions_db=%s sprints_db=%s project=%s assignee=%s bug_tpl=%s admin_tpl=%s",
            NOTION_ACTIONS_DATABASE_ID,
            NOTION_SPRINTS_DATABASE_ID or "(not set)",
            NOTION_PROJECT_PAGE_ID or "(not set)",
            NOTION_ASSIGNEE_PAGE_ID or "(not set)",
            NOTION_BUG_TEMPLATE_ID or "(not set)",
            NOTION_ADMIN_TEMPLATE_ID or "(not set)",
        )

        # 1. Find current sprint
        sprint_id = _find_current_sprint_id()
        logger.info("[feedback] sprint_id=%s", sprint_id or "(none)")

        # 2. Build title and properties
        title = format_title(feedback_type, priority)
        properties = _build_properties(title, feedback_type, sprint_id)
        logger.info("[feedback] title=%s", title)

        # 3. Build page creation payload
        payload: Dict[str, Any] = {
            "parent": {"database_id": NOTION_ACTIONS_DATABASE_ID},
            "properties": properties,
        }

        # Apply template if configured
        template_id = TEMPLATE_ID_MAP.get(feedback_type)
        if template_id:
            payload["template"] = {
                "type": "template_id",
                "template_id": template_id,
            }
            logger.info("[feedback] using template_id=%s", template_id)
        else:
            logger.info("[feedback] no template configured for type=%s", feedback_type)

        # 4. Create the action page (no body blocks)
        logger.info("[feedback] create_page payload: %s", json.dumps(payload, default=str)[:2000])
        result = create_page(payload)
        created_id = result.get("id")
        logger.info("[feedback] action created page_id=%s for feedback=%s", created_id, page_id)

        return {"statusCode": 200, "body": created_id or "created"}

    except Exception as e:
        logger.warning(
            "Failed to create Notion action for feedback page %s: %s",
            page_id, str(e),
        )
        return {"statusCode": 500, "body": "notion_error"}
