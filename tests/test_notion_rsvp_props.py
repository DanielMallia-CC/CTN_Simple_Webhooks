"""Property-based tests for Notion RSVP writer.

Feature: rsvp-sync
Property 5: Upsert decision correctness
"""

import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "CTN_NotionMeeting_CalEvent")
)

from hypothesis import given, settings
from hypothesis import strategies as st

from rsvp_sync.models import AttendeeRecord

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

RSVP_STATUSES = st.sampled_from(["accepted", "declined", "tentative", "needsAction"])

attendee_records = st.builds(
    AttendeeRecord,
    calendar_id=st.text(min_size=1, max_size=30),
    event_id=st.text(min_size=1, max_size=30),
    event_name=st.text(min_size=1, max_size=60),
    attendee_email=st.emails(),
    display_name=st.text(min_size=1, max_size=40),
    rsvp_status=RSVP_STATUSES,
    is_organizer=st.booleans(),
    remove=st.booleans(),
)


def _make_existing_page(page_id: str, rsvp_status: str) -> dict:
    """Build a minimal Notion page dict that mirrors what query_by_row_key returns."""
    return {
        "id": page_id,
        "properties": {
            "RSVP Status": {
                "select": {"name": rsvp_status},
            },
        },
    }


# Strategy for the existing Notion state seen by upsert_or_trash.
# One of: None (no row), page with same status, page with different status.


def existing_state_strategy(record_status):
    """Return a strategy that produces (existing_page_or_none, label).

    Labels:
      "no_row"           – query_by_row_key returns None
      "same_status"      – existing row with identical RSVP status
      "different_status"  – existing row with a different RSVP status
    """
    page_id = st.uuids().map(str)

    no_row = st.just((None, "no_row"))
    same = page_id.map(lambda pid: (_make_existing_page(pid, record_status), "same_status"))

    # Pick a status that differs from the record's status
    other_statuses = [s for s in ["accepted", "declined", "tentative", "needsAction"] if s != record_status]
    different = st.tuples(
        page_id,
        st.sampled_from(other_statuses) if other_statuses else st.just("accepted"),
    ).map(lambda t: (_make_existing_page(t[0], t[1]), "different_status"))

    return st.one_of(no_row, same, different)


# ---------------------------------------------------------------------------
# Property 5 – Upsert decision correctness
# ---------------------------------------------------------------------------

@given(data=st.data())
@settings(max_examples=100, deadline=None)
@patch("rsvp_sync.notion_rsvp.trash_rsvp_row")
@patch("rsvp_sync.notion_rsvp.update_rsvp_row")
@patch("rsvp_sync.notion_rsvp.create_rsvp_row")
@patch("rsvp_sync.notion_rsvp.query_by_row_key")
def test_upsert_decision_correctness(
    mock_query: MagicMock,
    mock_create: MagicMock,
    mock_update: MagicMock,
    mock_trash: MagicMock,
    data,
):
    """**Validates: Requirements 5.2, 5.3, 5.4**

    For any AttendeeRecord and any existing Notion database state, upsert_or_trash
    SHALL perform exactly one of: create, update, trash, or no-op.

    Decision matrix:
      remove=True  + existing row           → trash
      remove=True  + no row                 → no-op
      remove=False + no row                 → create
      remove=False + existing row, diff     → update
      remove=False + existing row, same     → no-op
    """
    from rsvp_sync.notion_rsvp import upsert_or_trash

    record = data.draw(attendee_records, label="record")
    existing_page, label = data.draw(
        existing_state_strategy(record.rsvp_status), label="existing_state"
    )

    # Reset mocks for each Hypothesis example
    mock_query.reset_mock()
    mock_create.reset_mock()
    mock_update.reset_mock()
    mock_trash.reset_mock()

    mock_query.return_value = existing_page
    mock_create.return_value = "new-page-id"

    # --- Act ---
    upsert_or_trash(record)

    # --- Assert: query is always called exactly once ---
    mock_query.assert_called_once_with(record.row_key)

    # Collect call counts
    created = mock_create.call_count
    updated = mock_update.call_count
    trashed = mock_trash.call_count
    total_writes = created + updated + trashed

    if record.remove and existing_page is not None:
        # trash existing row
        assert trashed == 1, f"Expected 1 trash, got {trashed}"
        assert created == 0 and updated == 0
        mock_trash.assert_called_once_with(existing_page["id"])

    elif record.remove and existing_page is None:
        # no-op
        assert total_writes == 0, f"Expected no-op, got writes={total_writes}"

    elif not record.remove and existing_page is None:
        # create new row
        assert created == 1, f"Expected 1 create, got {created}"
        assert updated == 0 and trashed == 0
        mock_create.assert_called_once_with(record)

    elif not record.remove and label == "different_status":
        # update existing row
        assert updated == 1, f"Expected 1 update, got {updated}"
        assert created == 0 and trashed == 0
        mock_update.assert_called_once_with(existing_page["id"], record)

    elif not record.remove and label == "same_status":
        # no-op
        assert total_writes == 0, f"Expected no-op (same status), got writes={total_writes}"

    else:
        raise AssertionError(f"Unhandled case: remove={record.remove}, label={label}")


