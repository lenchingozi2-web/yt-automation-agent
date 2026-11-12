"""Disposable REAL render smoke test (Supabase + Edge TTS + FFmpeg + JPEG).
Creates an isolated test row directly in rendering state so the scheduled worker
does not take it. Always removes the row and uploaded test media. Does not
publish to YouTube or use the user's real drafts.
"""
from __future__ import annotations

import os
from copy import deepcopy
import shutil
import sys
from unittest.mock import patch
from pathlib import Path

from supabase import create_client
from studio_maintenance import metadata
from studio_publish import has_approval
from worker import ROOT, fetch_broll, process
# Verify multi-scene neural narration, montage, final audio and real storage.


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
    }, {
        "scene_number": 2, "narration": "The second scene checks that narration continues after the video cut.",
        "search_queries": ["forest", "nature"], "format": "16:9",
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
    disposable_ids = [video_id]
    try:
        process(row, client)
        result = client.table("videos").select("*").eq("id", video_id).single().execute()
        updated = result.data
        stored = metadata(updated)
        assert updated["status"] == "completed", "worker did not mark job completed"
        assert stored.get("video_url"), "no playable MP4 URL was saved"
        assert stored.get("thumbnail_url"), "no JPEG thumbnail URL was saved"
        assert stored.get("render_version") == "studio-v6"
        assert len(stored["scene_timeline"]) == 2 and not has_approval(stored)
        from studio_audio import validate_audio
        for scene_file in (ROOT / video_id).glob("rendered_*.mp4"):
            validate_audio(scene_file)
        validate_audio(ROOT / video_id / "final_video.mp4")
        mp4 = client.storage.from_(os.getenv("SUPABASE_VIDEO_BUCKET", "renders")).download(video_id + ".mp4")
        jpg = client.storage.from_(os.getenv("SUPABASE_THUMBNAIL_BUCKET", "thumbnails")).download(video_id + ".jpg")
        assert len(mp4) > 10000 and b"ftyp" in mp4[:64], "MP4 not playable"
        assert jpg[:2] == bytes.fromhex("ffd8"), "invalid JPEG thumbnail"
        revised_draft = deepcopy(draft)
        revised_draft[0]["_studio"]["revision"] = {"parent_id": video_id, "scene_number": 2,
                                                     "mode": "audio", "original_media": True}
        payload = {**base, "scenes": revised_draft}
        if row.get("user_id"):
            payload["user_id"] = row["user_id"]
        revision = client.table("videos").insert(payload).execute().data[0]
        revision_id = str(revision["id"])
        disposable_ids.append(revision_id)
        process(revision, client)
        revised = metadata(client.table("videos").select("*").eq("id", revision_id).single().execute().data)
        assert revised["status"] == "completed" and revised["publish_status"] == "awaiting_review"
        assert not has_approval(revised) and not revised.get("youtube_url")
        assert revised["scene_timeline"] == stored["scene_timeline"], "an audio repair moved the scene or changed footage"
        assert client.storage.from_(os.getenv("SUPABASE_VIDEO_BUCKET", "renders")).download(video_id + ".mp4") == mp4
        validate_audio(ROOT / revision_id / "final_video.mp4")
        visual_draft = deepcopy(draft)
        visual_draft[0]["_studio"]["revision"] = {"parent_id": video_id, "scene_number": 2,
                                                   "mode": "visual", "original_media": True}
        visual_draft[1]["search_queries"] = ["mountain landscape", "waterfall"]
        visual_row = client.table("videos").insert({**payload, "scenes": visual_draft}).execute().data[0]
        visual_id = str(visual_row["id"])
        disposable_ids.append(visual_id)
        with (
            patch("worker.make_audio", side_effect=AssertionError("Footage-only edits must reuse narration")),
            patch("worker.fetch_broll", wraps=fetch_broll) as footage,
        ):
            process(visual_row, client)
            assert footage.call_count and all(call.args[0]["logical_scene"] == 2 for call in footage.call_args_list)
        visual_meta = metadata(client.table("videos").select("*").eq("id", visual_id).single().execute().data)
        assert visual_meta["status"] == "completed" and visual_meta["publish_status"] == "awaiting_review"
        assert visual_meta["scene_timeline"][0] == stored["scene_timeline"][0]
        assert not has_approval(visual_meta) and not visual_meta.get("youtube_url")
        assert visual_meta["scene_timeline"][1]["footage"] != stored["scene_timeline"][1]["footage"]
        print("PASS: Real footage-only edit changed scene 2 with zero narration generation and reused scene 1.")
        print("PASS: Real MP4, thumbnail and targeted voice repair saved and validated without YouTube approval.")
        return 0
    except Exception as error:
        print("FAILED: Real render:", type(error).__name__, str(error)[-1200:])
        return 4
    finally:
        for disposable_id in reversed(disposable_ids):
            for bucket, suffix in (("renders", ".mp4"), ("thumbnails", ".jpg")):
                try:
                    client.storage.from_(os.getenv("SUPABASE_VIDEO_BUCKET" if bucket == "renders" else "SUPABASE_THUMBNAIL_BUCKET", bucket)).remove([disposable_id + suffix])
                except Exception:
                    print("WARNING: Could not remove disposable", bucket, "media")
            try:
                client.table("videos").delete().eq("id", disposable_id).execute()
            except Exception:
                print("WARNING: Could not remove disposable test row", disposable_id)
            shutil.rmtree(ROOT / disposable_id, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
