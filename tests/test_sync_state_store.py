"""Integration tests for sync state persistence.

Validates: Requirements 6.1, 6.2
- 6.1: All fields stored in a single DynamoDB item
- 6.2: Fixed well-known client_id partition key (RSVP_SYNC_STATE_KEY)
"""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "CTN_NotionMeeting_CalEvent")
)

import adapters.sync_state_store as store_mod


FAKE_TABLE = "TestTable"
FAKE_PK = "rsvp_sync_state"


@patch.object(store_mod, "DYNAMO_TABLE", FAKE_TABLE)
@patch.object(store_mod, "RSVP_SYNC_STATE_KEY", FAKE_PK)
class TestGetSyncState:
    """Tests for get_sync_state()."""

    def test_returns_none_when_no_item(self):
        mock_client = MagicMock()
        mock_client.get_item.return_value = {}

        with patch.object(store_mod, "_get_client", return_value=mock_client):
            result = store_mod.get_sync_state()

        assert result is None
        mock_client.get_item.assert_called_once_with(
            TableName=FAKE_TABLE,
            Key={"client_id": {"S": FAKE_PK}},
        )

    def test_returns_sync_state_when_item_exists(self):
        mock_client = MagicMock()
        mock_client.get_item.return_value = {
            "Item": {
                "client_id": {"S": FAKE_PK},
                "sync_token": {"S": "tok_abc"},
                "channel_id": {"S": "chan_123"},
                "resource_id": {"S": "res_456"},
                "channel_expiration": {"N": "1700000000"},
                "channel_token": {"S": "secret_tok"},
            }
        }

        with patch.object(store_mod, "_get_client", return_value=mock_client):
            result = store_mod.get_sync_state()

        assert result is not None
        assert result.sync_token == "tok_abc"
        assert result.channel_id == "chan_123"
        assert result.resource_id == "res_456"
        assert result.channel_expiration == 1700000000
        assert result.channel_token == "secret_tok"

    def test_returns_none_fields_when_optional_attributes_missing(self):
        mock_client = MagicMock()
        mock_client.get_item.return_value = {
            "Item": {
                "client_id": {"S": FAKE_PK},
            }
        }

        with patch.object(store_mod, "_get_client", return_value=mock_client):
            result = store_mod.get_sync_state()

        assert result is not None
        assert result.sync_token is None
        assert result.channel_id is None
        assert result.resource_id is None
        assert result.channel_expiration is None
        assert result.channel_token is None


@patch.object(store_mod, "DYNAMO_TABLE", FAKE_TABLE)
@patch.object(store_mod, "RSVP_SYNC_STATE_KEY", FAKE_PK)
class TestUpdateSyncToken:
    """Tests for update_sync_token()."""

    def test_calls_update_item_with_correct_params(self):
        mock_client = MagicMock()

        with patch.object(store_mod, "_get_client", return_value=mock_client):
            store_mod.update_sync_token("new_token_xyz")

        mock_client.update_item.assert_called_once_with(
            TableName=FAKE_TABLE,
            Key={"client_id": {"S": FAKE_PK}},
            UpdateExpression="SET sync_token = :t",
            ExpressionAttributeValues={":t": {"S": "new_token_xyz"}},
        )


@patch.object(store_mod, "DYNAMO_TABLE", FAKE_TABLE)
@patch.object(store_mod, "RSVP_SYNC_STATE_KEY", FAKE_PK)
class TestUpdateChannelState:
    """Tests for update_channel_state()."""

    def test_calls_update_item_with_correct_params(self):
        mock_client = MagicMock()

        with patch.object(store_mod, "_get_client", return_value=mock_client):
            store_mod.update_channel_state(
                channel_id="chan_new",
                resource_id="res_new",
                expiration=1800000000,
                channel_token="ctk_new",
            )

        mock_client.update_item.assert_called_once_with(
            TableName=FAKE_TABLE,
            Key={"client_id": {"S": FAKE_PK}},
            UpdateExpression=(
                "SET channel_id = :cid, resource_id = :rid, "
                "channel_expiration = :exp, channel_token = :ctk"
            ),
            ExpressionAttributeValues={
                ":cid": {"S": "chan_new"},
                ":rid": {"S": "res_new"},
                ":exp": {"N": "1800000000"},
                ":ctk": {"S": "ctk_new"},
            },
        )


@patch.object(store_mod, "DYNAMO_TABLE", FAKE_TABLE)
@patch.object(store_mod, "RSVP_SYNC_STATE_KEY", FAKE_PK)
class TestUpdateFullState:
    """Tests for update_full_state()."""

    def test_calls_update_item_with_all_fields(self):
        mock_client = MagicMock()

        with patch.object(store_mod, "_get_client", return_value=mock_client):
            store_mod.update_full_state(
                sync_token="st_full",
                channel_id="cid_full",
                resource_id="rid_full",
                expiration=1900000000,
                channel_token="ctk_full",
            )

        mock_client.update_item.assert_called_once_with(
            TableName=FAKE_TABLE,
            Key={"client_id": {"S": FAKE_PK}},
            UpdateExpression=(
                "SET sync_token = :st, channel_id = :cid, resource_id = :rid, "
                "channel_expiration = :exp, channel_token = :ctk"
            ),
            ExpressionAttributeValues={
                ":st": {"S": "st_full"},
                ":cid": {"S": "cid_full"},
                ":rid": {"S": "rid_full"},
                ":exp": {"N": "1900000000"},
                ":ctk": {"S": "ctk_full"},
            },
        )


@patch.object(store_mod, "DYNAMO_TABLE", FAKE_TABLE)
@patch.object(store_mod, "RSVP_SYNC_STATE_KEY", FAKE_PK)
class TestPKConsistency:
    """Verify the PK is always RSVP_SYNC_STATE_KEY across all functions."""

    def test_all_functions_use_correct_pk(self):
        mock_client = MagicMock()
        mock_client.get_item.return_value = {}

        with patch.object(store_mod, "_get_client", return_value=mock_client):
            store_mod.get_sync_state()
            store_mod.update_sync_token("t")
            store_mod.update_channel_state("c", "r", 0, "ct")
            store_mod.update_full_state("s", "c", "r", 0, "ct")

        expected_key = {"client_id": {"S": FAKE_PK}}

        # get_item call
        mock_client.get_item.assert_called_once()
        assert mock_client.get_item.call_args.kwargs["Key"] == expected_key

        # All three update_item calls
        assert mock_client.update_item.call_count == 3
        for call in mock_client.update_item.call_args_list:
            assert call.kwargs["Key"] == expected_key
