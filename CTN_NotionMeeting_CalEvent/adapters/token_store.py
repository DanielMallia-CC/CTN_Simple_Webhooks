import json
from typing import Any, Dict, Optional

import boto3
from backoff import on_exception, expo
from botocore.exceptions import ClientError
from google.oauth2.credentials import Credentials

from config import DYNAMO_TABLE, SECRET_NAME

_dynamodb = None
_secrets_manager = None


def _get_dynamodb():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.client("dynamodb")
    return _dynamodb


def _get_secrets_manager():
    global _secrets_manager
    if _secrets_manager is None:
        _secrets_manager = boto3.client("secretsmanager")
    return _secrets_manager


@on_exception(expo, ClientError, max_tries=3, max_time=10)
def get_db_item(client_id: str) -> Optional[Dict[str, Any]]:
    resp = _get_dynamodb().get_item(TableName=DYNAMO_TABLE, Key={"client_id": {"S": client_id}})
    item = resp.get("Item")
    if not item:
        return None
    return {k: list(v.values())[0] for k, v in item.items()}

@on_exception(expo, ClientError, max_tries=3, max_time=10)
def update_db_notion_id(client_id: str, notion_user_id: str) -> None:
    _get_dynamodb().update_item(
        TableName=DYNAMO_TABLE,
        Key={"client_id": {"S": client_id}},
        UpdateExpression="SET notion_user_id = :u",
        ExpressionAttributeValues={":u": {"S": notion_user_id}},
    )

@on_exception(expo, ClientError, max_tries=3, max_time=10)
def get_google_credentials(refresh_token: str) -> Credentials:
    secret_val = _get_secrets_manager().get_secret_value(SecretId=SECRET_NAME)
    creds_json = json.loads(secret_val["SecretString"])["web"]
    return Credentials(
        None,
        refresh_token=refresh_token,
        client_id=creds_json["client_id"],
        client_secret=creds_json["client_secret"],
        token_uri=creds_json["token_uri"],
    )
