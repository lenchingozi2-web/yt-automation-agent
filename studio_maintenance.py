"""Conservative retention: never delete a video without a confirmed YouTube copy."""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

PREVIEW_DAYS = max(1, int(os.getenv("STUDIO_PUBLISHED_PREVIEW_DAYS", "7")))
THUMBNAIL_DAYS = max(30, int(os.getenv("STUDIO_PUBLISHED_THUMBNAIL_DAYS", "90")))


def obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            data = json.loads(value)
            return data if isinstance(data, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def metadata(row: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    scenes = row.get("scenes")
    if isinstance(scenes, str):
        try:
            scenes = json.loads(scenes)
        except ValueError:
            scenes = None
    if isinstance(scenes, list) and scenes and isinstance(scenes[0], dict):
        data.update(obj(scenes[0].get("_studio")))
    data.update(obj(scenes))
    data.update(obj(row.get("script")))
    data.update(obj(row.get("metadata")))
    data.update({k: v for k, v in row.items() if v is not None and v != ""})
    return data


def expiration_flags(row: dict[str, Any], now: datetime) -> tuple[bool, bool]:
    data = metadata(row)
    youtube_url = str(data.get("youtube_url") or "")
    youtube_id = str(data.get("youtube_id") or data.get("youtube_video_id") or "")
    if row.get("status") != "completed" or not (re.fullmatch(r"[\w-]{11}", youtube_id) or
            re.fullmatch(r"https://(?:www\.)?youtube\.com/(?:watch\?v=|shorts/)[\w-]{11}", youtube_url)):
        return False, False
    timestamp = data.get("published_at") or data.get("completed_at") or row.get("created_at")
    if not isinstance(timestamp, str):
        return False, False
    try:
        completed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if completed.tzinfo is None:
            completed = completed.replace(tzinfo=timezone.utc)
    except ValueError:
        return False, False
    age = now - completed
    render_bucket = os.getenv("SUPABASE_VIDEO_BUCKET", "renders")
    thumb_bucket = os.getenv("SUPABASE_THUMBNAIL_BUCKET", "thumbnails")
    video_url = str(data.get("video_url") or data.get("storage_url") or "")
    thumb_url = str(data.get("thumbnail_url") or "")
    archive_video = age >= timedelta(days=PREVIEW_DAYS) and ("/" + render_bucket + "/") in video_url
    archive_thumbnail = (age >= timedelta(days=THUMBNAIL_DAYS) and ("/" + thumb_bucket + "/") in thumb_url
                         and data.get("thumbnail_publish_status") not in {"pending", "retry", "failed"})
    return archive_video, archive_thumbnail


def clean_row(client: Any, row: dict[str, Any], now: datetime, dry_run: bool = False) -> dict[str, Any]:
    archive_video, archive_thumbnail = expiration_flags(row, now)
    video_id = str(row["id"])
    outcome = {"id": video_id, "video": archive_video, "thumbnail": archive_thumbnail}
    if not (archive_video or archive_thumbnail) or dry_run:
        return outcome
    from worker import save_fields
    data = metadata(row)
    if archive_video:
        bucket = os.getenv("SUPABASE_VIDEO_BUCKET", "renders")
        if not urlsplit(str(data.get("video_url") or data.get("storage_url"))).path.endswith(f"/{bucket}/{video_id}.mp4"):
            raise ValueError("Preview does not belong to this production.")
        client.storage.from_(bucket).remove([video_id + ".mp4"])
        save_fields(client, row, video_url=None, storage_url=None,
                    preview_expired_at=now.isoformat())
    if archive_thumbnail:
        bucket = os.getenv("SUPABASE_THUMBNAIL_BUCKET", "thumbnails")
        if not urlsplit(str(data.get("thumbnail_url"))).path.endswith(f"/{bucket}/{video_id}.jpg"):
            raise ValueError("Thumbnail does not belong to this production.")
        client.storage.from_(bucket).remove([video_id + ".jpg"])
        save_fields(client, row, thumbnail_url=None,
                    thumbnail_expired_at=now.isoformat())
    return outcome


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    from supabase import create_client
    url = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError("Missing Supabase URL or service-role key for maintenance")
    client = create_client(url, key)
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=PREVIEW_DAYS)).isoformat()
    response = client.table("videos").select("*").eq("status", "completed").lt("created_at", cutoff).order("created_at").limit(1000).execute()
    active = client.table("videos").select("scenes").in_("status", ["pending", "rendering"]).execute().data or []
    pinned = {revision.get("parent_id") for row in active
              if isinstance(revision := metadata(row).get("revision"), dict) and revision.get("original_media")}
    inventory = {}
    for setting, default in (("SUPABASE_VIDEO_BUCKET", "renders"), ("SUPABASE_THUMBNAIL_BUCKET", "thumbnails")):
        storage = client.storage.from_(os.getenv(setting, default))
        objects = []
        for offset in range(0, 10000, 1000):
            batch = storage.list("", {"limit": 1000, "offset": offset})
            objects.extend(item for item in batch if item.get("id"))
            if len(batch) < 1000:
                break
        inventory[default] = {"files": len(objects),
                              "bytes": sum(int((item.get("metadata") or {}).get("size") or 0) for item in objects)}
    print("[retention] storage inventory", json.dumps(inventory), flush=True)
    eligible = 0
    failed = 0
    for row in response.data or []:
        if row.get("id") in pinned:
            continue
        try:
            result = clean_row(client, row, now, args.dry_run)
            if result["video"] or result["thumbnail"]:
                eligible += 1
                print("[retention]", result, "dry-run" if args.dry_run else "archived", flush=True)
        except Exception as error:
            failed += 1
            print("[retention]", row.get("id"), type(error).__name__, flush=True)
    print("[retention] eligible", eligible, "of", len(response.data or []), flush=True)
    if failed:
        raise RuntimeError(f"Retention failed for {failed} production(s).")


if __name__ == "__main__":
    main()
