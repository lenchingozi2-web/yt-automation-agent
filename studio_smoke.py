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
from studio_media import verify_thumbnail
from studio_publish import has_approval
from studio_retry import TemporaryRenderError
from worker import ROOT, fetch_broll, process, render_job, verify_render_geometry
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
        "scene_number": 1, "narration": "Ocean waves roll toward the beach and wash over the sand.",
        "search_queries": ["ocean waves"], "format": "16:9",
        "_studio": {"aspect_ratio": "16:9", "thumbnail_title": "STUDIO VIDEO TEST", "duration_seconds": 10},
    }, {
        "scene_number": 2, "narration": "Forest trees shade the woodland path along the hillside.",
        "search_queries": ["forest trees", "woodland path"], "format": "16:9",
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
        assert stored.get("render_version") == "studio-v7"
        assert len(stored["scene_timeline"]) == 2 and not has_approval(stored)
        assert stored["scene_timeline"][0]["footage"], "the ocean narration selected no related stock media"
        from studio_audio import validate_audio
        for scene_file in (ROOT / video_id).glob("rendered_*.mp4"):
            validate_audio(scene_file)
        validate_audio(ROOT / video_id / "final_video.mp4")
        mp4 = client.storage.from_(os.getenv("SUPABASE_VIDEO_BUCKET", "renders")).download(video_id + ".mp4")
        jpg = client.storage.from_(os.getenv("SUPABASE_THUMBNAIL_BUCKET", "thumbnails")).download(video_id + ".jpg")
        assert len(mp4) > 10000 and b"ftyp" in mp4[:64], "MP4 not playable"
        assert jpg[:2] == bytes.fromhex("ffd8"), "invalid JPEG thumbnail"
        for ratio, target, size in [("16:9", 10, (1920, 1080)), ("9:16", 14, (1080, 1920))]:
            render_id = video_id
            if ratio == "9:16":
                portrait_draft = deepcopy(draft)
                portrait_draft[0]["_studio"]["aspect_ratio"] = ratio
                portrait_draft[0]["_studio"]["duration_seconds"] = target
                portrait = client.table("videos").insert({**base, **({"user_id": row["user_id"]} if row.get("user_id") else {}),
                                                            "scenes": portrait_draft}).execute().data[0]
                render_id = str(portrait["id"])
                disposable_ids.append(render_id)
                with patch("worker.fetch_broll", side_effect=lambda scene, *args:
                           None if scene["logical_scene"] == 2 else fetch_broll(scene, *args)):
                    process(portrait, client)
                portrait_meta = metadata(client.table("videos").select("*").eq("id", render_id).single().execute().data)
                assert portrait_meta["scene_timeline"][1]["narration_cards"] > 0
                assert not portrait_meta["scene_timeline"][1]["footage"]
                print("PASS: An unmatched scene rendered with timed narration cards.")
            path = ROOT / render_id / "preview_from_storage.mp4"
            path.write_bytes(client.storage.from_(os.getenv("SUPABASE_VIDEO_BUCKET", "renders")).download(render_id + ".mp4"))
            poster = path.with_suffix(".jpg")
            poster.write_bytes(client.storage.from_(os.getenv("SUPABASE_THUMBNAIL_BUCKET", "thumbnails")).download(render_id + ".jpg"))
            verify_thumbnail(poster, ratio)
            measured_ratio, measured_seconds = verify_render_geometry(path, size)
            assert abs(measured_seconds - target) <= 5
            assert measured_ratio == ratio
            if ratio == "9:16":
                assert abs(measured_seconds - target) > 0.1, "expected a natural runtime for the shorter narration"
            print(f"PASS: Stored {measured_ratio} MP4, {size[0]}×{size[1]}, {measured_seconds:.3f}s against {target}s target, and matching thumbnail.")
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
        visual_draft[1]["search_queries"] = ["mountain waterfall", "waterfall"]
        visual_draft[1]["footage_override"] = True
        visual_row = client.table("videos").insert({**payload, "scenes": visual_draft}).execute().data[0]
        visual_id = str(visual_row["id"])
        disposable_ids.append(visual_id)
        def intermittent_footage(*args):
            if footage.call_count == 1:
                raise TemporaryRenderError("Injected provider timeout for the retry check.")
            return fetch_broll(*args)

        with (
            patch("worker.make_audio", side_effect=AssertionError("Footage-only edits must reuse narration")),
            patch("worker.fetch_broll", side_effect=intermittent_footage) as footage,
        ):
            assert render_job(visual_row, client), "worker did not recover the footage-only revision"
            assert footage.call_count >= 2 and all(call.args[0]["logical_scene"] == 2 for call in footage.call_args_list)
        visual_meta = metadata(client.table("videos").select("*").eq("id", visual_id).single().execute().data)
        assert visual_meta["status"] == "completed" and visual_meta["publish_status"] == "awaiting_review"
        assert visual_meta["scene_timeline"][0] == stored["scene_timeline"][0]
        assert not has_approval(visual_meta) and not visual_meta.get("youtube_url")
        assert visual_meta["scene_timeline"][1]["footage"]
        assert visual_meta["scene_timeline"][1]["footage"] != stored["scene_timeline"][1]["footage"]
        print("PASS: Footage-only edit recovered within one worker run, changed scene 2, and reused the original narration and scene 1.")
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
