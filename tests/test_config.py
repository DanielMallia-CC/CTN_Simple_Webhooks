"""Unit tests for RSVP sync configuration environment variables.

Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7
"""

import importlib
import os
import sys

# Add the source directory to the path so we can import the config module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "CTN_NotionMeeting_CalEvent"))


def _reload_config(monkeypatch, env_overrides=None):
    """Set env vars via monkeypatch, then reload the config module and return it."""
    if env_overrides:
        for key, value in env_overrides.items():
            monkeypatch.setenv(key, value)
    import config
    importlib.reload(config)
    return config


def test_all_rsvp_env_vars_loaded(monkeypatch):
    """All 7 RSVP config variables load correctly from environment."""
    env = {
        "RSVP_CALENDAR_ID": "my-calendar@group.calendar.google.com",
        "NOTION_RSVP_DATASOURCE_ID": "abc123-notion-db-id",
        "RSVP_WEBHOOK_SLUG": "secret-slug-value",
        "RSVP_SYNC_STATE_KEY": "custom_state_key",
        "RSVP_CHANNEL_TTL_SECONDS": "86400",
        "RSVP_WEBHOOK_TOKEN": "webhook-token-xyz",
        "RSVP_FUNCTION_URL": "https://example.lambda-url.eu-north-1.on.aws/secret-slug-value",
    }
    config = _reload_config(monkeypatch, env)

    assert config.RSVP_CALENDAR_ID == "my-calendar@group.calendar.google.com"
    assert config.NOTION_RSVP_DATASOURCE_ID == "abc123-notion-db-id"
    assert config.RSVP_WEBHOOK_SLUG == "secret-slug-value"
    assert config.RSVP_SYNC_STATE_KEY == "custom_state_key"
    assert config.RSVP_CHANNEL_TTL_SECONDS == 86400
    assert config.RSVP_WEBHOOK_TOKEN == "webhook-token-xyz"
    assert config.RSVP_FUNCTION_URL == "https://example.lambda-url.eu-north-1.on.aws/secret-slug-value"


def test_default_rsvp_sync_state_key(monkeypatch):
    """RSVP_SYNC_STATE_KEY defaults to 'rsvp_sync_state' when env var is not set."""
    monkeypatch.delenv("RSVP_SYNC_STATE_KEY", raising=False)
    config = _reload_config(monkeypatch)

    assert config.RSVP_SYNC_STATE_KEY == "rsvp_sync_state"


def test_default_rsvp_channel_ttl_seconds(monkeypatch):
    """RSVP_CHANNEL_TTL_SECONDS defaults to 604800 when env var is not set."""
    monkeypatch.delenv("RSVP_CHANNEL_TTL_SECONDS", raising=False)
    config = _reload_config(monkeypatch)

    assert config.RSVP_CHANNEL_TTL_SECONDS == 604800


def test_rsvp_channel_ttl_seconds_is_int(monkeypatch):
    """RSVP_CHANNEL_TTL_SECONDS is cast to int from the env var string."""
    monkeypatch.setenv("RSVP_CHANNEL_TTL_SECONDS", "3600")
    config = _reload_config(monkeypatch)

    assert config.RSVP_CHANNEL_TTL_SECONDS == 3600
    assert isinstance(config.RSVP_CHANNEL_TTL_SECONDS, int)


def test_none_when_env_vars_not_set(monkeypatch):
    """Variables without defaults return None when env vars are absent."""
    for var in [
        "RSVP_CALENDAR_ID",
        "NOTION_RSVP_DATASOURCE_ID",
        "RSVP_WEBHOOK_SLUG",
        "RSVP_WEBHOOK_TOKEN",
        "RSVP_FUNCTION_URL",
    ]:
        monkeypatch.delenv(var, raising=False)
    config = _reload_config(monkeypatch)

    assert config.RSVP_CALENDAR_ID is None
    assert config.NOTION_RSVP_DATASOURCE_ID is None
    assert config.RSVP_WEBHOOK_SLUG is None
    assert config.RSVP_WEBHOOK_TOKEN is None
    assert config.RSVP_FUNCTION_URL is None
