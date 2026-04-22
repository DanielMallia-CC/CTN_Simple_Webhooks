import json
from typing import Any, Dict, Optional

import boto3
from backoff import on_exception, expo
from botocore.exceptions import ClientError
from google.oauth2.credentials import Credentials
import logging

from config import DYNAMO_TABLE, SECRET_NAME

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

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
    log.info("[token_store] get_db_item client_id=%s table=%s", client_id, DYNAMO_TABLE)
    resp = _get_dynamodb().get_item(TableName=DYNAMO_TABLE, Key={"client_id": {"S": client_id}})
    item = resp.get("Item")
    if not item:
        log.warning("[token_store] no DynamoDB item for client_id=%s", client_id)
        return None
    result = {k: list(v.values())[0] for k, v in item.items()}
    log.info("[token_store] found item keys=%s", list(result.keys()))
    return result

@on_exception(expo, ClientError, max_tries=3, max_time=10)
def update_db_notion_id(client_id: str, notion_user_id: str) -> None:
    log.info("[token_store] update_db_notion_id client_id=%s notion_user_id=%s", client_id, notion_user_id)
    _get_dynamodb().update_item(
        TableName=DYNAMO_TABLE,
        Key={"client_id": {"S": client_id}},
        UpdateExpression="SET notion_user_id = :u",
        ExpressionAttributeValues={":u": {"S": notion_user_id}},
    )

@on_exception(expo, ClientError, max_tries=3, max_time=10)
def get_google_credentials(refresh_token: str) -> Credentials:
    log.info("[token_store] fetching Google OAuth credentials from secret=%s", SECRET_NAME)
    secret_val = _get_secrets_manager().get_secret_value(SecretId=SECRET_NAME)
    creds_json = json.loads(secret_val["SecretString"])["web"]
    log.info("[token_store] credentials loaded, client_id=%s", creds_json.get("client_id"))
    return Credentials(
        None,
        refresh_token=refresh_token,
        client_id=creds_json["client_id"],
        client_secret=creds_json["client_secret"],
        token_uri=creds_json["token_uri"],
    )
