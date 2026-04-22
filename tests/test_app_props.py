"""Property-based tests for source routing in app.py.

Feature: rsvp-sync
Property 1: Source routing correctness
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# We must mock modules that app.py imports at the top level *before* we
# import app itself, because ``from invites import HANDLERS`` requires
# env-vars that won't be set in CI.
# ---------------------------------------------------------------------------

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "CTN_NotionMeeting_CalEvent")
)

# Pre-populate required env vars so config.py doesn't blow up
os.environ.setdefault("MUSICIAN_PORTAL_DB_ID", "fake-musician-db")
os.environ.setdefault("MEETINGS_DB_ID", "fake-meetings-db")
os.environ.setdefault("SITE_VISITS_DB_ID", "fake-site-visits-db")

from hypothesis import given, settings, assume
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Non-empty printable text (no control chars) for header values / field values
safe_text = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S")),
    min_size=1,
    max_size=40,
)

# A known database_id that our mock HANDLERS dict will recognise
KNOWN_DB_ID = "known-db-id-1234"

# Strategy: Google push notification event (has both required headers)
google_push_event_st = st.fixed_dictionaries(
    {
        "headers": st.fixed_dictionaries(
            {
                "x-goog-channel-id": safe_text,
                "x-goog-resource-id": safe_text,
            }
        ),
    }
)

# Strategy: EventBridge job_type events
job_type_event_st = st.fixed_dictionaries(
    {
        "job_type": st.sampled_from(["bootstrap", "renew_channel", "reconcile"]),
    }
)

# Strategy: Notion webhook event (body with data.parent.database_id)
notion_webhook_event_st = st.fixed_dictionaries(
    {
        "body": st.just(
            json.dumps(
                {"data": {"parent": {"database_id": KNOWN_DB_ID}, "id": "page-1"}}
            )
        ),
    }
)

# Strategy: Unrecognised event — no Google headers, no job_type, no
# data.parent.database_id.  We generate a dict that explicitly lacks
# the distinguishing fields.
unrecognised_event_st = st.fixed_dictionaries(
    {},
    optional={
        "some_field": safe_text,
        "body": st.just(json.dumps({"random": "payload"})),
    },
)

# Combined strategy that picks one of the six categories
event_category_st = st.one_of(
    google_push_event_st.map(lambda e: ("google_push", e)),
    job_type_event_st.filter(lambda e: e["job_type"] == "bootstrap").map(
        lambda e: ("bootstrap", e)
    ),
    job_type_event_st.filter(lambda e: e["job_type"] == "renew_channel").map(
        lambda e: ("renew_channel", e)
    ),
    job_type_event_st.filter(lambda e: e["job_type"] == "reconcile").map(
        lambda e: ("reconcile", e)
    ),
    notion_webhook_event_st.map(lambda e: ("notion_webhook", e)),
    unrecognised_event_st.map(lambda e: ("unrecognised", e)),
)


# ---------------------------------------------------------------------------
# Property 1 – Source routing correctness
# ---------------------------------------------------------------------------


@given(category_and_event=event_category_st)
@settings(max_examples=200, deadline=None)
def test_source_routing_correctness(category_and_event):
    """**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 11.1**

    For any incoming Lambda event, the source router SHALL select exactly one
    handler path determined by the event's distinguishing fields:
    - events with X-Goog-Channel-ID and X-Goog-Resource-ID headers route to
      the RSVP push handler
    - events with job_type in {bootstrap, renew_channel, reconcile} route to
      the corresponding job handler
    - events with data.parent.database_id route to the existing Notion handler
    - all other events return HTTP 400

    Note: job_type is read directly from the top-level event object (not from
    _parse_body), since EventBridge sends its payload as the top-level event.
    """
    category, event = category_and_event

    # Build mocks for every handler endpoint
    mock_push = MagicMock(return_value={"statusCode": 200, "body": "push_ok"})
    mock_bootstrap = MagicMock(return_value={"statusCode": 200, "body": "bootstrap_ok"})
    mock_renew = MagicMock(return_value={"statusCode": 200, "body": "renew_ok"})
    mock_reconcile = MagicMock(return_value={"statusCode": 200, "body": "reconcile_ok"})
    mock_notion_handler = MagicMock(
        return_value={"statusCode": 200, "body": "notion_ok"}
    )

    mock_rsvp_handler = MagicMock()
    mock_rsvp_handler.handle_push_notification = mock_push
    mock_rsvp_handler.handle_bootstrap = mock_bootstrap
    mock_rsvp_handler.handle_renew_channel = mock_renew
    mock_rsvp_handler.handle_reconciliation_sync = mock_reconcile

    mock_handlers = {KNOWN_DB_ID: mock_notion_handler}

    with patch.dict("sys.modules", {}):
        pass  # no-op, we patch at attribute level below

    with (
        patch("app.rsvp_handler", mock_rsvp_handler),
        patch("app.HANDLERS", mock_handlers),
    ):
        from app import lambda_handler

        result = lambda_handler(event, None)

    # Collect which handlers were called
    handlers_called = {
        "push": mock_push.called,
        "bootstrap": mock_bootstrap.called,
        "renew": mock_renew.called,
        "reconcile": mock_reconcile.called,
        "notion": mock_notion_handler.called,
    }

    called_names = [name for name, was_called in handlers_called.items() if was_called]

    if category == "google_push":
        assert called_names == ["push"], (
            f"Google push event should route to push handler only, "
            f"but called: {called_names}"
        )
        mock_push.assert_called_once_with(event)
        assert result["statusCode"] == 200

    elif category == "bootstrap":
        assert called_names == ["bootstrap"], (
            f"Bootstrap event should route to bootstrap handler only, "
            f"but called: {called_names}"
        )
        mock_bootstrap.assert_called_once_with()
        assert result["statusCode"] == 200

    elif category == "renew_channel":
        assert called_names == ["renew"], (
            f"Renew event should route to renew handler only, "
            f"but called: {called_names}"
        )
        mock_renew.assert_called_once_with()
        assert result["statusCode"] == 200

    elif category == "reconcile":
        assert called_names == ["reconcile"], (
            f"Reconcile event should route to reconcile handler only, "
            f"but called: {called_names}"
        )
        mock_reconcile.assert_called_once_with()
        assert result["statusCode"] == 200

    elif category == "notion_webhook":
        assert called_names == ["notion"], (
            f"Notion webhook event should route to Notion handler only, "
            f"but called: {called_names}"
        )
        assert result["statusCode"] == 200

    elif category == "unrecognised":
        assert called_names == [], (
            f"Unrecognised event should call no handler, "
            f"but called: {called_names}"
        )
        assert result["statusCode"] == 400

    # Verify exactly one handler was called (or zero for unrecognised)
    expected_count = 0 if category == "unrecognised" else 1
    assert len(called_names) == expected_count, (
        f"Expected {expected_count} handler(s) called for {category}, "
        f"got {len(called_names)}: {called_names}"
    )


# ---------------------------------------------------------------------------
# Property 1 (supplementary) – job_type is read from top-level event, not
# from _parse_body
# ---------------------------------------------------------------------------


@given(
    job_type=st.sampled_from(["bootstrap", "renew_channel", "reconcile"]),
    body_job_type=st.sampled_from(["bootstrap", "renew_channel", "reconcile"]),
)
@settings(max_examples=100, deadline=None)
def test_job_type_read_from_top_level_not_body(job_type, body_job_type):
    """**Validates: Requirements 1.2, 1.3, 1.5, 11.1**

    Verify that job_type is read from the top-level event dict, not from
    the parsed body. When the top-level job_type and the body's job_type
    differ, the router must use the top-level value.
    """
    assume(job_type != body_job_type)

    # Event has job_type at top level AND a different one inside body
    event = {
        "job_type": job_type,
        "body": json.dumps({"job_type": body_job_type}),
    }

    mock_bootstrap = MagicMock(return_value={"statusCode": 200})
    mock_renew = MagicMock(return_value={"statusCode": 200})
    mock_reconcile = MagicMock(return_value={"statusCode": 200})

    mock_rsvp_handler = MagicMock()
    mock_rsvp_handler.handle_bootstrap = mock_bootstrap
    mock_rsvp_handler.handle_renew_channel = mock_renew
    mock_rsvp_handler.handle_reconciliation_sync = mock_reconcile

    expected_handler_map = {
        "bootstrap": ("bootstrap", mock_bootstrap),
        "renew_channel": ("renew", mock_renew),
        "reconcile": ("reconcile", mock_reconcile),
    }

    with (
        patch("app.rsvp_handler", mock_rsvp_handler),
        patch("app.HANDLERS", {}),
    ):
        from app import lambda_handler

        result = lambda_handler(event, None)

    name, expected_mock = expected_handler_map[job_type]
    assert expected_mock.called, (
        f"Top-level job_type={job_type!r} should have triggered {name} handler"
    )
    assert result["statusCode"] == 200
