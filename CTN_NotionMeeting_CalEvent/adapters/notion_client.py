from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

import boto3
import requests
from backoff import on_exception, expo

from config import (
    NOTION_TOKEN_SECRET,
    NOTION_API_VERSION,
    NOTION_PAGES_ENDPOINT,
    NOTION_USER_ENDPOINT,
    REGION_NAME,
)

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)

_secrets_client = None
_cached_token: Optional[str] = None


def _get_secrets_client():
    global _secrets_client
    if _secrets_client is None:
        _secrets_client = boto3.client("secretsmanager", region_name=REGION_NAME)
    return _secrets_client


def _get_notion_token() -> str:
    """Fetch the Notion API token from AWS Secrets Manager (cached per container)."""
    global _cached_token
    if _cached_token is not None:
        return _cached_token
    try:
        secret_value: Dict[str, Any] = _get_secrets_client().get_secret_value(
            SecretId=NOTION_TOKEN_SECRET
        )
        secret_dict = json.loads(secret_value["SecretString"])
        _cached_token = secret_dict["INTERNAL_NOTION_API_KEY"]
        return _cached_token
    except Exception:
        log.exception("Failed to retrieve Notion token from Secrets Manager")
        raise


def _get_session() -> requests.Session:
    """Build a requests session with the Notion auth header."""
    token = _get_notion_token()
    s = requests.Session()
    s.headers.update(
        {
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_API_VERSION,
            "Content-Type": "application/json",
        }
    )
    return s


# Lazy-initialised session (created on first call)
_session: Optional[requests.Session] = None


def _sess() -> requests.Session:
    global _session
    if _session is None:
        _session = _get_session()
    return _session


@on_exception(expo, requests.RequestException, max_tries=3, max_time=30)
def fetch_notion_user_email(notion_user_id: str) -> Optional[str]:
    url = f"{NOTION_USER_ENDPOINT}/{notion_user_id}"
    log.info("[notion_client] fetching user email url=%s", url)
    resp = _sess().get(url, timeout=10)
    log.info("[notion_client] user response status=%s", resp.status_code)
    resp.raise_for_status()
    data = resp.json()
    email = data.get("person", {}).get("email")
    log.info("[notion_client] resolved email=%s for user=%s", email, notion_user_id)
    return email


@on_exception(expo, requests.RequestException, max_tries=3, max_time=30)
def update_page_properties(page_id: str, properties: Dict[str, Any]) -> None:
    payload = {"properties": properties}
    url = f"{NOTION_PAGES_ENDPOINT}/{page_id}"
    log.info("[notion_client] updating page properties url=%s props=%s", url, list(properties.keys()))
    resp = _sess().patch(url, json=payload, timeout=10)
    log.info("[notion_client] update response status=%s", resp.status_code)
    resp.raise_for_status()
