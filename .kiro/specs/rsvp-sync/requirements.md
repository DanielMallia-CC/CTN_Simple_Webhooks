# Requirements Document

## Introduction

Real-time synchronization of Google Calendar RSVP statuses to a Notion database. When a guest accepts, declines, or marks themselves as tentative on a Google Calendar event, the system automatically updates a corresponding row in a dedicated Notion RSVP database — with no manual intervention. The system uses Google Calendar push notifications for near-real-time updates, with scheduled reconciliation to guarantee completeness.

## Glossary

- **Lambda**: The existing AWS Lambda function at CTN_NotionMeeting_CalEvent that currently handles Notion webhook → Google Calendar event creation, extended with RSVP sync capabilities
- **Source_Router**: The routing logic in app.py that determines whether an incoming invocation is a Google push notification, an EventBridge scheduler job, or an existing Notion webhook
- **Google_Push_Notification**: An HTTP POST sent by Google Calendar to the Lambda Function URL when any event on a watched calendar changes; contains only headers (X-Goog-* fields), no event data
- **Watch_Channel**: A registration with Google Calendar's events.watch API that tells Google to send push notifications to a specified URL when calendar events change; expires after a configured TTL
- **Sync_Token**: An opaque string returned by Google Calendar's events.list API that enables incremental sync — fetching only events that changed since the last call
- **Sync_State_Store**: A single DynamoDB item in the existing GoogleAuthTokens table that persists the Sync_Token, Watch_Channel ID, Watch_Channel resource ID, Watch_Channel expiration, and the secret validation token
- **RSVP_Database**: The Notion database dedicated to RSVP tracking, where each row represents one attendee on one event
- **Row_Key**: A composite string in the format `calendarId::eventId::emailAddress` used to uniquely identify an attendee row in the RSVP_Database
- **RSVP_Status**: One of four Google Calendar responseStatus values: `accepted`, `declined`, `tentative`, or `needsAction`
- **Incremental_Sync**: The process of calling Google Calendar events.list with a stored Sync_Token to retrieve only changed events since the last sync
- **Full_Sync**: The process of calling Google Calendar events.list without a Sync_Token to retrieve all events and obtain an initial Sync_Token
- **Reconciliation_Job**: An EventBridge Scheduler rule that fires every 30 minutes and invokes the Lambda to run an Incremental_Sync regardless of whether a push notification was received
- **Renewal_Job**: An EventBridge Scheduler rule that fires every 12 hours and invokes the Lambda to renew the Watch_Channel before it expires
- **Secret_Slug**: A secret random string embedded in the Lambda Function URL path that makes the endpoint unguessable; stored in the `RSVP_WEBHOOK_SLUG` environment variable
- **Channel_Token**: A secret string stored in the Sync_State_Store and included by Google in the X-Goog-Channel-Token header on every push notification, used for request validation
- **RSVP_Sync_Handler**: The new orchestrator module (rsvp_sync/handler.py) that validates webhooks, runs syncs, extracts attendees, and writes to Notion
- **Notion_RSVP_Writer**: The module (rsvp_sync/notion_rsvp.py) that handles all Notion RSVP_Database writes: query by Row_Key, create, update, and trash

## Requirements

### Requirement 1: Source Routing

**User Story:** As a developer, I want the Lambda to route incoming invocations to the correct handler based on the source type, so that RSVP sync, scheduled jobs, and existing Notion webhooks all coexist in a single Lambda.

#### Acceptance Criteria

1. WHEN the incoming event contains X-Goog-Channel-ID and X-Goog-Resource-ID headers, THE Source_Router SHALL route the invocation to the RSVP_Sync_Handler as a Google_Push_Notification
2. WHEN the incoming event contains a `job_type` field with value `renew_channel`, THE Source_Router SHALL route the invocation to the Watch_Channel renewal function
3. WHEN the incoming event contains a `job_type` field with value `reconcile`, THE Source_Router SHALL route the invocation to the Incremental_Sync function
4. WHEN the incoming event contains a `data.parent.database_id` field, THE Source_Router SHALL route the invocation to the existing Notion webhook handler unchanged
5. WHEN the incoming event contains a `job_type` field with value `bootstrap`, THE Source_Router SHALL route the invocation to the bootstrap function
6. IF the incoming event matches none of the recognized source types, THEN THE Source_Router SHALL return HTTP 400 with a descriptive error message and log the unrecognized event type

### Requirement 2: Google Push Notification Validation

**User Story:** As a developer, I want the Lambda to validate incoming Google push notifications using two independent secrets, so that unauthorized requests are rejected.

#### Acceptance Criteria

