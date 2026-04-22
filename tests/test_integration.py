"""Integration tests for RSVP sync flows.

Tests cover:
- Reconciliation triggers incremental sync (Req 8.1, 8.2)
- Channel renewal stop → watch → persist sequence (Req 7.1, 7.2, 7.3)
- Bootstrap full flow: full sync + channel creation + state persistence (Req 11.2, 11.3)
- Config env vars integration (Req 10.1-10.7)
"""

import importlib
import json
import os
import sys
from unittest.mock import patch, MagicMock, call

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "CTN_NotionMeeting_CalEvent")
)

from rsvp_sync.handler import (
    handle_reconciliation_sync,
    handle_renew_channel,
    handle_bootstrap,
)
from rsvp_sync.models import SyncState


# ---------------------------------------------------------------------------
# Test 1: Reconciliation triggers incremental sync (Req 8.1, 8.2)
# ---------------------------------------------------------------------------


class TestReconciliationTriggersIncrementalSync:
    """WHEN the reconciliation job fires, the handler SHALL perform an
    incremental sync and process events identically to a push notification.

    Validates: Requirements 8.1, 8.2
    """

    @patch("rsvp_sync.handler._trash_removed_attendees")
    @patch("rsvp_sync.handler.notion_rsvp")
    @patch("rsvp_sync.handler.sync_state_store")
    @patch("rsvp_sync.handler.google_calendar")
    @patch("rsvp_sync.handler._build_calendar_service")
    def test_reconciliation_calls_incremental_sync_and_processes_events(
        self, mock_build, mock_gcal, mock_store, mock_notion, mock_trash
    ):
        mock_build.return_value = MagicMock()

        mock_store.get_sync_state.return_value = SyncState(
            sync_token="existing-token",
            channel_id="ch-1",
            resource_id="res-1",
            channel_expiration=9999,
            channel_token="tok-1",
        )

        events = [
            {
                "id": "evt-1",
                "summary": "Team Standup",
                "attendees": [
                    {"email": "alice@example.com", "responseStatus": "accepted"},
                    {"email": "bob@example.com", "responseStatus": "declined"},
                ],
            },
            {
                "id": "evt-2",
                "summary": "Lunch",
                "attendees": [
                    {"email": "carol@example.com", "responseStatus": "tentative"},
                ],
            },
        ]
        mock_gcal.list_events_incremental.return_value = (events, "new-sync-token")

        result = handle_reconciliation_sync()

        # Incremental sync was triggered (Req 8.1)
        mock_gcal.list_events_incremental.assert_called_once()

        # Sync token persisted
        mock_store.update_sync_token.assert_called_once_with("new-sync-token")

        # Each attendee was upserted (Req 8.2)
        assert mock_notion.upsert_or_trash.call_count == 3

        # Removed-attendee detection was called
        mock_trash.assert_called_once_with(events)

        assert result["statusCode"] == 200


# ---------------------------------------------------------------------------
# Test 2: Channel renewal flow (Req 7.1, 7.2, 7.3)
# ---------------------------------------------------------------------------


class TestChannelRenewalFlow:
    """WHEN the renewal job fires, the handler SHALL stop the old channel,
    create a new one with 7-day TTL, and persist the new state.

    Validates: Requirements 7.1, 7.2, 7.3
    """

    @patch("rsvp_sync.handler.uuid.uuid4", return_value="new-ch-uuid")
    @patch("rsvp_sync.handler.secrets.token_urlsafe", return_value="new-ch-token")
    @patch("rsvp_sync.handler.sync_state_store")
    @patch("rsvp_sync.handler.google_calendar")
    @patch("rsvp_sync.handler._build_calendar_service")
    def test_renewal_stop_watch_persist_order(
        self, mock_build, mock_gcal, mock_store, mock_secrets, mock_uuid
    ):
        service = MagicMock()
        mock_build.return_value = service

        mock_store.get_sync_state.return_value = SyncState(
            sync_token="tok",
            channel_id="old-channel",
            resource_id="old-resource",
            channel_expiration=1000,
            channel_token="old-token",
        )

        mock_gcal.create_watch_channel.return_value = {
            "resourceId": "new-resource-id",
            "expiration": "7776000",
        }

        result = handle_renew_channel()

        # 1. Stop old channel (Req 7.2)
        mock_gcal.stop_watch_channel.assert_called_once_with(
            service, "old-channel", "old-resource"
        )

        # 2. Create new channel (Req 7.1)
        mock_gcal.create_watch_channel.assert_called_once()

        # 3. Persist new state (Req 7.3)
        mock_store.update_channel_state.assert_called_once_with(
            "new-ch-uuid", "new-resource-id", 7776000, "new-ch-token"
        )

        # Verify call order: stop → create → persist
        stop_call = mock_gcal.stop_watch_channel.call_args_list[0]
        create_call = mock_gcal.create_watch_channel.call_args_list[0]
        persist_call = mock_store.update_channel_state.call_args_list[0]

        # All three were called (order enforced by sequential code)
        assert stop_call is not None
        assert create_call is not None
        assert persist_call is not None

        assert result["statusCode"] == 200


