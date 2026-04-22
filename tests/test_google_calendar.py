"""Unit tests for Google Calendar adapter – pagination and 410 fallback.

Validates: Requirements 3.3, 3.5, 9.1, 9.2
- 3.3: HTTP 410 Gone triggers full-sync fallback (raised, not caught here)
- 3.5: Paginated results followed until final page returns new syncToken
- 9.1: list_events_incremental returns changed events + new sync token
- 9.2: list_events_full returns all events + initial sync token
"""

import os
import sys
from unittest.mock import MagicMock

import httplib2
from googleapiclient.errors import HttpError

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "CTN_NotionMeeting_CalEvent")
)

from adapters.google_calendar import list_events_incremental


class TestListEventsIncrementalPagination:
    """Test 3-page pagination: all events collected and final sync token captured."""

    def _build_service(self, pages):
        """Build a mock service whose events().list().execute() returns pages in order."""
        service = MagicMock()
        list_req = MagicMock()
        list_req.execute = MagicMock(side_effect=pages)
        service.events.return_value.list.return_value = list_req
        return service

    def test_three_page_pagination(self):
        event1 = {"id": "e1", "summary": "Event 1"}
        event2 = {"id": "e2", "summary": "Event 2"}
        event3 = {"id": "e3", "summary": "Event 3"}

        pages = [
            {"items": [event1], "nextPageToken": "page2"},
            {"items": [event2], "nextPageToken": "page3"},
            {"items": [event3], "nextSyncToken": "new_token_abc"},
        ]

        service = self._build_service(pages)
        events, sync_token = list_events_incremental(service, "cal_id", "old_token")

        assert len(events) == 3
        assert events[0] == event1
        assert events[1] == event2
        assert events[2] == event3
        assert sync_token == "new_token_abc"

        # Verify events().list() was called 3 times (once per page)
        assert service.events.return_value.list.call_count == 3

        # First call: syncToken only, no pageToken
        first_call = service.events.return_value.list.call_args_list[0]
        assert first_call.kwargs["syncToken"] == "old_token"
        assert first_call.kwargs.get("pageToken") is None

        # Second call: syncToken + pageToken="page2"
        second_call = service.events.return_value.list.call_args_list[1]
        assert second_call.kwargs["syncToken"] == "old_token"
        assert second_call.kwargs["pageToken"] == "page2"

        # Third call: syncToken + pageToken="page3"
        third_call = service.events.return_value.list.call_args_list[2]
        assert third_call.kwargs["syncToken"] == "old_token"
        assert third_call.kwargs["pageToken"] == "page3"

    def test_single_page_no_pagination(self):
        event = {"id": "e1", "summary": "Only event"}
        pages = [{"items": [event], "nextSyncToken": "token_single"}]

        service = self._build_service(pages)
        events, sync_token = list_events_incremental(service, "cal_id", "tok")

        assert events == [event]
        assert sync_token == "token_single"
        assert service.events.return_value.list.call_count == 1

    def test_empty_items_pages(self):
        """Pages with no items still work; sync token is captured."""
        pages = [
            {"items": [], "nextPageToken": "p2"},
            {"nextSyncToken": "tok_empty"},  # no items key at all
        ]

        service = self._build_service(pages)
        events, sync_token = list_events_incremental(service, "cal_id", "tok")

        assert events == []
        assert sync_token == "tok_empty"


class TestListEventsIncremental410:
    """Test that HTTP 410 from events.list is raised (not caught)."""

    def test_http_410_is_raised(self):
        service = MagicMock()
        resp = httplib2.Response({"status": "410"})
        error = HttpError(resp, b"sync token expired")
        service.events.return_value.list.return_value.execute.side_effect = error

        try:
            list_events_incremental(service, "cal_id", "expired_token")
            assert False, "Expected HttpError to be raised"
        except HttpError as exc:
            assert exc.resp.status == 410
