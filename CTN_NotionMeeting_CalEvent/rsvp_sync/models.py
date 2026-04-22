from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class AttendeeRecord:
    calendar_id: str
    event_id: str
    event_name: str          # event summary
    attendee_email: str
    display_name: str        # attendee displayName or email fallback
    rsvp_status: str         # accepted | declined | tentative | needsAction
    is_organizer: bool
    remove: bool = False     # True when attendee/event should be trashed

    @property
    def row_key(self) -> str:
        return f"{self.calendar_id}::{self.event_id}::{self.attendee_email}"


@dataclass
class SyncState:
    sync_token: Optional[str]
    channel_id: Optional[str]
    resource_id: Optional[str]
    channel_expiration: Optional[int]  # epoch millis
    channel_token: Optional[str]
