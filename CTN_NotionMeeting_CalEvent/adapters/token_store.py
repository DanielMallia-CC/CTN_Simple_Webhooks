import json
from typing import Any, Dict, Optional

import boto3
from backoff import on_exception, expo
from botocore.exceptions import ClientError
from google.oauth2.credentials import Credentials

from config import DYNAMO_TABLE, SECRET_NAME

dynamodb = boto3.client("dynamodb")
secrets_manager = boto3.client("secretsmanager")

@on_exception(expo, ClientError, max_tries=3, max_time=10)
def get_db_item(client_id: str) -> Optional[Dict[str, Any]]:
    resp = dynamodb.get_item(TableName=DYNAMO_TABLE, Key={"client_id": {"S": client_id}})
    item = resp.get("Item")
    if not item:
        return None
    return {k: list(v.values())[0] for k, v in item.items()}

@on_exception(expo, ClientError, max_tries=3, max_time=10)
def update_db_notion_id(client_id: str, notion_user_id: str) -> None:
    dynamodb.update_item(
        TableName=DYNAMO_TABLE,
        Key={"client_id": {"S": client_id}},
        UpdateExpression="SET notion_user_id = :u",
        ExpressionAttributeValues={":u": {"S": notion_user_id}},
    )

@on_exception(expo, ClientError, max_tries=3, max_time=10)
def get_google_credentials(refresh_token: str) -> Credentials:
    secret_val = secrets_manager.get_secret_value(SecretId=SECRET_NAME)
    creds_json = json.loads(secret_val["SecretString"])["web"]
    return Credentials(
        None,
        refresh_token=refresh_token,
        client_id=creds_json["client_id"],
        client_secret=creds_json["client_secret"],
        token_uri=creds_json["token_uri"],
    )