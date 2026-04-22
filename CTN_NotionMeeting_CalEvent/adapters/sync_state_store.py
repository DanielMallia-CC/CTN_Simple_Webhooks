from typing import Optional

import boto3
from backoff import on_exception, expo
from botocore.exceptions import ClientError

from config import DYNAMO_TABLE, RSVP_SYNC_STATE_KEY
from rsvp_sync.models import SyncState

dynamodb = boto3.client("dynamodb")


@on_exception(expo, ClientError, max_tries=3, max_time=10)
def get_sync_state() -> Optional[SyncState]:
    """Read the sync state DynamoDB item."""
    resp = dynamodb.get_item(
        TableName=DYNAMO_TABLE,
        Key={"client_id": {"S": RSVP_SYNC_STATE_KEY}},
    )
    item = resp.get("Item")
    if not item:
        return None
    return SyncState(
        sync_token=item["sync_token"]["S"] if "sync_token" in item else None,
        channel_id=item["channel_id"]["S"] if "channel_id" in item else None,
        resource_id=item["resource_id"]["S"] if "resource_id" in item else None,
        channel_expiration=int(item["channel_expiration"]["N"]) if "channel_expiration" in item else None,
        channel_token=item["channel_token"]["S"] if "channel_token" in item else None,
    )


@on_exception(expo, ClientError, max_tries=3, max_time=10)
def update_sync_token(token: str) -> None:
    """Atomically update the sync_token field."""
    dynamodb.update_item(
        TableName=DYNAMO_TABLE,
        Key={"client_id": {"S": RSVP_SYNC_STATE_KEY}},
        UpdateExpression="SET sync_token = :t",
        ExpressionAttributeValues={":t": {"S": token}},
    )


@on_exception(expo, ClientError, max_tries=3, max_time=10)
def update_channel_state(
    channel_id: str, resource_id: str, expiration: int, channel_token: str
) -> None:
    """Persist watch channel metadata."""
    dynamodb.update_item(
        TableName=DYNAMO_TABLE,
        Key={"client_id": {"S": RSVP_SYNC_STATE_KEY}},
        UpdateExpression=(
            "SET channel_id = :cid, resource_id = :rid, "
            "channel_expiration = :exp, channel_token = :ctk"
        ),
        ExpressionAttributeValues={
            ":cid": {"S": channel_id},
            ":rid": {"S": resource_id},
            ":exp": {"N": str(expiration)},
            ":ctk": {"S": channel_token},
        },
    )


@on_exception(expo, ClientError, max_tries=3, max_time=10)
def update_full_state(
    sync_token: str,
    channel_id: str,
    resource_id: str,
    expiration: int,
    channel_token: str,
) -> None:
    """Persist all fields at once (bootstrap)."""
    dynamodb.update_item(
        TableName=DYNAMO_TABLE,
        Key={"client_id": {"S": RSVP_SYNC_STATE_KEY}},
        UpdateExpression=(
            "SET sync_token = :st, channel_id = :cid, resource_id = :rid, "
            "channel_expiration = :exp, channel_token = :ctk"
        ),
        ExpressionAttributeValues={
            ":st": {"S": sync_token},
            ":cid": {"S": channel_id},
            ":rid": {"S": resource_id},
            ":exp": {"N": str(expiration)},
            ":ctk": {"S": channel_token},
        },
    )
