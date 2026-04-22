# Implementation Plan: RSVP Sync

## Overview

Extend the existing Lambda with Google Calendar RSVP synchronization to a Notion database. Implementation proceeds bottom-up: config → data models → adapters → Notion writer → handler/orchestrator → source router wiring. Property-based tests validate correctness properties from the design; unit and integration tests cover edge cases and flows.

## Tasks

- [x] 1. Configuration and data models
  - [x] 1.1 Add RSVP environment variables to config.py
    - Append `RSVP_CALENDAR_ID`, `NOTION_RSVP_DATASOURCE_ID`, `RSVP_WEBHOOK_SLUG`, `RSVP_SYNC_STATE_KEY` (default `rsvp_sync_state`), `RSVP_CHANNEL_TTL_SECONDS` (default `604800`, cast to int), `RSVP_WEBHOOK_TOKEN`, and `RSVP_FUNCTION_URL` to `CTN_NotionMeeting_CalEvent/config.py`
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7_

  - [x] 1.2 Create data models module
    - Create `CTN_NotionMeeting_CalEvent/rsvp_sync/__init__.py` (empty)
    - Create `CTN_NotionMeeting_CalEvent/rsvp_sync/models.py` with `AttendeeRecord` and `SyncState` dataclasses exactly as specified in the design
    - `AttendeeRecord` must include the `row_key` property (`calendarId::eventId::emailAddress`)
    - _Requirements: 4.1, 5.1, 6.1_

  - [x] 1.3 Write property test for Row Key round-trip
    - **Property 4: Row Key round-trip**
    - Use Hypothesis to generate random `(calendar_id, event_id, attendee_email)` tuples without `::` separator
    - Verify `parse(build(x)) == x` for the `row_key` property
    - Create test file `tests/test_rsvp_models_props.py`
    - **Validates: Requirement 5.1**

  - [x] 1.4 Write unit test for config env vars
    - Verify all 7 new config variables load correctly from environment, including defaults
    - Create test file `tests/test_config.py`
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7_

- [x] 2. Sync State Store adapter
  - [x] 2.1 Implement sync_state_store.py
    - Create `CTN_NotionMeeting_CalEvent/adapters/sync_state_store.py`
    - Implement `get_sync_state()`, `update_sync_token(token)`, `update_channel_state(channel_id, resource_id, expiration, channel_token)`, and `update_full_state(sync_token, channel_id, resource_id, expiration, channel_token)`
    - Use `boto3.client("dynamodb")` with `@on_exception(expo, ClientError, max_tries=3)` consistent with `token_store.py`
    - Use `RSVP_SYNC_STATE_KEY` from config as the fixed partition key
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

  - [x] 2.2 Write integration test for sync state persistence
    - Mock DynamoDB client, verify all fields written and read back correctly with the correct PK
    - Add to `tests/test_sync_state_store.py`
    - _Requirements: 6.1, 6.2_

- [x] 3. Google Calendar adapter extensions
  - [x] 3.1 Add four new functions to google_calendar.py
    - Add `list_events_incremental(service, calendar_id, sync_token)` — paginated events.list with syncToken, returns `(list[dict], str)`
    - Add `list_events_full(service, calendar_id)` — paginated events.list without syncToken, returns `(list[dict], str)`
    - Add `create_watch_channel(service, calendar_id, channel_id, webhook_url, token, ttl)` — events.watch call, returns dict
    - Add `stop_watch_channel(service, channel_id, resource_id)` — channels.stop call
    - All use `@on_exception(expo, HttpError, max_tries=3, max_time=20)` matching existing patterns
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5_

  - [x] 3.2 Write unit tests for pagination and 410 fallback
    - Test `list_events_incremental` with 3-page mock response, verify all events collected and final sync token captured
    - Test that HTTP 410 from events.list is raised (handler will catch it)
    - Add to `tests/test_google_calendar.py`
    - _Requirements: 3.3, 3.5, 9.1, 9.2_

- [x] 4. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Notion RSVP Writer
  - [x] 5.1 Implement notion_rsvp.py
    - Create `CTN_NotionMeeting_CalEvent/rsvp_sync/notion_rsvp.py`
    - Implement `query_by_row_key(row_key)`, `query_by_event_id(event_id)`, `create_rsvp_row(record)`, `update_rsvp_row(page_id, record)`, `trash_rsvp_row(page_id)`, and `upsert_or_trash(record)`
    - Use existing `adapters/notion_client.py` session and retry patterns
    - Use `NOTION_RSVP_DATASOURCE_ID` from config for the database ID
    - Trash uses `in_trash: true` (Notion API 2025-09-03)
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6_

  - [x] 5.2 Write property test for upsert decision correctness
    - **Property 5: Upsert decision correctness**
    - Use Hypothesis to generate random `AttendeeRecord` instances and random existing Notion states (no row, same status, different status, remove=True with/without row)
    - Mock Notion API, verify exactly one of: create, update, trash, or no-op
    - Add to `tests/test_notion_rsvp_props.py`
    - **Validates: Requirements 5.2, 5.3, 5.4**

  - [x] 5.3 Write property test for idempotent writes
    - **Property 6: Idempotent writes**
    - Use Hypothesis to generate random `AttendeeRecord` instances
    - Mock Notion API with a stateful fake, process each record twice, verify state is identical after second pass
    - Add to `tests/test_notion_rsvp_props.py`
    - **Validates: Requirements 5.5, 8.3**

  - [x] 5.4 Write unit test for Notion 429 retry
    - Mock Notion API to return 429, verify 3 retries with backoff
    - Add to `tests/test_notion_rsvp.py`
    - _Requirements: 5.6_

