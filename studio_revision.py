"""Reuse timestamped media from a saved Studio preview for scene-level revisions."""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import wave
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
        raise ValueError("This preview has no saved edit points for a targeted revision.")
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



def picture_track(path: Path) -> dict:
    from studio_audio import probe
    return next(track for track in probe(path)["streams"] if track["codec_type"] == "video")


def picture_keys(path: Path) -> set[int]:
    packets = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_packets",
         "-show_entries", "packet=pts_time,flags", "-of", "json", str(path)],
        check=True, capture_output=True, text=True, timeout=60,
    )
    return {round(float(packet["pts_time"]) * 30) for packet in json.loads(packets.stdout)["packets"]
            if "K" in packet.get("flags", "") and "pts_time" in packet}


def source_picture(parent: Path, start: int, end: int, keyframes: set[int], directory: Path) -> list[Path]:
    """Copy full-GOP footage and encode the edge only if a saved cut falls inside a GOP."""
    from worker import run, video_encoder
    if start >= end:
        return []
    copy_start = min((frame for frame in keyframes if start <= frame < end), default=end)
    parts = []
    if copy_start > start:
        edge = directory / f"source_edge_{start}.mp4"
        count = copy_start - start
        run(["ffmpeg", "-y", "-v", "error", "-xerror", "-ss", f"{start / 30:.8f}", "-i", str(parent),
             "-map", "0:v:0", "-an", "-vf", "fps=30,setsar=1,tpad=stop_mode=clone:stop_duration=0.2",
             "-frames:v", str(count), *video_encoder(), str(edge)], timeout=max(180, count // 3))
        parts.append(edge)
    if copy_start < end:
        part = directory / f"source_copy_{copy_start}.mp4"
        count = end - copy_start
        # Seek just inside the keyframe so decimal rounding cannot select the previous GOP.
        run(["ffmpeg", "-y", "-v", "error", "-xerror", "-ss", f"{(copy_start + .1) / 30:.8f}",
             "-i", str(parent), "-map", "0:v:0", "-an", "-frames:v", str(count),
             "-c:v", "copy", "-video_track_timescale", "90000", str(part)])
        if int(picture_track(part).get("nb_frames") or 0) != count:
            raise ValueError("The source picture did not provide the expected frames for a targeted edit.")
        parts.append(part)
    return parts


def copy_audio_samples(parent: Path, start: int, end: int, output: Path):
    from worker import run
    samples = end - start
    run(["ffmpeg", "-y", "-v", "error", "-xerror", "-i", str(parent), "-map", "0:a:0",
         "-af", f"atrim=start_sample={start}:end_sample={end},asetpts=PTS-STARTPTS,apad=whole_len={samples}",
         "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(output)])


def stitch_revision(parent: Path, start: int, end: int, pictures: list[Path], voices: list[Path],
                    mode: str, directory: Path) -> Path:
    from worker import run
    track = picture_track(parent)
    frames = round(float(track["duration"]) * 30)
    if not 0 <= start < end <= frames:
        raise ValueError("The selected scene is outside the saved preview.")
    final = directory / "final_video.mp4"
    ffmpeg = ["ffmpeg", "-y", "-v", "error", "-xerror"]
    if mode == "audio":
        video_input = ["-i", str(parent)]
        expected_frames = frames
    else:
        # The time reset below is safe for non-reordered frames. For older previews with B frames,
        # decode the saved picture spans locally, without synthesizing or sourcing their content.
        keyframes = picture_keys(parent) if track.get("has_b_frames") == 0 else set()
        before = source_picture(parent, 0, start, keyframes, directory)
        after = source_picture(parent, end, frames, keyframes, directory)
        spans = before + pictures + after
        expected_frames = sum(int(picture_track(path)["nb_frames"]) for path in spans)
        manifest = directory / "revision_picture.ffconcat"
        manifest.write_text("ffconcat version 1.0\n" + "".join(f"file '{path.name}'\n" for path in spans))
        video_input = ["-f", "concat", "-safe", "0", "-i", str(manifest)]
    if mode == "visual":
        if expected_frames != frames:
            raise ValueError("A footage-only edit must keep the source picture length.")
        audio_input = ["-i", str(parent)]
        audio_codec = ["-c:a", "copy"]
    else:
        pieces = []
        for index, (left, right) in enumerate(((0, start), (end, frames))):
            if left == right:
                pieces.append(None)
            else:
                output = directory / f"source_audio_span_{index}.wav"
                copy_audio_samples(parent, left * 1600, right * 1600, output)
                pieces.append(output)
        pcm = directory / "revision_master.wav"
        with wave.open(str(pcm), "wb") as master:
            master.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
            for part in (pieces[0], *voices, pieces[1]):
                if part is not None:
                    with wave.open(str(part), "rb") as source:
                        master.writeframes(source.readframes(source.getnframes()))
        audio_input = ["-i", str(pcm)]
        audio_codec = ["-c:a", "aac", "-b:a", "160k"]
    picture_clock = [] if mode == "audio" else [
        "-bsf:v", "setts=pts=N*3000:dts=N*3000:duration=3000:time_base=1/90000",
        "-video_track_timescale", "90000",
    ]
    run([*ffmpeg, *video_input, *audio_input, "-map", "0:v:0", "-map", "1:a:0",
         "-c:v", "copy", *picture_clock, *audio_codec, "-movflags", "+faststart", str(final)], timeout=600)
    if int(picture_track(final).get("nb_frames") or 0) != expected_frames:
        raise ValueError("The revised picture is missing frames.")
    validate_audio(final, expected=expected_frames / 30)
    # Catch incompatible compressed picture streams before a preview is marked ready.
    run([*ffmpeg, "-i", str(final), "-map", "0:v:0", "-f", "null", "-"], timeout=max(180, expected_frames // 8))
    return final
