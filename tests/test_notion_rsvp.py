"""Unit tests for Notion RSVP writer.

Validates: Requirement 5.6 — Notion 429 retry with exponential backoff.
"""

import os
import sys
from unittest.mock import patch, MagicMock

import pytest
import requests

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "CTN_NotionMeeting_CalEvent")
)


def _make_429_response() -> requests.Response:
    """Build a fake requests.Response with status 429."""
    resp = requests.Response()
    resp.status_code = 429
    resp.headers["Retry-After"] = "1"
    resp._content = b'{"message": "rate limited"}'
    return resp


@patch("rsvp_sync.notion_rsvp._sess")
def test_query_by_row_key_retries_on_429(mock_sess):
    """Mock Notion API to return 429, verify 3 retries with backoff.

    The @on_exception(expo, RequestException, max_tries=3, max_time=30)
    decorator on query_by_row_key should retry up to 3 times on any
    RequestException (including HTTPError from raise_for_status on a 429).
    After exhausting retries the exception propagates.
    """
    from rsvp_sync.notion_rsvp import query_by_row_key

    mock_session = MagicMock()
    mock_sess.return_value = mock_session

    # Every call to session.post() returns a 429 response whose
    # raise_for_status() raises HTTPError.
    fake_resp = _make_429_response()
    mock_session.post.return_value = fake_resp

    with pytest.raises(requests.exceptions.HTTPError):
        query_by_row_key("cal::evt::email@example.com")

    # backoff max_tries=3 means the function body executes 3 times total
    assert mock_session.post.call_count == 3, (
        f"Expected 3 calls (initial + 2 retries), got {mock_session.post.call_count}"
    )
