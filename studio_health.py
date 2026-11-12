"""Read-only GitHub Actions integration health check. Never prints credentials or rows."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import os
import sys
import httpx
from supabase import create_client

from studio_maintenance import metadata
from studio_publish import has_approval, parse_timestamp
from studio_retry import RENDER_STALE_SECONDS, REVISION_STALE_SECONDS, retry_io


def main() -> int:
    url = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
    missing = []
    if not url:
        missing.append("SUPABASE_URL or NEXT_PUBLIC_SUPABASE_URL")
    if not key:
        missing.append("SUPABASE_SERVICE_ROLE_KEY or SUPABASE_KEY")
    if missing:
        print("BLOCKED: GitHub Actions repository secrets missing:", ", ".join(missing))
        return 2
    from urllib.parse import urlparse
    print("PROJECT:", urlparse(url).hostname, flush=True)
    client = create_client(url, key)
    try:
        response = client.table("videos").select("id,title,status,created_at").limit(1).execute()
        print("OK: Studio videos table is readable; database returned", len(response.data or []), "sample rows (contents hidden).")
    except Exception as error:
        print("BLOCKED: videos table read failed:", type(error).__name__, str(error)[:320])
        return 3
    available = []
    columns = ("id", "title", "status", "created_at", "scenes", "script", "metadata",
               "video_url", "thumbnail_url", "youtube_url", "description", "tags",
               "duration_seconds", "duration", "format", "aspect_ratio", "error_message", "user_id")
    for column in columns:
        try:
            client.table("videos").select(column).limit(0).execute()
            available.append(column)
        except Exception:
            pass
    print("SCHEMA: Existing videos columns: " + ", ".join(available), flush=True)
    if not ({"scenes", "script"} & set(available)):
        print("BLOCKED: videos table needs a scenes or script column to hold render data.")
        return 4
    healthy = True
    pixabay_key = os.getenv("PIXABAY_API_KEY", "").strip()
    if pixabay_key:
        try:
            def check_pixabay():
                response = httpx.get("https://pixabay.com/api/videos/", params={
                    "key": pixabay_key, "q": "forest", "safesearch": "true", "per_page": 3,
                }, timeout=10)
                response.raise_for_status()
                return response.json()["hits"]

            if not retry_io(check_pixabay, "check Pixabay search"):
                raise ValueError("empty search response")
            print("OK: Pixabay video search returned footage.")
        except Exception as error:
            print("BLOCKED: Pixabay search failed.", type(error).__name__)
            healthy = False
    else:
        print("BLOCKED: PIXABAY_API_KEY is missing in GitHub Actions.")
        healthy = False
    if not os.getenv("YOUTUBE_TOKEN_JSON"):
        print("NOTICE: YouTube automatic uploads disabled until YOUTUBE_TOKEN_JSON is configured.")
    else:
        from worker import youtube_credentials, safe_publish_error
        try:
            youtube_credentials()
            print("OK: YouTube credentials refreshed successfully (no video uploaded).")
        except Exception as error:
            _, note = safe_publish_error(error)
            print("BLOCKED:", note, flush=True)
    for setting, default in (("SUPABASE_VIDEO_BUCKET", "renders"), ("SUPABASE_THUMBNAIL_BUCKET", "thumbnails")):
        bucket = os.getenv(setting, default)
        try:
            retry_io(lambda: client.storage.get_bucket(bucket), f"check {default} storage")
            print("OK: Storage bucket exists:", default)
        except Exception as error:
            print("BLOCKED: Storage bucket unavailable:", default, type(error).__name__)
            healthy = False

    active = client.table("videos").select("*").in_("status", ["pending", "rendering", "failed"]).order("created_at").limit(100).execute().data or []
    completed = client.table("videos").select("*").eq("status", "completed").order("created_at", desc=True).limit(100).execute().data or []
    print("QUEUE COUNTS:", dict(Counter(row.get("status") for row in [*active, *completed])), flush=True)
    approved = [metadata(row) for row in completed if has_approval(metadata(row))]
    print("APPROVED UPLOAD COUNTS:", dict(Counter(row.get("publish_status", "legacy") for row in approved)), flush=True)
    now = datetime.now(timezone.utc)
    for row in active:
        data = metadata(row)
        queued = parse_timestamp(row.get("created_at"))
        if row["status"] == "pending" and queued:
            print("PENDING AGE:", max(0, round((now - queued).total_seconds())), "seconds", flush=True)
        if row["status"] == "rendering":
            last = parse_timestamp(data.get("worker_heartbeat_at")) or parse_timestamp(data.get("render_started_at")) or queued
            if last:
                age = max(0, round((now - last).total_seconds()))
                threshold = REVISION_STALE_SECONDS if data.get("revision") else RENDER_STALE_SECONDS
                print("ACTIVE RENDER:", "stale" if age >= threshold else "working",
                      "heartbeat", age, "seconds ago", flush=True)
    for row in completed:
        data = metadata(row)
        if not data.get("revision"):
            continue
        queued = parse_timestamp(row.get("created_at"))
        started = parse_timestamp(data.get("render_started_at"))
        finished = parse_timestamp(data.get("completed_at"))
        if queued and started and finished and queued <= started <= finished:
            print("LATEST REVISION TIMING:", round((started - queued).total_seconds()), "seconds queued;",
                  round((finished - started).total_seconds()), "seconds in worker", flush=True)
            break
    print("PASS: Studio database, storage and stock-footage probe." if healthy else "FAILED: Studio render dependencies require attention.")
    return 0 if healthy else 5



if __name__ == "__main__":
    sys.exit(main())