1. WHEN a Google_Push_Notification is received, THE RSVP_Sync_Handler SHALL verify that the request URL path contains the configured Secret_Slug
2. WHEN a Google_Push_Notification is received, THE RSVP_Sync_Handler SHALL verify that the X-Goog-Channel-Token header matches the Channel_Token stored in the Sync_State_Store
3. IF the Secret_Slug in the URL path does not match the configured value, THEN THE RSVP_Sync_Handler SHALL return HTTP 403 and log the rejection reason
4. IF the X-Goog-Channel-Token header does not match the stored Channel_Token, THEN THE RSVP_Sync_Handler SHALL return HTTP 403 and log the rejection reason
5. WHEN a Google_Push_Notification has X-Goog-Resource-State header value of `sync`, THE RSVP_Sync_Handler SHALL return HTTP 200 without performing any sync (this is Google's initial verification ping)

### Requirement 3: Incremental Sync with Google Calendar

**User Story:** As a developer, I want the Lambda to fetch only changed events from Google Calendar using incremental sync, so that each sync is efficient and avoids re-processing the entire calendar.

#### Acceptance Criteria

1. WHEN an Incremental_Sync is triggered, THE Lambda SHALL call Google Calendar events.list with the Sync_Token retrieved from the Sync_State_Store
2. WHEN Google Calendar returns a new Sync_Token in the response, THE Lambda SHALL persist the new Sync_Token to the Sync_State_Store before processing completes
3. IF the stored Sync_Token is invalid or expired (Google returns HTTP 410 Gone), THEN THE Lambda SHALL perform a Full_Sync to obtain a new Sync_Token
4. WHEN a Full_Sync is performed, THE Lambda SHALL call Google Calendar events.list without a Sync_Token and persist the returned Sync_Token to the Sync_State_Store
5. WHEN Google Calendar returns paginated results, THE Lambda SHALL follow all pageToken pages until the final page returns a new Sync_Token

### Requirement 4: RSVP Status Extraction

**User Story:** As a developer, I want the Lambda to extract attendee RSVP statuses from changed Google Calendar events, so that each attendee's response is available for writing to Notion.

#### Acceptance Criteria

1. WHEN a changed event is returned by the Incremental_Sync, THE Lambda SHALL read the `attendees` array and extract each attendee's `email`, `displayName`, `responseStatus`, and `organizer` flag
2. WHEN a changed event has a `status` of `cancelled`, THE Lambda SHALL mark all attendees of that event for row removal in the RSVP_Database
3. WHEN an attendee is no longer present in the `attendees` array of a previously synced event, THE Lambda SHALL mark that attendee for row removal in the RSVP_Database
4. WHEN an event has no `attendees` array, THE Lambda SHALL skip that event without error

### Requirement 5: Notion RSVP Database Writes

**User Story:** As a developer, I want the Lambda to create, update, or trash rows in the Notion RSVP database based on extracted RSVP data, so that Notion always reflects the current state of Google Calendar RSVPs.

#### Acceptance Criteria

1. WHEN writing an attendee record, THE Notion_RSVP_Writer SHALL query the RSVP_Database for an existing row using the Row_Key (`calendarId::eventId::emailAddress`)
2. WHEN a matching row exists and the RSVP_Status has changed, THE Notion_RSVP_Writer SHALL update the row with the new RSVP_Status
3. WHEN no matching row exists, THE Notion_RSVP_Writer SHALL create a new row with the following properties: Name (attendee display name), Row_Key, Event ID, Event Name, Attendee Email, RSVP_Status, and Is Organizer
4. WHEN an attendee is marked for removal, THE Notion_RSVP_Writer SHALL trash the corresponding row in the RSVP_Database by setting `in_trash: true` on the Notion page
5. THE Notion_RSVP_Writer SHALL produce identical results when the same sync data is processed multiple times (idempotent operation)
6. IF the Notion API returns a rate-limit error (HTTP 429), THEN THE Notion_RSVP_Writer SHALL retry the request with exponential backoff up to 3 attempts

### Requirement 6: Sync State Persistence

**User Story:** As a developer, I want sync state (token and channel metadata) stored in a single DynamoDB item, so that the Lambda can resume incremental sync across invocations without a new table.

#### Acceptance Criteria

1. THE Sync_State_Store SHALL store the following fields in a single DynamoDB item in the existing GoogleAuthTokens table: Sync_Token, Watch_Channel ID, Watch_Channel resource ID, Watch_Channel expiration timestamp, and Channel_Token
2. THE Sync_State_Store SHALL use a fixed, well-known `client_id` partition key value to identify the RSVP sync state item
3. WHEN the Sync_Token is updated, THE Sync_State_Store SHALL write the new value atomically so that concurrent Lambda invocations do not corrupt the stored state
4. WHEN Watch_Channel metadata is updated after a renewal, THE Sync_State_Store SHALL persist the new channel ID, resource ID, expiration, and Channel_Token

### Requirement 7: Watch Channel Management

**User Story:** As a developer, I want the Lambda to create and renew Google Calendar watch channels, so that push notifications are continuously received without manual intervention.

#### Acceptance Criteria

1. WHEN the Renewal_Job invokes the Lambda, THE Lambda SHALL call Google Calendar events.watch to create a new Watch_Channel with a TTL of 7 days
2. WHEN a new Watch_Channel is created, THE Lambda SHALL stop the previous Watch_Channel by calling channels.stop with the old channel ID and resource ID
3. WHEN a new Watch_Channel is created, THE Lambda SHALL persist the new channel ID, resource ID, expiration timestamp, and Channel_Token to the Sync_State_Store
4. IF stopping the previous Watch_Channel fails (e.g., HTTP 404 because it already expired), THEN THE Lambda SHALL log the failure and continue with the new channel creation
5. THE Lambda SHALL generate a cryptographically random Channel_Token for each new Watch_Channel using a secure random generator

### Requirement 8: Reconciliation Sync

**User Story:** As a developer, I want a scheduled reconciliation sync to run every 30 minutes, so that any push notifications dropped by Google are caught and Notion stays accurate.

#### Acceptance Criteria

1. WHEN the Reconciliation_Job invokes the Lambda, THE Lambda SHALL perform an Incremental_Sync using the stored Sync_Token
2. WHEN the Reconciliation_Job sync completes, THE Lambda SHALL process all changed events and update the RSVP_Database identically to a push-notification-triggered sync
3. THE Reconciliation_Job sync SHALL produce no duplicate or conflicting rows in the RSVP_Database when run concurrently with a push-notification-triggered sync (idempotent by Row_Key)

### Requirement 9: Google Calendar Adapter Extensions

**User Story:** As a developer, I want new functions added to the existing google_calendar adapter, so that the RSVP sync can perform incremental sync, full sync, watch channel creation, and watch channel teardown.

#### Acceptance Criteria

1. THE google_calendar adapter SHALL expose a `list_events_incremental` function that calls events.list with a syncToken parameter and returns changed events plus the new Sync_Token
2. THE google_calendar adapter SHALL expose a `list_events_full` function that calls events.list without a syncToken and returns all events plus the initial Sync_Token
3. THE google_calendar adapter SHALL expose a `create_watch_channel` function that calls events.watch with the Lambda Function URL, channel ID, TTL, and Channel_Token
4. THE google_calendar adapter SHALL expose a `stop_watch_channel` function that calls channels.stop with the channel ID and resource ID
5. WHEN any Google Calendar API call returns an HttpError, THE google_calendar adapter SHALL retry with exponential backoff up to 3 attempts, consistent with the existing retry pattern

### Requirement 10: Configuration Extensions

**User Story:** As a developer, I want new environment variables added to config.py for RSVP sync settings, so that all configurable values are centralized and environment-specific.

#### Acceptance Criteria

1. THE config module SHALL expose `RSVP_CALENDAR_ID` loaded from the `RSVP_CALENDAR_ID` environment variable
2. THE config module SHALL expose `NOTION_RSVP_DATASOURCE_ID` loaded from the `NOTION_RSVP_DATASOURCE_ID` environment variable
3. THE config module SHALL expose `RSVP_WEBHOOK_SLUG` loaded from the `RSVP_WEBHOOK_SLUG` environment variable
4. THE config module SHALL expose `RSVP_SYNC_STATE_KEY` loaded from the `RSVP_SYNC_STATE_KEY` environment variable, with a default value of `rsvp_sync_state`
5. THE config module SHALL expose `RSVP_CHANNEL_TTL_SECONDS` loaded from the `RSVP_CHANNEL_TTL_SECONDS` environment variable, with a default value of `604800` (7 days)
6. THE config module SHALL expose `RSVP_WEBHOOK_TOKEN` loaded from the `RSVP_WEBHOOK_TOKEN` environment variable
7. THE config module SHALL expose `RSVP_FUNCTION_URL` loaded from the `RSVP_FUNCTION_URL` environment variable (the full Lambda Function URL including the secret slug path segment, used as the webhook address when creating watch channels)

### Requirement 11: Bootstrap Job

**User Story:** As a developer, I want a bootstrap job type that performs a full sync and creates the initial watch channel, so that the system can be initialised from scratch and recovered if sync state is lost.

#### Acceptance Criteria

1. WHEN the incoming event contains a `job_type` field with value `bootstrap`, THE Source_Router SHALL route the invocation to the bootstrap function
2. WHEN the bootstrap function is invoked, THE Lambda SHALL perform a Full_Sync to obtain an initial Sync_Token and persist it to the Sync_State_Store
3. WHEN the bootstrap function is invoked, THE Lambda SHALL create a new Watch_Channel and persist its metadata to the Sync_State_Store
4. IF a Watch_Channel already exists in the Sync_State_Store, THEN THE bootstrap function SHALL stop the existing channel before creating a new one
5. WHEN the bootstrap function completes successfully, THE Lambda SHALL return HTTP 200 with a summary of the initial sync (number of events fetched, channel ID created)
