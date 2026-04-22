"""Property-based tests for RSVP sync data models.

Feature: rsvp-sync
Property 4: Row Key round-trip
"""

import os
import sys

# Add the source directory to the path so we can import the models
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "CTN_NotionMeeting_CalEvent"))

from hypothesis import given, settings
from hypothesis import strategies as st

from rsvp_sync.models import AttendeeRecord


# Strategy: generate strings that do not contain the "::" separator
no_separator = st.text(
    alphabet=st.characters(blacklist_characters=":"),
    min_size=1,
).filter(lambda s: "::" not in s)


@given(
    calendar_id=no_separator,
    event_id=no_separator,
    attendee_email=no_separator,
)
@settings(max_examples=100)
def test_row_key_round_trip(calendar_id: str, event_id: str, attendee_email: str):
    """**Validates: Requirements 5.1**

    For any valid calendar_id, event_id, and attendee_email (none containing
    the '::' separator), constructing a Row Key and parsing it back SHALL yield
    the original three components.
    """
    record = AttendeeRecord(
        calendar_id=calendar_id,
        event_id=event_id,
        event_name="test event",
        attendee_email=attendee_email,
        display_name="Test User",
        rsvp_status="accepted",
        is_organizer=False,
    )

    # Build the row key
    row_key = record.row_key

    # Parse it back
    parts = row_key.split("::")

    assert len(parts) == 3, f"Expected 3 parts, got {len(parts)}: {parts}"
    assert parts[0] == calendar_id
    assert parts[1] == event_id
    assert parts[2] == attendee_email
