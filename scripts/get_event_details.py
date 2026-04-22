"""Fetch full details of a Google Calendar event.

Usage:
    python scripts/get_event_details.py <client_id> <event_id>

Where client_id is the local part of the Google email (e.g. "chutneynomad").
"""
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "CTN_NotionMeeting_CalEvent"))

from adapters.token_store import get_db_item, get_google_credentials
from adapters.google_calendar import build_calendar_service


def main():
    if len(sys.argv) < 3:
        print("Usage: python scripts/get_event_details.py <client_id> <event_id>")
        sys.exit(1)

    client_id = sys.argv[1]
    event_id = sys.argv[2]

    item = get_db_item(client_id)
    if not item or not item.get("refresh_token"):
        print(f"ERROR: No credentials for client_id={client_id}")
        sys.exit(1)

    creds = get_google_credentials(item["refresh_token"])
    service = build_calendar_service(creds)

    event = service.events().get(calendarId="primary", eventId=event_id).execute()
    print(json.dumps(event, indent=2, default=str))


if __name__ == "__main__":
    main()
