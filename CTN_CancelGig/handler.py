"""
Lambda handler for the CTN CancelGig webhook.

Cancels Google Calendar events for related pages (Musician Portal, Meetings,
Site Visits), archives them, then marks the Gig page as Cancelled.
"""

from __future__ import annotations

import json
import time
import logging
import base64
from urllib.parse import urlparse, parse_qs
from typing import Any, Dict, Optional, List, Tuple

import boto3
import requests
from backoff import on_exception, expo
from botocore.exceptions import ClientError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import (
    NOTION_TOKEN_SECRET,
    NOTION_API_VERSION,
    NOTION_API_BASE,
    REGION_NAME,
    DYNAMO_TABLE,
    SECRET_NAME,
    GIGS_STATUS_PROPERTY_NAME,
    GIGS_CANCELLED_STATUS_NAME,
    GIGS_CANCELLATION_SENT_PROP,
    GIGS_PORTAL_RELATION_NAME,
    GIGS_MEETINGS_RELATION_NAME,
    GIGS_SITE_VISITS_RELATION_NAME,
    GOOGLE_EVENT_ID_PROP,
    GOOGLE_EVENT_URL_PROP,
    DEFAULT_CALENDAR_ID,
)

# ----------------------------
# Logging
# ----------------------------
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(h)

# ----------------------------
# AWS clients
# ----------------------------
dynamodb = boto3.client("dynamodb", region_name=REGION_NAME)
secrets_manager = boto3.client("secretsmanager", region_name=REGION_NAME)

# ----------------------------
# Notion token (Secrets Manager, cached per container)
# ----------------------------
_cached_token: Optional[str] = None


def _get_notion_token() -> str:
    global _cached_token
    if _cached_token is not None:
        return _cached_token
    secret_value = secrets_manager.get_secret_value(SecretId=NOTION_TOKEN_SECRET)
    secret_dict = json.loads(secret_value["SecretString"])
    _cached_token = secret_dict["INTERNAL_NOTION_API_KEY"]
    return _cached_token


def _build_session() -> requests.Session:
    token = _get_notion_token()
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_API_VERSION,
        "Content-Type": "application/json",
    })
    return s


_session: Optional[requests.Session] = None


def _sess() -> requests.Session:
    global _session
    if _session is None:
        _session = _build_session()
    return _session


# ----------------------------
# Notion helpers
# ----------------------------
@on_exception(expo, requests.RequestException, max_tries=3, max_time=10)
def retrieve_page(page_id: str) -> Dict[str, Any]:
    resp = _sess().get(f"{NOTION_API_BASE}/pages/{page_id}")
    resp.raise_for_status()
    return resp.json()


@on_exception(expo, requests.RequestException, max_tries=3, max_time=10)
def archive_page(page_id: str) -> None:
    resp = _sess().patch(f"{NOTION_API_BASE}/pages/{page_id}", json={"archived": True})
    resp.raise_for_status()