# ---------------------------------------------------------------------------
# Property 6 – Idempotent writes
# ---------------------------------------------------------------------------


class FakeNotionDB:
    """In-memory fake Notion database keyed by row_key."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self._next_id = 0

    def _new_page_id(self) -> str:
        self._next_id += 1
        return f"fake-page-{self._next_id}"

    def query_by_row_key(self, row_key: str):
        return self.rows.get(row_key)

    def create_rsvp_row(self, record) -> str:
        page_id = self._new_page_id()
        self.rows[record.row_key] = {
            "id": page_id,
            "properties": {
                "RSVP Status": {"select": {"name": record.rsvp_status}},
                "Row Key": {"rich_text": [{"text": {"content": record.row_key}}]},
            },
        }
        return page_id

    def update_rsvp_row(self, page_id: str, record) -> None:
        # Find the row by page_id and update its status
        for key, row in self.rows.items():
            if row["id"] == page_id:
                row["properties"]["RSVP Status"]["select"]["name"] = record.rsvp_status
                return

    def trash_rsvp_row(self, page_id: str) -> None:
        # Remove the row with matching page_id
        to_delete = [k for k, v in self.rows.items() if v["id"] == page_id]
        for k in to_delete:
            del self.rows[k]

    def snapshot(self) -> dict:
        """Return a deep-ish copy of current state for comparison."""
        import copy
        return copy.deepcopy(self.rows)


@given(record=attendee_records.filter(lambda r: not r.remove))
@settings(max_examples=100, deadline=None)
@patch("rsvp_sync.notion_rsvp.trash_rsvp_row")
@patch("rsvp_sync.notion_rsvp.update_rsvp_row")
@patch("rsvp_sync.notion_rsvp.create_rsvp_row")
@patch("rsvp_sync.notion_rsvp.query_by_row_key")
def test_idempotent_writes_create_then_noop(
    mock_query: MagicMock,
    mock_create: MagicMock,
    mock_update: MagicMock,
    mock_trash: MagicMock,
    record,
):
    """**Validates: Requirements 5.5, 8.3**

    For any AttendeeRecord with remove=False, processing it through
    upsert_or_trash twice SHALL produce the same Notion database state as
    processing it once.  The second invocation SHALL be a no-op.
    """
    from rsvp_sync.notion_rsvp import upsert_or_trash

    db = FakeNotionDB()

    # Wire mocks to the fake DB
    mock_query.side_effect = lambda rk: db.query_by_row_key(rk)
    mock_create.side_effect = lambda rec: db.create_rsvp_row(rec)
    mock_update.side_effect = lambda pid, rec: db.update_rsvp_row(pid, rec)
    mock_trash.side_effect = lambda pid: db.trash_rsvp_row(pid)

    # --- First pass: should create ---
    upsert_or_trash(record)
    state_after_first = db.snapshot()

    assert len(state_after_first) == 1, "Expected exactly one row after first pass"

    # Reset call counts (but keep side_effects wired to the same db)
    mock_query.reset_mock(side_effect=False)
    mock_create.reset_mock(side_effect=False)
    mock_update.reset_mock(side_effect=False)
    mock_trash.reset_mock(side_effect=False)
    mock_query.side_effect = lambda rk: db.query_by_row_key(rk)
    mock_create.side_effect = lambda rec: db.create_rsvp_row(rec)
    mock_update.side_effect = lambda pid, rec: db.update_rsvp_row(pid, rec)
    mock_trash.side_effect = lambda pid: db.trash_rsvp_row(pid)

    # --- Second pass: should be no-op ---
    upsert_or_trash(record)
    state_after_second = db.snapshot()

    assert state_after_first == state_after_second, (
        f"State changed after second pass!\n"
        f"After first:  {state_after_first}\n"
        f"After second: {state_after_second}"
    )

    # Second pass should not have created, updated, or trashed anything
    assert mock_create.call_count == 0, f"Unexpected create on second pass"
    assert mock_update.call_count == 0, f"Unexpected update on second pass"
    assert mock_trash.call_count == 0, f"Unexpected trash on second pass"


@given(record=attendee_records.filter(lambda r: r.remove))
@settings(max_examples=100, deadline=None)
@patch("rsvp_sync.notion_rsvp.trash_rsvp_row")
@patch("rsvp_sync.notion_rsvp.update_rsvp_row")
@patch("rsvp_sync.notion_rsvp.create_rsvp_row")
@patch("rsvp_sync.notion_rsvp.query_by_row_key")
def test_idempotent_writes_trash_then_noop(
    mock_query: MagicMock,
    mock_create: MagicMock,
    mock_update: MagicMock,
    mock_trash: MagicMock,
    record,
):
    """**Validates: Requirements 5.5, 8.3**

    For any AttendeeRecord with remove=True, processing it through
    upsert_or_trash twice SHALL produce the same Notion database state as
    processing it once.  If a row existed, the first call trashes it; the
    second call is a no-op (no row to trash).
    """
    from rsvp_sync.notion_rsvp import upsert_or_trash

    db = FakeNotionDB()

    # Pre-populate the fake DB with a row for this record so the first
    # pass has something to trash.
    db.create_rsvp_row(record)
    assert len(db.rows) == 1

    # Wire mocks to the fake DB
    mock_query.side_effect = lambda rk: db.query_by_row_key(rk)
    mock_create.side_effect = lambda rec: db.create_rsvp_row(rec)
    mock_update.side_effect = lambda pid, rec: db.update_rsvp_row(pid, rec)
    mock_trash.side_effect = lambda pid: db.trash_rsvp_row(pid)

    # --- First pass: should trash the existing row ---
    upsert_or_trash(record)
    state_after_first = db.snapshot()

    assert len(state_after_first) == 0, "Expected empty DB after trashing"

    # Reset call counts
    mock_query.reset_mock(side_effect=False)
    mock_create.reset_mock(side_effect=False)
    mock_update.reset_mock(side_effect=False)
    mock_trash.reset_mock(side_effect=False)
    mock_query.side_effect = lambda rk: db.query_by_row_key(rk)
    mock_create.side_effect = lambda rec: db.create_rsvp_row(rec)
    mock_update.side_effect = lambda pid, rec: db.update_rsvp_row(pid, rec)
    mock_trash.side_effect = lambda pid: db.trash_rsvp_row(pid)

    # --- Second pass: should be no-op (nothing to trash) ---
    upsert_or_trash(record)
    state_after_second = db.snapshot()

    assert state_after_first == state_after_second, (
        f"State changed after second pass!\n"
        f"After first:  {state_after_first}\n"
        f"After second: {state_after_second}"
    )

    # Second pass should not have created, updated, or trashed anything
    assert mock_create.call_count == 0, f"Unexpected create on second pass"
    assert mock_update.call_count == 0, f"Unexpected update on second pass"
    assert mock_trash.call_count == 0, f"Unexpected trash on second pass"
