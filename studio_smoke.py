"""Disposable REAL render smoke test (Supabase + Edge TTS + FFmpeg + JPEG).
Creates an isolated test row directly in rendering state so the scheduled worker
does not take it. Always removes the row and uploaded test media. Does not
publish to YouTube or use the user's real drafts.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from supabase import create_client
from studio_maintenance import metadata
from worker import ROOT, process
# Fourth diagnostic run: verify deterministic subtitles and final media storage.


def main() -> int:
    url = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
    if not url or not key:
        print("BLOCKED: GitHub Actions Supabase credentials missing")
        return 2
    client = create_client(url, key)
    os.environ.pop("YOUTUBE_TOKEN_JSON", None)  # Never publish a smoke test.
    user_id = None
    try:
        users = client.auth.admin.list_users(page=1, per_page=1)
        candidates = users.users if hasattr(users, "users") else users if isinstance(users, list) else []
        if candidates:
            user_id = str(candidates[0].id)
    except Exception:
        pass
    draft = [{
        "scene_number": 1, "narration": "This is a short system verification video for Studio.",
        "search_queries": ["ocean waves"], "format": "16:9",
        "_studio": {"aspect_ratio": "16:9", "thumbnail_title": "STUDIO VIDEO TEST", "duration_seconds": 8},
    }]
    base = {"title": "Studio disposable rendering test", "status": "rendering",
            "description": "Temporary integration test. Removed after validation.",
            "scenes": draft}
    options = [{**base, "user_id": user_id}, base] if user_id else [base]
    row = None
    for payload in options:
        try:
            reply = client.table("videos").insert(payload).execute()
            if reply.data:
                row = reply.data[0]
                break
        except Exception as error:
            print("Schema insert candidate rejected:", type(error).__name__, str(error)[:220])
    if row is None:
        print("BLOCKED: Could not insert a schema-compatible disposable test job")
        return 3

    video_id = str(row["id"])
    try:
        process(row, client)
        result = client.table("videos").select("*").eq("id", video_id).single().execute()
        updated = result.data
        stored = metadata(updated)
        assert updated["status"] == "completed", "worker did not mark job completed"
        assert stored.get("video_url"), "no playable MP4 URL was saved"
        assert stored.get("thumbnail_url"), "no JPEG thumbnail URL was saved"
        mp4 = client.storage.from_(os.getenv("SUPABASE_VIDEO_BUCKET", "renders")).download(video_id + ".mp4")
        jpg = client.storage.from_(os.getenv("SUPABASE_THUMBNAIL_BUCKET", "thumbnails")).download(video_id + ".jpg")
        assert len(mp4) > 10000 and b"ftyp" in mp4[:64], "MP4 not playable"
        assert jpg[:2] == bytes.fromhex("ffd8"), "invalid JPEG thumbnail"
        print("PASS: Real test render saved; MP4 and thumbnail downloaded and validated.")
        return 0
    except Exception as error:
        print("FAILED: Real render:", type(error).__name__, str(error)[-1200:])
        return 4
    finally:
        for bucket, suffix in (("renders", ".mp4"), ("thumbnails", ".jpg")):
            try:
                client.storage.from_(os.getenv("SUPABASE_VIDEO_BUCKET" if bucket == "renders" else "SUPABASE_THUMBNAIL_BUCKET", bucket)).remove([video_id + suffix])
            except Exception:
                print("WARNING: Could not remove disposable", bucket, "media")
        try:
            client.table("videos").delete().eq("id", video_id).execute()
        except Exception:
            print("WARNING: Could not remove disposable test row", video_id)
        shutil.rmtree(ROOT / video_id, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
