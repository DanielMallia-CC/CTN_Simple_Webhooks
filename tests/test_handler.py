"""Unit tests for RSVP sync handler orchestration.

Depends on: task 6.6 (handle_push_notification must be implemented first).
"""

import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "CTN_NotionMeeting_CalEvent")
)

from rsvp_sync.handler import handle_push_notification


# ---------------------------------------------------------------------------
# 6.5 – Sync ping returns HTTP 200 with no sync triggered (Requirement 2.5)
# ---------------------------------------------------------------------------


def test_sync_ping_returns_200_no_sync():
    """WHEN a push notification has X-Goog-Resource-State: sync,
    the handler SHALL return HTTP 200 without performing any sync.

    Validates: Requirement 2.5
    """
    event = {
        "rawPath": "/my-secret-slug/webhook",
        "headers": {
            "x-goog-resource-state": "sync",
            "x-goog-channel-id": "channel-123",
            "x-goog-resource-id": "resource-456",
            "x-goog-channel-token": "valid-token",
        },
    }

    with patch("rsvp_sync.handler._run_incremental_sync") as mock_sync, \
         patch("rsvp_sync.handler._validate_push", return_value=True):
        result = handle_push_notification(event)

    assert result["statusCode"] == 200
    mock_sync.assert_not_called()

import json
import httplib2
from googleapiclient.errors import HttpError

from rsvp_sync.handler import (
    _run_incremental_sync,
    handle_renew_channel,
    handle_bootstrap,
)
from rsvp_sync.models import SyncState


# ---------------------------------------------------------------------------
# 6.7 – Handler orchestration unit tests
# ---------------------------------------------------------------------------


class TestIncrementalSync410Fallback:
    """WHEN list_events_incremental raises HTTP 410 Gone,
    the handler SHALL fall back to a full sync.

    Validates: Requirement 3.3
    """

    @patch("rsvp_sync.handler.sync_state_store")
    @patch("rsvp_sync.handler.google_calendar")
    @patch("rsvp_sync.handler._build_calendar_service")
    def test_410_triggers_full_sync(self, mock_build, mock_gcal, mock_store):
        mock_build.return_value = MagicMock()

        mock_store.get_sync_state.return_value = SyncState(
            sync_token="old_token",
            channel_id=None,
            resource_id=None,
            channel_expiration=None,
            channel_token=None,
        )

        resp_410 = httplib2.Response({"status": "410"})
        mock_gcal.list_events_incremental.side_effect = HttpError(
            resp_410, b"sync token expired"
        )
        mock_gcal.list_events_full.return_value = ([], "new_token")

        events = _run_incremental_sync()

        mock_gcal.list_events_full.assert_called_once()
        mock_store.update_sync_token.assert_called_with("new_token")
        assert events == []


class TestStopChannelFailureContinues:
    """WHEN stopping the old watch channel fails with HTTP 404,
    the handler SHALL log the failure and continue with new channel creation.

    Validates: Requirement 7.4
    """

    @patch("rsvp_sync.handler.uuid.uuid4", return_value="new-uuid")
    @patch("rsvp_sync.handler.secrets.token_urlsafe", return_value="random-token")
    @patch("rsvp_sync.handler.sync_state_store")
    @patch("rsvp_sync.handler.google_calendar")
    @patch("rsvp_sync.handler._build_calendar_service")
    def test_stop_404_continues_with_new_channel(
        self, mock_build, mock_gcal, mock_store, mock_secrets, mock_uuid
    ):
        mock_build.return_value = MagicMock()

        mock_store.get_sync_state.return_value = SyncState(
            sync_token="tok",
            channel_id="old-channel",
            resource_id="old-resource",
            channel_expiration=9999,
            channel_token="old-token",
        )

        resp_404 = httplib2.Response({"status": "404"})
        mock_gcal.stop_watch_channel.side_effect = HttpError(
            resp_404, b"channel not found"
        )
        mock_gcal.create_watch_channel.return_value = {
            "resourceId": "new-res",
            "expiration": "123456",
        }

        result = handle_renew_channel()

        mock_gcal.create_watch_channel.assert_called_once()
        mock_store.update_channel_state.assert_called_once_with(
            "new-uuid", "new-res", 123456, "random-token"
        )
        assert result["statusCode"] == 200


class TestBootstrapStopsExistingChannel:
    """WHEN handle_bootstrap is called and a watch channel already exists,
    the handler SHALL stop the existing channel before creating a new one.

    Validates: Requirement 11.4
    """

    @patch("rsvp_sync.handler._trash_removed_attendees")
    @patch("rsvp_sync.handler.notion_rsvp")
    @patch("rsvp_sync.handler.uuid.uuid4", return_value="bs-uuid")
    @patch("rsvp_sync.handler.secrets.token_urlsafe", return_value="bs-token")
    @patch("rsvp_sync.handler.sync_state_store")
    @patch("rsvp_sync.handler.google_calendar")
    @patch("rsvp_sync.handler._build_calendar_service")
    def test_existing_channel_stopped_first(
        self,
        mock_build,
        mock_gcal,
        mock_store,
        mock_secrets,
        mock_uuid,
        mock_notion,
        mock_trash,
    ):
        mock_build.return_value = MagicMock()

        mock_store.get_sync_state.return_value = SyncState(
            sync_token="tok",
            channel_id="existing-ch",
            resource_id="existing-res",
            channel_expiration=9999,
            channel_token="existing-tok",
        )

        mock_gcal.list_events_full.return_value = ([], "new-sync-tok")
        mock_gcal.create_watch_channel.return_value = {
            "resourceId": "new-res",
            "expiration": "999",
        }

        handle_bootstrap()

        mock_gcal.stop_watch_channel.assert_called_once()
        call_args = mock_gcal.stop_watch_channel.call_args
        assert call_args[0][1] == "existing-ch"
        assert call_args[0][2] == "existing-res"


class TestBootstrapResponseSummary:
    """WHEN handle_bootstrap completes, the response body SHALL contain
    the number of events fetched and the channel ID created.

    Validates: Requirement 11.5
    """

    @patch("rsvp_sync.handler._trash_removed_attendees")
    @patch("rsvp_sync.handler.notion_rsvp")
    @patch("rsvp_sync.handler.uuid.uuid4", return_value="resp-uuid")
    @patch("rsvp_sync.handler.secrets.token_urlsafe", return_value="resp-token")
    @patch("rsvp_sync.handler.sync_state_store")
    @patch("rsvp_sync.handler.google_calendar")
    @patch("rsvp_sync.handler._build_calendar_service")
    def test_response_contains_event_count_and_channel_id(
        self,
        mock_build,
        mock_gcal,
        mock_store,
        mock_secrets,
        mock_uuid,
        mock_notion,
        mock_trash,
    ):
        mock_build.return_value = MagicMock()

        mock_store.get_sync_state.return_value = SyncState(
            sync_token=None,
            channel_id=None,
            resource_id=None,
            channel_expiration=None,
            channel_token=None,
        )

        event1 = {"id": "e1", "summary": "Ev1", "attendees": [{"email": "a@b.com", "responseStatus": "accepted"}]}
        event2 = {"id": "e2", "summary": "Ev2", "attendees": [{"email": "c@d.com", "responseStatus": "declined"}]}
        mock_gcal.list_events_full.return_value = ([event1, event2], "sync-tok")
        mock_gcal.create_watch_channel.return_value = {
            "resourceId": "res-id",
            "expiration": "5000",
        }

        result = handle_bootstrap()

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["events_fetched"] == 2
        assert body["channel_id"] == "resp-uuid"
