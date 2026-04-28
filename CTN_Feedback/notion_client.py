"""
Notion API client for the CTN Feedback service.

Reuses the same Secrets Manager pattern as CTN_NotionMeeting_CalEvent:
same secret ARN, same key (INTERNAL_NOTION_API_KEY), cached per container.
"""

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
    NOTION_API_BASE,
    REGION_NAME,
)

log = logging.getLogger(__name__)

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


_session: Optional[requests.Session] = None


def _sess() -> requests.Session:
    global _session
    if _session is None:
        _session = _get_session()
    return _session


@on_exception(expo, requests.RequestException, max_tries=3, max_time=30)
def create_page(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create a Notion page. Returns the response JSON."""
    url = f"{NOTION_API_BASE}/pages"
    log.info("[feedback] creating page in Notion")
    resp = _sess().post(url, json=payload, timeout=10)
    log.info("[feedback] create page status=%s", resp.status_code)
    resp.raise_for_status()
    return resp.json()


@on_exception(expo, requests.RequestException, max_tries=3, max_time=30)
def query_database(database_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Query a Notion database. Returns the response JSON."""
    url = f"{NOTION_API_BASE}/databases/{database_id}/query"
    log.info("[feedback] querying database %s", database_id)
    resp = _sess().post(url, json=payload, timeout=10)
    log.info("[feedback] query status=%s", resp.status_code)
    resp.raise_for_status()
    return resp.json()


@on_exception(expo, requests.RequestException, max_tries=3, max_time=30)
def get_database(database_id: str) -> Dict[str, Any]:
    """Retrieve a Notion database. Returns the response JSON."""
    url = f"{NOTION_API_BASE}/databases/{database_id}"
    log.info("[feedback] retrieving database %s", database_id)
    resp = _sess().get(url, timeout=10)
    log.info("[feedback] retrieve status=%s", resp.status_code)
    resp.raise_for_status()
    return resp.json()
