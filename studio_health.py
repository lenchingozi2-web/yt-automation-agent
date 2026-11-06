"""Read-only GitHub Actions integration health check. Never prints credentials or rows."""
from __future__ import annotations

import os
import sys
from supabase import create_client


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
    if not os.getenv("PIXABAY_API_KEY"):
        print("WARNING: PIXABAY_API_KEY is missing in GitHub Actions (safe dark fallback only).")
    else:
        print("OK: Pixabay key configured (API access itself not tested).")
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
    for name in ("renders", "thumbnails"):
        try:
            client.storage.get_bucket(name)
            print("OK: Storage bucket exists:", name)
        except Exception:
            print("NOTICE: Storage bucket not created yet:", name,
                  "(worker will create it on its first successful upload).")
    from studio_maintenance import metadata
    rows = client.table("videos").select("*").order("created_at", desc=True).limit(100).execute().data or []
    from collections import Counter
    print("QUEUE COUNTS:", dict(Counter(r.get("status") for r in rows)), flush=True)
    print("UPLOAD COUNTS:", dict(Counter(metadata(r).get("publish_status", "legacy") for r in rows)), flush=True)
    print("V4 RENDERS:", sum(metadata(r).get("render_version") == "studio-v4" for r in rows), flush=True)
    print("PASS: Read-only Studio database integration probe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
