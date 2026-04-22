from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from config import GOOGLE_EVENT_ID_PROP
from utils.datetime_utils import is_date_only
from utils.notion_extractors import clean_event_title, extract_page_title

SYDNEY_TZ = ZoneInfo("Australia/Sydney")


def _extract_existing_google_event_id(properties: Dict[str, Any]) -> Optional[str]:
    prop = properties.get(GOOGLE_EVENT_ID_PROP)
    if not isinstance(prop, dict):
        return None

    if prop.get("type") == "rich_text":
        for n in prop.get("rich_text", []):
            plain = n.get("plain_text")
            if plain:
                return plain.strip() or None

    if prop.get("type") == "title":
        for n in prop.get("title", []):
            plain = n.get("plain_text")
            if plain:
                return plain.strip() or None

    return None


def _extract_title(properties: Dict[str, Any]) -> str:
    # Business rule:
    #   Meeting_<name> -> <name>
    raw = extract_page_title(properties, fallback="Meeting")
    cleaned = clean_event_title(raw, prefixes=["Meeting_", "Meeting "]) + " (Meeting)"
    return cleaned or "Meeting"


def _extract_attendees(properties: Dict[str, Any], organizer_email: str) -> list[dict]:
    attendees: list[dict] = []

    people = properties.get("Attendees", {}).get("people", [])
    for p in people:
        email = p.get("person", {}).get("email")
        if email:
            attendees.append({"email": email})

    if organizer_email and organizer_email not in {a["email"] for a in attendees}:
        attendees.append({"email": organizer_email})

    return attendees


def _parse_iso_to_sydney(iso_str: str) -> datetime:
    """
    Parse an ISO string from Notion and ensure the result is timezone-aware
    in Australia/Sydney.
    """
    dt = datetime.fromisoformat(iso_str)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SYDNEY_TZ)
    else:
        dt = dt.astimezone(SYDNEY_TZ)

    return dt


def _extract_date_value(properties: Dict[str, Any]) -> Dict[str, Optional[str]]:
    prop = properties.get("Date & Time")
    if not prop or prop.get("type") != "date":
        raise ValueError("Meeting missing Date & Time property")

    date_val = prop.get("date")
    if not date_val:
        raise ValueError("Meeting Date & Time is empty")

    return {"start": date_val.get("start"), "end": date_val.get("end")}


def build_event_payload(
    properties: Dict[str, Any],
    notion_url: str,
    organizer_email: str,
) -> Dict[str, Any]:
    title = _extract_title(properties)
    attendees = _extract_attendees(properties, organizer_email)

    date_val = _extract_date_value(properties)
    start_raw = date_val.get("start")
    end_raw = date_val.get("end")

    if not start_raw:
        raise ValueError("Meeting Date & Time missing start")

    # ---- All-day event (date-only) ----
    if is_date_only(start_raw):
        start_date = start_raw[:10]
        last_day = end_raw[:10] if end_raw and is_date_only(end_raw) else start_date
        end_date_exclusive = (
            datetime.strptime(last_day, "%Y-%m-%d").date() + timedelta(days=1)
        ).isoformat()

        return {
            "summary": title,
            "description": f"Notion Page: {notion_url}",
            "start": {"date": start_date},
            "end": {"date": end_date_exclusive},
            "attendees": attendees,
        }

    # ---- Timed event ----
    start_dt = _parse_iso_to_sydney(start_raw)
    end_dt = _parse_iso_to_sydney(end_raw) if end_raw else start_dt + timedelta(hours=1)

    return {
        "summary": title,
        "description": f"Notion Page: {notion_url}",
        "start": {
            "dateTime": start_dt.isoformat(),
            "timeZone": "Australia/Sydney",
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": "Australia/Sydney",
        },
        "attendees": attendees,
    }


def parse_meetings(
    *,
    properties: Dict[str, Any],
    notion_url: str,
    organizer_email: str,
) -> Dict[str, Any]:
    return {
        "event_body": build_event_payload(
            properties=properties,
            notion_url=notion_url,
            organizer_email=organizer_email,
        ),
        "existing_event_id": _extract_existing_google_event_id(properties),
    }
