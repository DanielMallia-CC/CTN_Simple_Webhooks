"""Configuration for the CTN CancelGig service."""

import os

# Secrets Manager ARN for the Notion API token (shared across CTN services)
NOTION_TOKEN_SECRET = os.getenv(
    "NOTION_TOKEN_SECRET",
    "arn:aws:secretsmanager:eu-north-1:982081075156:secret:prod/NotionAPIkey/Chutney-3HJ1Ik",
)

REGION_NAME = os.getenv("AWS_REGION", "eu-north-1")

NOTION_API_VERSION = "2022-06-28"
NOTION_API_BASE = "https://api.notion.com/v1"

# Google OAuth storage
DYNAMO_TABLE = os.getenv("DYNAMODB_TABLE_NAME", "GoogleAuthTokens")
SECRET_NAME = os.getenv("SECRET_NAME", "prod/gc-project/calendar-events-notion/")

# Gig page property names
GIGS_STATUS_PROPERTY_NAME = os.getenv("GIGS_STATUS_PROPERTY_NAME", "Status")
GIGS_CANCELLED_STATUS_NAME = os.getenv("GIGS_CANCELLED_STATUS_NAME", "Cancelled")
GIGS_CANCELLATION_SENT_PROP = os.getenv("GIGS_CANCELLATION_SENT_PROP", "Cancellation Sent")

GIGS_PORTAL_RELATION_NAME = os.getenv("GIGS_PORTAL_RELATION_NAME", "Musician Portal")
GIGS_MEETINGS_RELATION_NAME = os.getenv("GIGS_MEETINGS_RELATION_NAME", "Meetings")
GIGS_SITE_VISITS_RELATION_NAME = os.getenv("GIGS_SITE_VISITS_RELATION_NAME", "Site Visits")

GOOGLE_EVENT_ID_PROP = os.getenv("GOOGLE_EVENT_ID_PROP", "Google_Event_ID")
GOOGLE_EVENT_URL_PROP = os.getenv("GOOGLE_EVENT_URL_PROP", "Google_Event_URL")

DEFAULT_CALENDAR_ID = os.getenv("DEFAULT_CALENDAR_ID")
