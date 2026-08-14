"""Notification response schemas."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict

from app.models.notification import NotificationType


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: NotificationType
    title: str
    message: str | None = None
    match_history_id: uuid.UUID | None = None
    batch_id: uuid.UUID | None = None
    is_read: bool
    read_at: dt.datetime | None = None
    created_at: dt.datetime


class UnreadCount(BaseModel):
    count: int


class MarkedRead(BaseModel):
    marked: int
