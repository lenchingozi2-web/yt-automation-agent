"""Expire uploaded previews and support an explicit cleanup of failed Studio productions."""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

PREVIEW_HOURS = max(1, int(os.getenv("STUDIO_PUBLISHED_PREVIEW_HOURS", "12")))
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


def preview_filename(row: dict[str, Any]) -> str:
    suffix = "-pace-v1" if metadata(row).get("narration_pace_version") == 1 else ""
    return f"{row['id']}{suffix}.mp4"


def confirmed_upload(data: dict[str, Any]) -> bool:
    youtube_id = str(data.get("youtube_id") or data.get("youtube_video_id") or "")
    youtube_url = str(data.get("youtube_url") or "")
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{11}", youtube_id) or
                re.fullmatch(r"https://(?:www\.)?youtube\.com/(?:watch\?v=|shorts/)[A-Za-z0-9_-]{11}", youtube_url))


def timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def expiration_flags(row: dict[str, Any], now: datetime) -> tuple[bool, bool]:
    data = metadata(row)
    if row.get("status") != "completed" or not confirmed_upload(data):
        return False, False

    uploaded = timestamp(data.get("published_at")) or timestamp(data.get("upload_confirmed_observed_at"))
    render_bucket = os.getenv("SUPABASE_VIDEO_BUCKET", "renders")
    thumb_bucket = os.getenv("SUPABASE_THUMBNAIL_BUCKET", "thumbnails")
    video_url = "" if data.get("preview_expired_at") else str(data.get("video_url") or data.get("storage_url") or "")
    thumb_url = "" if data.get("thumbnail_expired_at") else str(data.get("thumbnail_url") or "")
    archive_video = (uploaded is not None and now - uploaded >= timedelta(hours=PREVIEW_HOURS)
                     and ("/" + render_bucket + "/") in video_url)
    thumbnail_since = uploaded or timestamp(data.get("completed_at")) or timestamp(row.get("created_at"))
    if thumbnail_since and data.get("publish_at"):
        release = timestamp(data["publish_at"])
        thumbnail_since = max(thumbnail_since, release) if release else None
    archive_thumbnail = (thumbnail_since is not None and now - thumbnail_since >= timedelta(days=THUMBNAIL_DAYS)
                         and ("/" + thumb_bucket + "/") in thumb_url
                         and data.get("thumbnail_publish_status") not in {"pending", "retry", "failed"})
    return archive_video, archive_thumbnail


def clean_row(client: Any, row: dict[str, Any], now: datetime, dry_run: bool = False, *, has_master_bucket: bool | None = None) -> dict[str, Any]:
    archive_video, archive_thumbnail = expiration_flags(row, now)
    video_id = str(row["id"])
    outcome = {"id": video_id, "video": archive_video, "thumbnail": archive_thumbnail}
    data = metadata(row)
    needs_observation = (confirmed_upload(data) and row.get("status") == "completed"
                         and (data.get("video_url") or data.get("storage_url")) and not data.get("preview_expired_at")
                         and not (timestamp(data.get("published_at")) or timestamp(data.get("upload_confirmed_observed_at"))))
    if dry_run or not (archive_video or archive_thumbnail or needs_observation):
        return outcome
    from worker import save_fields
    if needs_observation:
        save_fields(client, row, guard_snapshot=True, upload_confirmed_observed_at=now.isoformat())
    if archive_video:
        bucket = os.getenv("SUPABASE_VIDEO_BUCKET", "renders")
        filename = preview_filename(row)
        if not urlsplit(str(data.get("video_url") or data.get("storage_url"))).path.endswith(f"/{bucket}/{filename}"):
            raise ValueError("Preview does not belong to this production.")
        client.storage.from_(bucket).remove(
            [filename] if filename == f"{video_id}.mp4" else [filename, f"{video_id}.mp4"])
        if data.get("upload_master") or has_master_bucket:
            from studio_master import remove_upload_master

            remove_upload_master(client, video_id, has_bucket=has_master_bucket)
        save_fields(client, row, guard_snapshot=True, video_url=None, storage_url=None, upload_master=None,
                    preview_expired_at=now.isoformat())
    if archive_thumbnail:
        bucket = os.getenv("SUPABASE_THUMBNAIL_BUCKET", "thumbnails")
        if not urlsplit(str(data.get("thumbnail_url"))).path.endswith(f"/{bucket}/{video_id}.jpg"):
            raise ValueError("Thumbnail does not belong to this production.")
        client.storage.from_(bucket).remove([video_id + ".jpg"])
        save_fields(client, row, guard_snapshot=True, thumbnail_url=None,
                    thumbnail_expired_at=now.isoformat())
    return outcome


def video_rows(client: Any, statuses: list[str]):
    offset, page_size = 0, 200
    while True:
        rows = (client.table("videos").select("*").in_("status", statuses)
                .order("created_at").order("id").range(offset, offset + page_size - 1).execute().data or [])
        yield from rows
        if len(rows) < page_size:
            return
        offset += page_size


