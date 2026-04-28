"""
Configuration for the CTN Feedback service.

Mirrors the pattern used in CTN_NotionMeeting_CalEvent — all values
come from environment variables so the same Lambda can be configured
per-stage without code changes.
"""

import os

# Secrets Manager ARN for the Notion API token (shared with meeting service)
NOTION_TOKEN_SECRET = os.getenv(
    "NOTION_TOKEN_SECRET",
    "arn:aws:secretsmanager:eu-north-1:982081075156:secret:prod/NotionAPIkey/Chutney-3HJ1Ik",
)

REGION_NAME = os.getenv("AWS_REGION", "eu-north-1")

# Notion database where action items are created
NOTION_ACTIONS_DATABASE_ID = os.getenv("FEEDBACK_ACTIONS_DB_ID", "")

# Sprints database for linking actions to the current sprint
NOTION_SPRINTS_DATABASE_ID = os.getenv("FEEDBACK_SPRINTS_DB_ID", "")

# Relation / assignee defaults
NOTION_PROJECT_PAGE_ID = os.getenv("FEEDBACK_PROJECT_PAGE_ID", "")
NOTION_ASSIGNEE_PAGE_ID = os.getenv("FEEDBACK_ASSIGNEE_PAGE_ID", "")

# Template IDs (optional — omit to create pages without a template)
NOTION_BUG_TEMPLATE_ID = os.getenv("FEEDBACK_BUG_TEMPLATE_ID", "")
NOTION_ADMIN_TEMPLATE_ID = os.getenv("FEEDBACK_ADMIN_TEMPLATE_ID", "")

# Notion API
NOTION_API_VERSION = "2022-06-28"
NOTION_API_BASE = "https://api.notion.com/v1"

# Feedback database ID (the source DB that triggers the webhook)
FEEDBACK_DB_ID = os.getenv("FEEDBACK_DB_ID", "")
