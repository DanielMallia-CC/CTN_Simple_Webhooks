"""Property-based tests for RSVP sync handler.

Feature: rsvp-sync
Property 2: Push notification validation
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "CTN_NotionMeeting_CalEvent")
)

from hypothesis import given, settings
from hypothesis import strategies as st

from rsvp_sync.handler import _validate_push

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Non-empty strings for slugs and tokens
non_empty_text = st.text(min_size=1, max_size=50).filter(lambda s: s.strip())


# ---------------------------------------------------------------------------
# Property 2 – Push notification validation
# ---------------------------------------------------------------------------


@given(
    slug=non_empty_text,
    token=non_empty_text,
    configured_slug=non_empty_text,
    configured_token=non_empty_text,
)
@settings(max_examples=100, deadline=None)
def test_push_notification_validation(
    slug: str,
    token: str,
    configured_slug: str,
    configured_token: str,
):
    """**Validates: Requirements 2.1, 2.2, 2.3, 2.4**

    For any incoming push notification event with a URL path and a channel
    token header, the validation function SHALL return success if and only if
    the URL path contains the configured secret slug AND the channel token
    header matches the RSVP_WEBHOOK_TOKEN environment variable. All other
    combinations SHALL be rejected.
    """
    event = {
        "rawPath": f"/{slug}/webhook",
        "headers": {"x-goog-channel-token": token},
    }

    with patch("rsvp_sync.handler.config") as mock_config:
        mock_config.RSVP_WEBHOOK_SLUG = configured_slug
        mock_config.RSVP_WEBHOOK_TOKEN = configured_token

        result = _validate_push(event)

    slug_matches = configured_slug in f"/{slug}/webhook"
    token_matches = token == configured_token
    expected = slug_matches and token_matches

    assert result == expected, (
        f"Expected {expected}, got {result}. "
        f"slug={slug!r}, configured_slug={configured_slug!r}, "
        f"token={token!r}, configured_token={configured_token!r}"
    )


# ---------------------------------------------------------------------------
# Strategies for Property 3
# ---------------------------------------------------------------------------

from rsvp_sync.handler import _process_events

# Email-like strings (simple)
email_st = st.from_regex(r"[a-z]{1,10}@[a-z]{1,5}\.[a-z]{2,3}", fullmatch=True)

# Google Calendar attendee dict
attendee_dict_st = st.fixed_dictionaries(
    {
        "email": email_st,
        "responseStatus": st.sampled_from(
            ["accepted", "declined", "tentative", "needsAction"]
        ),
    },
    optional={
        "displayName": st.text(min_size=1, max_size=30),
        "organizer": st.booleans(),
    },
)

# Google Calendar event dict — may or may not have attendees / status
event_dict_st = st.fixed_dictionaries(
    {
        "id": st.text(min_size=1, max_size=40),
    },
    optional={
        "summary": st.text(max_size=60),
        "status": st.sampled_from(["confirmed", "cancelled", "tentative"]),
        "attendees": st.lists(attendee_dict_st, min_size=0, max_size=5),
    },
)


# ---------------------------------------------------------------------------
# Property 3 – Attendee extraction correctness
# ---------------------------------------------------------------------------


@given(events=st.lists(event_dict_st, min_size=0, max_size=10))
@settings(max_examples=100, deadline=None)
def test_attendee_extraction_correctness(events: list):
    """**Validates: Requirements 4.1, 4.2, 4.4**

    For any list of Google Calendar event objects, the extraction function
    SHALL produce one AttendeeRecord per attendee in non-cancelled events
    with an attendees array, with remove=True for all attendees of cancelled
    events, and zero records for events with no attendees array. Each record
    SHALL contain the correct email, display_name, rsvp_status, is_organizer,
    event_id, and event_name fields.
    """
    fixed_calendar_id = "test-calendar@group.calendar.google.com"

    with patch("rsvp_sync.handler.config") as mock_config:
        mock_config.RSVP_CALENDAR_ID = fixed_calendar_id
        records = _process_events(events)

    # --- Total record count ---
    expected_count = sum(
        len(ev["attendees"]) for ev in events if "attendees" in ev
    )
    assert len(records) == expected_count, (
        f"Expected {expected_count} records, got {len(records)}"
    )

    # Walk events and records in lock-step to verify field correctness
    idx = 0
    for ev in events:
        if "attendees" not in ev:
            continue

        cancelled = ev.get("status") == "cancelled"

        for att in ev["attendees"]:
            rec = records[idx]

            # calendar_id
            assert rec.calendar_id == fixed_calendar_id

            # event_id
            assert rec.event_id == ev["id"]

            # event_name — defaults to "" when summary absent
            assert rec.event_name == ev.get("summary", "")

            # attendee_email
            assert rec.attendee_email == att["email"]

            # display_name — falls back to email when displayName absent
            assert rec.display_name == att.get("displayName", att["email"])

            # rsvp_status — defaults to "needsAction"
            assert rec.rsvp_status == att.get("responseStatus", "needsAction")

            # is_organizer — defaults to False
            assert rec.is_organizer == att.get("organizer", False)

            # remove flag
            if cancelled:
                assert rec.remove is True, (
                    f"Cancelled event {ev['id']}: expected remove=True"
                )
            else:
                assert rec.remove is False, (
                    f"Non-cancelled event {ev['id']}: expected remove=False"
                )

            idx += 1

    # Verify we consumed all records
    assert idx == len(records)


# ---------------------------------------------------------------------------
# Strategies for Property 3b
# ---------------------------------------------------------------------------

from rsvp_sync.handler import _trash_removed_attendees

# Notion row dict matching the shape used by _trash_removed_attendees
def _notion_row(page_id: str, email: str) -> dict:
    """Build a minimal Notion row dict with the fields read by the handler."""
    return {
        "id": page_id,
        "properties": {
            "Attendee Email": {"email": email},
        },
    }


# Strategy: a set of email addresses (current attendees on the Google event)
email_set_st = st.frozensets(email_st, min_size=0, max_size=8)

# Strategy: a set of email addresses for existing Notion rows (may overlap)
notion_email_set_st = st.frozensets(email_st, min_size=0, max_size=8)


# ---------------------------------------------------------------------------
# Property 3b – Removed attendee detection
# ---------------------------------------------------------------------------


@given(
    event_id=st.text(min_size=1, max_size=40),
    current_emails=email_set_st,
    notion_emails=notion_email_set_st,
)
@settings(max_examples=100, deadline=None)
def test_removed_attendee_detection(
    event_id: str,
    current_emails: frozenset,
    notion_emails: frozenset,
):
    """**Validates: Requirements 4.3**

    For any non-cancelled changed event with a set of current attendees, and
    any set of existing Notion rows for that event's Event ID, the
    _trash_removed_attendees function SHALL trash exactly those Notion rows
    whose attendee email is not present in the current Google attendee list,
    and SHALL not trash any row whose email is present.
    """
    # Build a non-cancelled event with the generated current attendees
    event = {
        "id": event_id,
        "status": "confirmed",
        "attendees": [{"email": e} for e in current_emails],
    }

    # Build Notion rows for the existing emails, each with a unique page ID
    existing_rows = [
        _notion_row(f"page-{i}", email)
        for i, email in enumerate(sorted(notion_emails))
    ]

    trashed_ids: list[str] = []

    with patch("rsvp_sync.handler.notion_rsvp") as mock_notion:
        mock_notion.query_by_event_id.return_value = existing_rows
        mock_notion.trash_rsvp_row.side_effect = lambda pid: trashed_ids.append(pid)

        _trash_removed_attendees([event])

    # Determine which rows should have been trashed
    expected_trashed = {
        row["id"]
        for row in existing_rows
        if row["properties"]["Attendee Email"]["email"] not in current_emails
    }

    # Determine which rows should NOT have been trashed
    expected_kept = {
        row["id"]
        for row in existing_rows
        if row["properties"]["Attendee Email"]["email"] in current_emails
    }

    trashed_set = set(trashed_ids)

    # Exactly the right rows were trashed
    assert trashed_set == expected_trashed, (
        f"Expected trashed: {expected_trashed}, got: {trashed_set}"
    )

    # No kept rows were trashed
    assert trashed_set.isdisjoint(expected_kept), (
        f"Rows that should be kept were trashed: {trashed_set & expected_kept}"
    )