def attention_action(row: dict[str, Any]) -> str | None:
    data = metadata(row)
    if row.get("status") in {"pending", "rendering"}:
        return None
    if confirmed_upload(data) or data.get("publish_status") == "uploaded":
        return "dismiss_thumbnail" if data.get("thumbnail_publish_status") == "failed" else None
    if row.get("status") == "failed" or data.get("publish_status") in {
        "upload_uncertain", "failed", "auth_required", "not_configured", "retry",
    }:
        return "delete"
    return None


def clear_attention(client: Any, rows: list[dict], pinned: set[str], dry_run: bool, include_new: bool, *, has_master_bucket: bool) -> None:
    from worker import guard_video_snapshot, save_fields
    from studio_master import remove_upload_master
    from studio_retry import retry_io

    deleted = dismissed = deferred = removed = 0
    cleanup_errors = []
    for row in rows:
        marked = bool(metadata(row).get("discard_requested_at"))
        if not (marked or include_new):
            continue
        action = attention_action(row)
        if not action:
            continue
        video_id = row["id"]
        if action == "dismiss_thumbnail":
            if not dry_run:
                save_fields(client, row, guard_snapshot=True, thumbnail_publish_status="dismissed")
            dismissed += 1
            continue
        if video_id in pinned:
            deferred += 1
            continue
        if not dry_run:
            if not marked:
                try:
                    save_fields(client, row, guard_snapshot=True, discard_requested_at=datetime.now(timezone.utc).isoformat(),
                                publish_approved_at=None, publish_requested=False)
                except ValueError:
                    deferred += 1
                    continue
            print("[attention] removing local media for", video_id, flush=True)
            cleaned = True
            for setting, default, paths in (
                ("SUPABASE_VIDEO_BUCKET", "renders", [f"{video_id}.mp4", f"{video_id}-pace-v1.mp4"]),
                ("SUPABASE_THUMBNAIL_BUCKET", "thumbnails", [f"{video_id}.jpg"]),
            ):
                bucket = client.storage.from_(os.getenv(setting, default))
                try:
                    removed += len(retry_io(lambda: bucket.remove(paths), "remove discarded Studio media") or [])
                except Exception as error:
                    cleaned = False
                    cleanup_errors.append(video_id)
                    print("[attention] media removal failed", video_id, default, type(error).__name__, flush=True)
            try:
                removed += remove_upload_master(client, video_id, has_bucket=has_master_bucket)
            except Exception as error:
                cleaned = False
                cleanup_errors.append(video_id)
                print("[attention] master removal failed", video_id, type(error).__name__, flush=True)
            if not cleaned:
                continue
            changed = guard_video_snapshot(client.table("videos").delete().eq("id", video_id), row).execute().data
            if not changed:
                deferred += 1
                continue
        deleted += 1
    print(f"[attention] {'dry run' if dry_run else 'complete'}: {deleted} deleted, {dismissed} thumbnail issues dismissed,"
          f" {removed} storage objects removed, {deferred} deferred", flush=True)
    if cleanup_errors:
        raise RuntimeError("Could not remove all storage objects for: " + ", ".join(sorted(set(cleanup_errors))))


def cleanup_orphan_media(client: Any, now: datetime, dry_run: bool, *,
                         has_master_bucket: bool) -> None:
    """Remove old Studio files with no production record, including legacy orphans.

    Never delete unidentified filenames, newly uploaded files, or anything whose
    UUID still exists in the database. Storage objects must be deleted through
    the Storage API, not by manipulating storage.objects in SQL.
    """
    from studio_master import MASTER_BUCKET, media_objects
    from studio_retry import retry_io

    current_ids = set()
    offset = 0
    while True:
        batch = (client.table("videos").select("id").order("id")
                 .range(offset, offset + 199).execute().data or [])
        current_ids.update(str(row["id"]) for row in batch)
        if len(batch) < 200:
            break
        offset += len(batch)
    uuid = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
    buckets = [
        (os.getenv("SUPABASE_VIDEO_BUCKET", "renders"), rf"({uuid})(?:-pace-v1)?\.mp4"),
        (os.getenv("SUPABASE_THUMBNAIL_BUCKET", "thumbnails"), rf"({uuid})\.jpg"),
    ]
    if has_master_bucket:
        buckets.append((MASTER_BUCKET, rf"({uuid})-[0-9a-f]{{64}}-\d{{3}}\.part"))
    orphan_count = orphan_bytes = 0
    for bucket_name, expression in buckets:
        targets = []
        for item in media_objects(client, bucket_name):
            found = re.fullmatch(expression, str(item.get("name") or ""), re.I)
            created = timestamp(item.get("created_at"))
            if (not found or found.group(1) in current_ids or created is None
                    or now - created < timedelta(hours=24)):
                continue
            targets.append(item)
        if targets and not dry_run:
            names = [item["name"] for item in targets]
            for index in range(0, len(names), 100):
                retry_io(lambda chunk=names[index:index + 100]: client.storage.from_(bucket_name).remove(chunk),
                         "remove orphaned Studio media")
            remaining = {item["name"] for item in media_objects(client, bucket_name)}
            if any(name in remaining for name in names):
                raise RuntimeError("Storage still contains orphaned files from " + bucket_name)
        orphan_count += len(targets)
        orphan_bytes += sum(int((item.get("metadata") or {}).get("size") or 0) for item in targets)
    print(f"[retention] {'dry-run' if dry_run else 'verified'} orphan cleanup: "
          f"{orphan_count} files, {orphan_bytes} bytes", flush=True)


