"""Shared eligibility rules for the Actions queue probe and YouTube worker."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from studio_maintenance import metadata


def credential_version() -> str:
    """Identify a changed credential without storing OAuth values in the queue."""
    raw = os.getenv("YOUTUBE_TOKEN_JSON", "").strip()
    if not raw:
        return ""
    try:
        values = json.loads(raw)
        identity = [values[key] for key in ("client_id", "client_secret", "refresh_token")]
        if not all(isinstance(value, str) and value.strip() for value in identity):
            return ""
    except (KeyError, TypeError, ValueError):
        return ""
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24]


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def has_approval(data: dict[str, Any]) -> bool:
    return bool(parse_timestamp(data.get("publish_approved_at")) and data.get("publish_approved_by"))


def thumbnail_due(row: dict[str, Any], version: str, now: datetime) -> bool:
    if row.get("status") != "completed" or not version:
        return False
    data = metadata(row)
    if (data.get("publish_status") != "uploaded" or not has_approval(data)
            or not data.get("thumbnail_url") or not (data.get("youtube_id") or data.get("youtube_video_id"))
            or data.get("thumbnail_publish_status") not in ("pending", "retry")):
        return False
    due = parse_timestamp(data.get("thumbnail_retry_at"))
    return due is None or due <= now


def publication_due(row: dict[str, Any], version: str, now: datetime) -> bool:
    if row.get("status") != "completed" or not version:
        return False
    data = metadata(row)
    if (data.get("youtube_url") or data.get("youtube_id") or data.get("youtube_video_id")
            or not (data.get("video_url") or data.get("storage_url"))):
        return False
    if not has_approval(data):
        return False
    state = data.get("publish_status")
    if state == "uploading":
        heartbeat = parse_timestamp(data.get("publish_heartbeat_at")) or parse_timestamp(data.get("worker_heartbeat_at"))
        return heartbeat is None or now - heartbeat >= timedelta(hours=2)
    if data.get("publish_requested"):
        return True
    if state == "upload_uncertain":
        return False
    if state == "auth_required":
        if data.get("publish_auth_version") == version:
            return False
        return True
    if state == "failed" or int(data.get("publish_attempts") or 0) >= 5:
        return False
    due = parse_timestamp(data.get("publish_retry_at"))
    return due is None or due <= now
