import os

DYNAMO_TABLE = os.getenv("DYNAMODB_TABLE_NAME", "GoogleAuthTokens")
SECRET_NAME = os.getenv("SECRET_NAME", "prod/gc-project/calendar-events-notion/")
NOTION_TOKEN_SECRET = os.getenv(
    "NOTION_TOKEN_SECRET",
    "arn:aws:secretsmanager:eu-north-1:982081075156:secret:prod/NotionAPIkey/Chutney-3HJ1Ik",
)
REGION_NAME = os.getenv("AWS_REGION", "eu-north-1")

MUSICIAN_PORTAL_DB_ID = os.getenv("MUSICIAN_PORTAL_DB_ID")
MEETINGS_DB_ID = os.getenv("MEETINGS_DB_ID")
SITE_VISITS_DB_ID = os.getenv("SITE_VISITS_DB_ID")

NOTION_API_VERSION = "2025-09-03"

GIG_RELATION_PROP =  "Gig (Management)"
GIGS_GOOGLE_EVENT_PROP = "Google Event"

GOOGLE_EVENT_ID_PROP = "Google_Event_ID"
GOOGLE_EVENT_URL_PROP = "Google_Event_URL"

NOTION_USER_ENDPOINT = "https://api.notion.com/v1/users"
NOTION_PAGES_ENDPOINT = "https://api.notion.com/v1/pages"

# RSVP Sync configuration
RSVP_CALENDAR_ID = os.getenv("RSVP_CALENDAR_ID")
NOTION_RSVP_DATASOURCE_ID = os.getenv("NOTION_RSVP_DATASOURCE_ID")
RSVP_WEBHOOK_SLUG = os.getenv("RSVP_WEBHOOK_SLUG")
RSVP_SYNC_STATE_KEY = os.getenv("RSVP_SYNC_STATE_KEY", "rsvp_sync_state")
RSVP_CHANNEL_TTL_SECONDS = int(os.getenv("RSVP_CHANNEL_TTL_SECONDS", "604800"))
RSVP_WEBHOOK_TOKEN = os.getenv("RSVP_WEBHOOK_TOKEN")
RSVP_FUNCTION_URL = os.getenv("RSVP_FUNCTION_URL")
