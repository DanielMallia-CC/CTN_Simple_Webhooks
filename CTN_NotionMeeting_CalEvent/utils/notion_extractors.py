from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


def extract_page_title(properties: Dict[str, Any], fallback: str = "Untitled Event") -> str:
    """Return the Notion page title as a single string.

    The title property name varies by database. This finds the first property
    with type == 'title' and concatenates all 'plain_text' fragments.
    """
    for _, prop in (properties or {}).items():
        if isinstance(prop, dict) and prop.get("type") == "title":
            nodes: List[Dict[str, Any]] = prop.get("title", []) or []
            title = "".join(n.get("plain_text", "") for n in nodes).strip()
            return title or fallback
    return fallback


def clean_event_title(raw_title: str, *, prefixes: List[str]) -> str:
    """Strip a known prefix and normalize for Calendar summaries.

    Examples:
      'Portal_Testing_Gig' -> 'Testing Gig'
      'Meeting Testing Gig' -> 'Testing Gig'
      'Site_Visit_Testing_Gig' -> 'Testing Gig'
    """
    if not raw_title:
        return ""

    title = raw_title.strip()

    # Strip the first matching prefix.
    for p in prefixes:
        if not p:
            continue
        if title.startswith(p):
            title = title[len(p) :]
            break

    # Also support prefixes where '_' and spaces are used interchangeably.
    # e.g. 'Site Visit_' vs 'Site_Visit_'
    for p in prefixes:
        if not p:
            continue
        p_fuzzy = re.sub(r"[ _]+", r"[ _]+", re.escape(p))
        title = re.sub(rf"^{p_fuzzy}", "", title)

    # Normalize underscores to spaces, collapse whitespace.
    title = title.replace("_", " ")
    title = re.sub(r"\s+", " ", title).strip()

    return title


def extract_emails(properties: Dict[str, Any]) -> List[str]:
    arr = properties.get("Email", {}).get("rollup", {}).get("array", [])
    emails: List[str] = []
    for e in arr:
        if isinstance(e, dict) and e.get("email"):
            emails.append(e["email"])
    return emails


def extract_location(properties: Dict[str, Any]) -> str:
    maps = properties.get("Maps Link", {}).get("rollup", {}).get("array", [])
    if maps and isinstance(maps[0], dict):
        return maps[0].get("url") or ""
    return ""


def extract_related_page_ids(properties: Dict[str, Any], relation_name: str) -> List[str]:
    rel = properties.get(relation_name, {})
    if isinstance(rel, dict) and rel.get("type") == "relation":
        return [i["id"] for i in rel.get("relation", []) if isinstance(i, dict) and i.get("id")]
    if isinstance(rel, dict) and isinstance(rel.get("relation"), list):
        return [i["id"] for i in rel["relation"] if isinstance(i, dict) and i.get("id")]
    return []


def extract_date_range(properties: Dict[str, Any]) -> Dict[str, Optional[str]]:
    date_prop = properties.get("Date", {}).get("date", {}) or {}
    return {"start": date_prop.get("start"), "end": date_prop.get("end")}


def extract_google_event_id(properties: Dict[str, Any], prop_name: str) -> Optional[str]:
    rt = properties.get(prop_name, {}).get("rich_text") or properties.get(prop_name, {}).get("title") or properties.get(prop_name, {}).get("text")
    # But for Notion "rich_text" property in page payload:
    # {"type":"rich_text","rich_text":[{"plain_text":"..."}]}
    if isinstance(properties.get(prop_name), dict):
        p = properties[prop_name]
        if p.get("type") == "rich_text":
            nodes = p.get("rich_text", [])
            for n in nodes:
                if n.get("plain_text"):
                    return n["plain_text"]
        if p.get("type") == "title":
            nodes = p.get("title", [])
            for n in nodes:
                if n.get("plain_text"):
                    return n["plain_text"]
    return None