def delete_one_failed_video(client: Any, video_id: str, expected_title: str) -> None:
    """Owner-authorized maintenance removal of one exact failed, unuploaded job."""
    from studio_master import MASTER_BUCKET, bucket_exists
    if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", video_id, re.I):
        raise ValueError("Invalid video id")
    if not expected_title:
        raise ValueError("Expected title required to prevent wrong-job removal")
    rows = client.table("videos").select("*").eq("id", video_id).limit(1).execute().data or []
    if not rows:
        print("[single-delete] already absent:", video_id, flush=True)
        return
    row = rows[0]
    if row.get("title", "").strip().casefold() != expected_title.strip().casefold():
        raise ValueError("Video title does not match the explicitly selected failed production")
    if row.get("status") != "failed" or attention_action(row) != "delete":
        raise ValueError("Selected production is not a failed, unuploaded job; refusing delete")
    active = list(video_rows(client, ["pending", "rendering"]))
    pinned = {revision["parent_id"] for item in active
              if isinstance(revision := metadata(item).get("revision"), dict)
              and revision.get("original_media") and revision.get("parent_id")}
    if video_id in pinned:
        raise ValueError("A running revision uses this production")
    clear_attention(client, [row], pinned, False, True,
                    has_master_bucket=bucket_exists(client, MASTER_BUCKET))
    remaining = client.table("videos").select("id").eq("id", video_id).execute().data or []
    if remaining:
        raise RuntimeError("The failed production record remains after cleanup")
    print("[single-delete] verified database removal:", video_id, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--clear-attention", action="store_true")
    parser.add_argument("--delete-video-id")
    parser.add_argument("--expected-title")
    args = parser.parse_args()
    from supabase import create_client
    from studio_master import MASTER_BUCKET, bucket_exists
    url = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError("Missing Supabase URL or service-role key for maintenance")
    client = create_client(url, key)
    if args.delete_video_id:
        delete_one_failed_video(client, args.delete_video_id, args.expected_title or "")
        return
    now = datetime.now(timezone.utc)
    pinned = {revision["parent_id"] for row in video_rows(client, ["pending", "rendering"])
              if isinstance(revision := metadata(row).get("revision"), dict)
              and revision.get("original_media") and revision.get("parent_id")}
    has_master_bucket = bucket_exists(client, MASTER_BUCKET)
    rows = list(video_rows(client, ["completed", "failed"]))
    if args.clear_attention or any(metadata(row).get("discard_requested_at") for row in rows):
        clear_attention(client, rows, pinned, args.dry_run, args.clear_attention, has_master_bucket=has_master_bucket)
    rows = [row for row in rows if row["status"] == "completed" and not metadata(row).get("discard_requested_at")]
    inventory = {}
    names = [("renders", os.getenv("SUPABASE_VIDEO_BUCKET", "renders")),
             ("thumbnails", os.getenv("SUPABASE_THUMBNAIL_BUCKET", "thumbnails"))]
    if has_master_bucket:
        names.append(("masters", MASTER_BUCKET))
    for label, name in names:
        storage = client.storage.from_(name)
        objects = []
        for offset in range(0, 10000, 1000):
            batch = storage.list("", {"limit": 1000, "offset": offset})
            objects.extend(item for item in batch if item.get("id"))
            if len(batch) < 1000:
                break
        inventory[label] = {"files": len(objects),
                              "bytes": sum(int((item.get("metadata") or {}).get("size") or 0) for item in objects)}
    print("[retention] storage inventory", json.dumps(inventory), flush=True)
    eligible = 0
    failed = 0
    for row in rows:
        if row.get("id") in pinned:
            continue
        try:
            result = clean_row(client, row, now, args.dry_run, has_master_bucket=has_master_bucket)
            if result["video"] or result["thumbnail"]:
                eligible += 1
                print("[retention]", result, "dry-run" if args.dry_run else "deleted", flush=True)
        except Exception as error:
            failed += 1
            print("[retention]", row.get("id"), type(error).__name__, flush=True)
    print("[retention] eligible", eligible, "of", len(rows), flush=True)
    cleanup_orphan_media(client, now, args.dry_run, has_master_bucket=has_master_bucket)
    if failed:
        raise RuntimeError(f"Retention failed for {failed} production(s).")


if __name__ == "__main__":
    main()
