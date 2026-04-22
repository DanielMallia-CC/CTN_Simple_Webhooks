"""Fetch the data_source_id for a Notion database.

Usage:
    python scripts/get_datasource_id.py <database_id>

Requires AWS credentials configured (to read the Notion token from Secrets Manager).
"""
import json
import sys
import os

import boto3
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "CTN_NotionMeeting_CalEvent"))
from config import NOTION_TOKEN_SECRET, REGION_NAME


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/get_datasource_id.py <database_id>")
        sys.exit(1)

    database_id = sys.argv[1]

    # Get Notion token from Secrets Manager
    sm = boto3.client("secretsmanager", region_name=REGION_NAME)
    secret = json.loads(sm.get_secret_value(SecretId=NOTION_TOKEN_SECRET)["SecretString"])
    token = secret["INTERNAL_NOTION_API_KEY"]

    # Call the Notion API with 2025-09-03 version
    resp = requests.get(
        f"https://api.notion.com/v1/databases/{database_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": "2025-09-03",
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    data_sources = data.get("data_sources", [])
    if not data_sources:
        print("No data sources found for this database.")
        sys.exit(1)

    for ds in data_sources:
        print(f"Data Source ID: {ds['id']}")
        print(f"Name: {ds.get('name', '(unnamed)')}")


if __name__ == "__main__":
    main()