- [x] 6. RSVP Sync Handler
  - [x] 6.1 Implement rsvp_sync/handler.py — validation and extraction helpers
    - Create `CTN_NotionMeeting_CalEvent/rsvp_sync/handler.py`
    - Implement `_validate_push(event)` — checks secret slug in URL path against `RSVP_WEBHOOK_SLUG` from config and validates `X-Goog-Channel-Token` against `RSVP_WEBHOOK_TOKEN` from config (no DynamoDB call)
    - Implement `_process_events(events)` — extracts `AttendeeRecord` list from changed events; sets `remove=True` for cancelled events; skips events with no attendees array
    - Implement `_trash_removed_attendees(events)` — for each non-cancelled changed event, queries Notion by Event ID, trashes rows whose email is not in current attendee list
    - Implement `_build_calendar_service()` — loads credentials from DynamoDB via `token_store`, builds Google Calendar service
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 4.1, 4.2, 4.3, 4.4_

  - [x] 6.2 Write property test for push notification validation
    - **Property 2: Push notification validation**
    - Use Hypothesis to generate random `(slug, token, configured_slug, configured_token)` tuples
    - Verify validation returns success iff both match; rejects with 403 otherwise
    - Add to `tests/test_handler_props.py`
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.4**

  - [x] 6.3 Write property test for attendee extraction correctness
    - **Property 3: Attendee extraction correctness**
    - Use Hypothesis to generate random lists of Google Calendar event dicts with varying attendee arrays, statuses, cancelled events, and missing fields
    - Verify one `AttendeeRecord` per attendee in non-cancelled events, `remove=True` for cancelled, zero records for no-attendees events
    - Add to `tests/test_handler_props.py`
    - **Validates: Requirements 4.1, 4.2, 4.4**

  - [x] 6.4 Write property test for removed attendee detection
    - **Property 3b: Removed attendee detection**
    - Use Hypothesis to generate random sets of current Google attendees and existing Notion rows for an event
    - Verify exactly the correct rows are trashed (those whose email is not in current attendees)
    - Add to `tests/test_handler_props.py`
    - **Validates: Requirement 4.3**

  - [x] 6.5 Write unit test for sync ping handling
    - Send push notification with `X-Goog-Resource-State: sync`, verify HTTP 200 returned with no sync triggered
    - Add to `tests/test_handler.py`
    - _Requirements: 2.5_

  - [x] 6.6 Implement handler orchestration functions
    - Implement `handle_push_notification(event)` — validate → incremental sync → process events → upsert/trash → trash removed attendees
    - Implement `handle_reconciliation_sync()` — incremental sync → process events → upsert/trash → trash removed attendees
    - Implement `handle_renew_channel()` — stop old channel → create new channel with random token → persist state
    - Implement `handle_bootstrap()` — full sync → create watch channel → persist state; stop existing channel if present; return summary
    - Implement `_run_incremental_sync()` — calls `list_events_incremental`, falls back to `list_events_full` on 410
    - Implement `_run_full_sync()` — calls `list_events_full`
    - _Requirements: 2.5, 3.1, 3.2, 3.3, 3.4, 3.5, 7.1, 7.2, 7.3, 7.4, 7.5, 8.1, 8.2, 11.2, 11.3, 11.4, 11.5_

  - [x] 6.7 Write unit tests for handler orchestration
    - Test 410 triggers full sync fallback
    - Test stop_channel failure (404) continues with new channel creation
    - Test bootstrap with existing channel stops old channel first
    - Test bootstrap response contains event count and channel ID
    - Add to `tests/test_handler.py`
    - _Requirements: 3.3, 7.4, 11.4, 11.5_

- [x] 7. Checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Source router wiring
  - [x] 8.1 Modify app.py to add source routing
    - Add detection logic at the top of `lambda_handler` before existing Notion webhook routing
    - Check for Google push notification headers (`x-goog-channel-id` and `x-goog-resource-id` in `event.get("headers")`)
    - Check for `job_type` field directly on the top-level event (not `_parse_body`): `bootstrap`, `renew_channel`, `reconcile`
    - Fall through to existing `data.parent.database_id` routing
    - Return HTTP 400 for unrecognized events
    - Import `rsvp_sync.handler` at the top of `app.py`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 11.1_

  - [x] 8.2 Write property test for source routing correctness
    - **Property 1: Source routing correctness**
    - Use Hypothesis to generate random event dicts with varying header/field combinations
    - Mock all handler functions, verify exactly one handler is called per event
    - Verify `job_type` is read from top-level event, not from `_parse_body`
    - Add to `tests/test_app_props.py`
    - **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 11.1**

  - [x] 8.3 Write integration tests for reconciliation and channel renewal flows
    - Test reconciliation job triggers incremental sync and processes events identically to push
    - Test channel renewal flow: stop → watch → persist sequence
    - Test bootstrap full flow: full sync + channel creation + state persistence
    - Test config env vars: set all 7 env vars, verify config module values
    - Add to `tests/test_integration.py`
    - _Requirements: 8.1, 8.2, 7.1, 7.2, 7.3, 11.2, 11.3, 10.1-10.7_

- [x] 9. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate the 7 correctness properties from the design document
- All external API calls (Google Calendar, Notion, DynamoDB) are mocked in tests
- Python is the implementation language throughout, consistent with the existing codebase
