"""Quick script to verify the stored refresh token has calendar read access.

Usage:
    python scripts/verify_calendar_read_scope.py <client_id>

Where <client_id> is the local part of the Google account email
(e.g. "daniel" if the email is daniel@example.com).

Requires AWS credentials configured (same as the Lambda uses).
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "CTN_NotionMeeting_CalEvent"))

from adapters.token_store import get_db_item, get_google_credentials
from adapters.google_calendar import build_calendar_service


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/verify_calendar_read_scope.py <client_id>")
        print("  client_id = local part of the Google email (before the @)")
        sys.exit(1)

    client_id = sys.argv[1]
    print(f"Looking up DynamoDB record for client_id={client_id!r} ...")

    item = get_db_item(client_id)
    if not item:
        print(f"ERROR: No DynamoDB item found for client_id={client_id!r}")
        sys.exit(1)

    refresh_token = item.get("refresh_token")
    if not refresh_token:
        print("ERROR: DynamoDB item exists but has no refresh_token")
        sys.exit(1)

    print("Found refresh token. Building Calendar service ...")
    creds = get_google_credentials(refresh_token)
    service = build_calendar_service(creds)

    print("Calling events.list (maxResults=1) ...")
    try:
        result = service.events().list(calendarId="primary", maxResults=1).execute()
        print("\nSUCCESS — calendar read scope is working.")
        events = result.get("items", [])
        if events:
            print(f"  Sample event: {events[0].get('summary', '(no title)')}")
        else:
            print("  (No upcoming events found, but the API call succeeded.)")
    except Exception as e:
        print(f"\nFAILED — {e}")
        print("\nThe stored refresh token likely doesn't have calendar read scope.")
        print("You'll need to re-run the OAuth consent flow and store the new refresh token.")
        sys.exit(1)


if __name__ == "__main__":
    main()
