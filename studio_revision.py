"""Reuse timestamped media from a saved Studio preview for scene-level revisions."""
from __future__ import annotations

import math
import os
import re
from pathlib import Path

from studio_audio import validate_audio
from studio_maintenance import metadata


def footage_history(client, video_id):
    recent = client.table("videos").select("id,scenes").eq("status", "completed").order("created_at", desc=True).limit(10).execute()
    return {str(clip) for row in recent.data or [] if row.get("id") != video_id
            for item in metadata(row).get("scene_timeline", []) for clip in item.get("footage", [])}


def prepare_revision(client, video, details, directory: Path):
    revision = details.get("revision")
    if not isinstance(revision, dict):
        return None, {}, set()
    parent_id = revision.get("parent_id", "")
    if not isinstance(parent_id, str) or not re.fullmatch(r"[a-fA-F0-9-]{36}", parent_id):
        raise ValueError("Revision has no valid parent video")
    if not revision.get("original_media"):
        # Historical rows have no reliable edit points. Use their saved script as the first baseline.
        return None, {}, set()
    parent = client.table("videos").select("*").eq("id", parent_id).limit(1).execute()
    row = next(iter(parent.data or []), None)
    if not row or (row.get("user_id") and row.get("user_id") != video.get("user_id")):
        raise RuntimeError("The source video for this revision is unavailable")
    base = metadata(row)
    timeline = base.get("scene_timeline")
    if not isinstance(timeline, list) or not timeline:
        raise RuntimeError("The source video has no saved edit points")
    if base.get("preview_expired_at"):
        raise RuntimeError("The source preview expired after the revision was queued")

    by_scene = {}
    footage = set()
    previous_end = 0.0
    for entry in timeline:
        if not isinstance(entry, dict):
            raise ValueError("The source video has invalid edit points")
        start, duration = float(entry["start"]), float(entry["duration"])
        if not math.isfinite(start) or not math.isfinite(duration) or abs(start - previous_end) > .07 or not 0 < duration <= 120:
            raise ValueError("The source video has invalid edit points")
        by_scene.setdefault(int(entry["scene_number"]), []).append(entry)
        footage.update(str(clip) for clip in entry.get("footage", []))
        previous_end = start + duration
    path = directory / "source_preview.mp4"
    bucket = client.storage.from_(os.getenv("SUPABASE_VIDEO_BUCKET", "renders"))
    path.write_bytes(bucket.download(parent_id + ".mp4"))
    return path, by_scene, footage


def extract_audio(parent: Path, entry: dict, path: Path):
    from worker import run
    duration = float(entry["duration"])
    run(["ffmpeg", "-y", "-v", "error", "-xerror", "-ss", str(entry["start"]), "-i", str(parent),
         "-t", f"{duration:.6f}", "-map", "0:a:0", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(path)])
    validate_audio(path, expected=duration)


def reuse_visual(parent: Path, entry: dict, scene: dict, directory: Path, replacement: Path | None = None):
    from worker import run, prepare_scene_audio, video_size, video_encoder
    number = scene["scene_number"]
    output = directory / f"rendered_{number:02d}.mp4"
    pcm = output.with_suffix(".wav")
    duration = float(entry["duration"])
    if replacement:
        seconds = validate_audio(replacement)
        prepare_scene_audio(replacement, pcm, duration, tempo=seconds / duration)
    else:
        extract_audio(parent, entry, pcm)
    width, height = video_size(scene)
    run(["ffmpeg", "-y", "-v", "error", "-xerror", "-ss", str(entry["start"]), "-i", str(parent),
         "-i", str(pcm), "-t", f"{duration:.6f}", "-map", "0:v:0", "-map", "1:a:0",
         "-frames:v", str(round(duration * 30)),
         "-vf", f"scale={width}:{height}:flags=lanczos,setsar=1,fps=30",
         *video_encoder(), "-c:a", "aac", "-b:a", "160k", str(output)], timeout=300)
    return output