@on_exception(expo, requests.RequestException, max_tries=3, max_time=10)
def patch_page(page_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    resp = _sess().patch(f"{NOTION_API_BASE}/pages/{page_id}", json=payload)
    resp.raise_for_status()
    return resp.json()


@on_exception(expo, requests.RequestException, max_tries=3, max_time=10)
def fetch_notion_user_email(user_id: str) -> Optional[str]:
    resp = _sess().get(f"{NOTION_API_BASE}/users/{user_id}")
    resp.raise_for_status()
    obj = resp.json()
    person = obj.get("person") or {}
    return person.get("email")


def extract_relation_ids(page_obj: Dict[str, Any], relation_prop_name: str) -> List[str]:
    props = page_obj.get("properties") or {}
    node = props.get(relation_prop_name)
    if not node or node.get("type") != "relation":
        return []
    return [x["id"] for x in (node.get("relation") or []) if x.get("id")]


def extract_text_value(page_obj: Dict[str, Any], prop_name: str) -> Optional[str]:
    props = page_obj.get("properties") or {}
    node = props.get(prop_name)
    if not node:
        return None

    t = node.get("type")
    if t == "rich_text":
        parts = node.get("rich_text") or []
        val = "".join(p.get("plain_text", "") for p in parts).strip()
        return val or None
    if t == "url":
        return node.get("url") or None
    if t == "title":
        parts = node.get("title") or []
        val = "".join(p.get("plain_text", "") for p in parts).strip()
        return val or None
    return None


# ----------------------------
# Google helpers
# ----------------------------
@on_exception(expo, ClientError, max_tries=3, max_time=10)
def get_db_item(client_id: str) -> Optional[Dict[str, Any]]:
    resp = dynamodb.get_item(
        TableName=DYNAMO_TABLE,
        Key={"client_id": {"S": client_id}},
    )
    item = resp.get("Item")
    if not item:
        return None
    return {k: list(v.values())[0] for k, v in item.items()}


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


def load_service_for_client_id(client_id: str) -> Any:
    rec = get_db_item(client_id)
    if not rec:
        raise KeyError(f"No GoogleAuthTokens record for client_id={client_id}")
    rt = rec.get("refresh_token")
    if not rt:
        raise KeyError(f"Missing refresh_token for client_id={client_id}")
    creds = get_google_credentials(rt)
    return build("calendar", "v3", credentials=creds)


def _urlsafe_b64decode_padded(s: str) -> bytes:
    padding_needed = (4 - len(s) % 4) % 4
    return base64.urlsafe_b64decode((s + ("=" * padding_needed)).encode("utf-8"))


def parse_event_from_google_url(google_event_url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (event_id, calendar_id_or_email_if_present).
    Supports:
      - ?eid=<base64url("eventId calendarId")>
      - /eventedit/<eventId> (no calendar id)
    """
    if not google_event_url:
        return (None, None)

    parsed = urlparse(google_event_url)
    qs = parse_qs(parsed.query)
    eid_vals = qs.get("eid") or []
    if eid_vals:
        decoded = _urlsafe_b64decode_padded(eid_vals[0]).decode("utf-8", errors="replace")
        parts = decoded.split(" ")
        event_id = parts[0] if parts and parts[0] else None
        calendar_id = parts[1] if len(parts) > 1 else None
        return (event_id, calendar_id)

    path_parts = parsed.path.strip("/").split("/")
    if "eventedit" in path_parts:
        idx = path_parts.index("eventedit")
        if idx + 1 < len(path_parts):
            return (path_parts[idx + 1], None)

    return (None, None)


def cancel_event_idempotent(service: Any, calendar_id: str, event_id: str) -> str:
    """Delete the event and notify guests. Treat 404/410 as success."""
    try:
        service.events().delete(
            calendarId=calendar_id,
            eventId=event_id,
            sendUpdates="all",
        ).execute()
        return "deleted"
    except HttpError as he:
        status = getattr(he, "resp", None).status if getattr(he, "resp", None) else None
        if status in (404, 410):
            return "already_deleted"
        raise


# ----------------------------
# Lambda handler
# ----------------------------
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    start = time.time()
    try:
        body_raw = event.get("body")
        body = json.loads(body_raw) if isinstance(body_raw, str) else (body_raw or event)

        data = body.get("data") or {}
        gig_page_id = data.get("id")
        if not gig_page_id:
            return _resp(400, {"ok": False, "error": "Missing data.id (Gig page id)."}, start)

        notion_user_id = (body.get("source") or {}).get("user_id")
        if not notion_user_id:
            return _resp(400, {"ok": False, "error": "Missing source.user_id"}, start)

        actor_email = fetch_notion_user_email(notion_user_id)
        if not actor_email:
            return _resp(400, {"ok": False, "error": "Unable to resolve Notion actor email"}, start)

        actor_client_id = actor_email.split("@")[0]
        actor_service = load_service_for_client_id(actor_client_id)

        try:
            gig_obj = retrieve_page(gig_page_id)
        except Exception as e:
            if "404" in str(e):
                logger.warning("Gig page %s not found (already processed or deleted), skipping.", gig_page_id)
                return _resp(200, {"ok": True, "skipped": True, "reason": "gig_page_not_found"}, start)
            raise

        gig_props = gig_obj.get("properties") or {}
        logger.info("Gig page %s properties keys: %s", gig_page_id, sorted(list(gig_props.keys())))

        portal_ids = extract_relation_ids(gig_obj, GIGS_PORTAL_RELATION_NAME)
        meeting_ids = extract_relation_ids(gig_obj, GIGS_MEETINGS_RELATION_NAME)
        site_visit_ids = extract_relation_ids(gig_obj, GIGS_SITE_VISITS_RELATION_NAME)

        _log_relation_node(gig_props, GIGS_PORTAL_RELATION_NAME)
        _log_relation_node(gig_props, GIGS_MEETINGS_RELATION_NAME)
        _log_relation_node(gig_props, GIGS_SITE_VISITS_RELATION_NAME)

        logger.info(
            "Collected from Gig relations: portals=%s meetings=%s site_visits=%s",
            len(portal_ids), len(meeting_ids), len(site_visit_ids),
        )

        portal_id_set = set(portal_ids)
        archive_ids = list({*meeting_ids, *site_visit_ids} - portal_id_set)

        processed: List[Dict[str, Any]] = []

        # --- Musician Portal pages: rename to CANCELLED_{name}, do NOT archive ---
        for pid in portal_ids:
            item: Dict[str, Any] = {"pageId": pid, "source": "portal"}
            try:
                pobj = retrieve_page(pid)

                title = extract_text_value(pobj, "Name") or ""
                item["title"] = title

                event_id_prop = extract_text_value(pobj, GOOGLE_EVENT_ID_PROP)
                event_url = extract_text_value(pobj, GOOGLE_EVENT_URL_PROP)
                item["event_id_prop"] = event_id_prop
                item["event_url"] = event_url

                url_event_id, url_calendar_id = (
                    parse_event_from_google_url(event_url) if event_url else (None, None)
                )
                final_event_id = url_event_id or event_id_prop
                final_calendar_id = (
                    (url_calendar_id if url_calendar_id and "@" in url_calendar_id else None)
                    or DEFAULT_CALENDAR_ID
                    or actor_email
                )

                event_state = "no_event"
                if final_event_id:
                    service = actor_service
                    if url_calendar_id and "@" in url_calendar_id:
                        cal_client_id = url_calendar_id.split("@")[0]
                        try:
                            service = load_service_for_client_id(cal_client_id)
                        except KeyError:
                            service = actor_service
                    event_state = cancel_event_idempotent(service, final_calendar_id, final_event_id)

                # Rename instead of archiving
                new_title = f"CANCELLED_{title}" if not title.startswith("CANCELLED_") else title
                patch_page(pid, {
                    "properties": {
                        "Name": {"title": [{"text": {"content": new_title}}]}
                    }
                })

                item.update({
                    "final_event_id": final_event_id,
                    "final_calendar_id": final_calendar_id,
                    "event_state": event_state,
                    "renamed": new_title,
                })
            except Exception as e:
                item["error"] = str(e)
            processed.append(item)

        # --- Meetings / Site Visits: cancel event + archive ---
        for pid in archive_ids:
            item = {"pageId": pid, "source": "meeting_or_site_visit"}
            try:
                pobj = retrieve_page(pid)

                title = extract_text_value(pobj, "Name")
                item["title"] = title

                event_id_prop = extract_text_value(pobj, GOOGLE_EVENT_ID_PROP)
                event_url = extract_text_value(pobj, GOOGLE_EVENT_URL_PROP)
                item["event_id_prop"] = event_id_prop
                item["event_url"] = event_url

                url_event_id, url_calendar_id = (
                    parse_event_from_google_url(event_url) if event_url else (None, None)
                )
                final_event_id = url_event_id or event_id_prop
                final_calendar_id = (
                    (url_calendar_id if url_calendar_id and "@" in url_calendar_id else None)
                    or DEFAULT_CALENDAR_ID
                    or actor_email
                )

                event_state = "no_event"
                if final_event_id:
                    service = actor_service
                    if url_calendar_id and "@" in url_calendar_id:
                        cal_client_id = url_calendar_id.split("@")[0]
                        try:
                            service = load_service_for_client_id(cal_client_id)
                        except KeyError:
                            service = actor_service
                    event_state = cancel_event_idempotent(service, final_calendar_id, final_event_id)

                archive_page(pid)

                item.update({
                    "final_event_id": final_event_id,
                    "final_calendar_id": final_calendar_id,
                    "event_state": event_state,
                    "archived": True,
                })
            except Exception as e:
                item["error"] = str(e)
            processed.append(item)

        # Update Gig page status + checkbox
        try:
            patch_page(gig_page_id, {
                "properties": {
                    GIGS_STATUS_PROPERTY_NAME: {"status": {"name": GIGS_CANCELLED_STATUS_NAME}},
                    GIGS_CANCELLATION_SENT_PROP: {"checkbox": True},
                }
            })
            gig_updated = True
        except Exception as e:
            gig_updated = False
            logger.error("Failed to update Gig status/checkbox: %s", str(e))

        payload = {
            "ok": True,
            "gigPageId": gig_page_id,
            "actorEmail": actor_email,
            "counts": {
                "portals_renamed": len(portal_ids),
                "meetings_archived": len(meeting_ids),
                "site_visits_archived": len(site_visit_ids),
                "processed": len(processed),
            },
            "gigUpdated": gig_updated,
            "processedSummary": [
                {"pageId": x.get("pageId"), "event_state": x.get("event_state"), "error": x.get("error")}
                for x in processed
            ],
        }
        return _resp(200, payload, start)

    except Exception:
        logger.exception("Unexpected error")
        return _resp(200, {"ok": False, "error": "internal_error"}, start)


def _log_relation_node(gig_props: Dict[str, Any], prop_name: str) -> None:
    node = gig_props.get(prop_name)
    if not node:
        logger.warning("Relation property missing on Gig payload: %s", prop_name)
        return
    t = node.get("type")
    rel = node.get("relation") or []
    has_more = node.get("has_more")
    logger.info("Relation %s present, type=%s, count=%s, has_more=%s", prop_name, t, len(rel), has_more)


def _resp(status_code: int, payload: Dict[str, Any], start_time: float) -> Dict[str, Any]:
    duration_ms = int((time.time() - start_time) * 1000)
    logger.info("Responding status=%s in %sms", status_code, duration_ms)
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
    }