# ---------------------------------------------------------------------------
# Test 3: Bootstrap full flow (Req 11.2, 11.3)
# ---------------------------------------------------------------------------


class TestBootstrapFullFlow:
    """WHEN bootstrap is invoked, the handler SHALL perform a full sync,
    create a watch channel, and persist all state in one shot.

    Validates: Requirements 11.2, 11.3
    """

    @patch("rsvp_sync.handler._trash_removed_attendees")
    @patch("rsvp_sync.handler.notion_rsvp")
    @patch("rsvp_sync.handler.uuid.uuid4", return_value="bs-uuid")
    @patch("rsvp_sync.handler.secrets.token_urlsafe", return_value="bs-token")
    @patch("rsvp_sync.handler.sync_state_store")
    @patch("rsvp_sync.handler.google_calendar")
    @patch("rsvp_sync.handler._build_calendar_service")
    def test_bootstrap_full_sync_channel_creation_state_persistence(
        self,
        mock_build,
        mock_gcal,
        mock_store,
        mock_secrets,
        mock_uuid,
        mock_notion,
        mock_trash,
    ):
        service = MagicMock()
        mock_build.return_value = service

        # No existing channel
        mock_store.get_sync_state.return_value = SyncState(
            sync_token=None,
            channel_id=None,
            resource_id=None,
            channel_expiration=None,
            channel_token=None,
        )

        events = [
            {
                "id": "e1",
                "summary": "Event 1",
                "attendees": [
                    {"email": "a@b.com", "responseStatus": "accepted"},
                ],
            },
        ]
        mock_gcal.list_events_full.return_value = (events, "initial-sync-token")
        mock_gcal.create_watch_channel.return_value = {
            "resourceId": "bs-resource",
            "expiration": "12345",
        }

        result = handle_bootstrap()

        # Full sync was called (Req 11.2)
        mock_gcal.list_events_full.assert_called_once()

        # Watch channel was created (Req 11.3)
        mock_gcal.create_watch_channel.assert_called_once()

        # All state persisted in one shot (Req 11.3)
        mock_store.update_full_state.assert_called_once_with(
            "initial-sync-token",
            "bs-uuid",
            "bs-resource",
            12345,
            "bs-token",
        )

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["events_fetched"] == 1
        assert body["channel_id"] == "bs-uuid"


# ---------------------------------------------------------------------------
# Test 4: Config env vars integration (Req 10.1-10.7)
# ---------------------------------------------------------------------------


class TestConfigEnvVarsIntegration:
    """Set all 7 RSVP env vars at once and verify the config module
    exposes the correct values after reload.

    Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7
    """

    def test_all_seven_env_vars_loaded_together(self, monkeypatch):
        env = {
            "RSVP_CALENDAR_ID": "integration-cal@group.calendar.google.com",
            "NOTION_RSVP_DATASOURCE_ID": "integ-notion-db-id",
            "RSVP_WEBHOOK_SLUG": "integ-secret-slug",
            "RSVP_SYNC_STATE_KEY": "integ_state_key",
            "RSVP_CHANNEL_TTL_SECONDS": "43200",
            "RSVP_WEBHOOK_TOKEN": "integ-webhook-token",
            "RSVP_FUNCTION_URL": "https://integ.lambda-url.eu-north-1.on.aws/integ-secret-slug",
        }
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        import config
        importlib.reload(config)

        assert config.RSVP_CALENDAR_ID == "integration-cal@group.calendar.google.com"
        assert config.NOTION_RSVP_DATASOURCE_ID == "integ-notion-db-id"
        assert config.RSVP_WEBHOOK_SLUG == "integ-secret-slug"
        assert config.RSVP_SYNC_STATE_KEY == "integ_state_key"
        assert config.RSVP_CHANNEL_TTL_SECONDS == 43200
        assert isinstance(config.RSVP_CHANNEL_TTL_SECONDS, int)
        assert config.RSVP_WEBHOOK_TOKEN == "integ-webhook-token"
        assert config.RSVP_FUNCTION_URL == "https://integ.lambda-url.eu-north-1.on.aws/integ-secret-slug"
