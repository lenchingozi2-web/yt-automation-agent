"""LenxiQ Studio background renderer.

Run locally with `python worker.py --once` or continuously with `python worker.py`.
The process is intentionally outside Vercel: it needs FFmpeg, local disk, and CPU audio models.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
from datetime import datetime, timezone
import os
import subprocess
import wave
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from supabase import create_client
from studio_media import create_thumbnail
from studio_audio import make_audio, validate_audio, probe
from studio_maintenance import metadata as stored_metadata
from studio_publish import credential_version, has_approval, publication_due
from studio_revision import prepare_revision, extract_audio, reuse_visual, footage_history

load_dotenv()
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
ROOT = Path(os.getenv("WORKER_OUTPUT_DIR", "output_run"))
POLL_SECONDS = int(os.getenv("WORKER_POLL_SECONDS", "20"))
SHOT_SECONDS = max(3, min(8, float(os.getenv("STUDIO_SHOT_SECONDS", "5"))))


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def run(command: list[str], *, timeout: int = 180) -> None:
    result = subprocess.run(command, check=False, timeout=timeout, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError("FFmpeg exited " + str(result.returncode) + ": " + result.stderr[-1200:])


def ffprobe_duration(path: Path) -> float:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)], check=True, capture_output=True, text=True)
    return max(0.5, float(result.stdout.strip()))


def usable_footage(path: Path) -> bool:
    """Reject unkeyed green-screen assets rather than showing them to viewers."""
    from collections import Counter
    for seconds in (.5, min(2.0, ffprobe_duration(path) / 2)):
        result = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(seconds), "-i", str(path),
            "-frames:v", "1", "-vf", "scale=64:64", "-pix_fmt", "rgb24", "-threads", "1", "-f", "rawvideo", "-"],
            capture_output=True, timeout=30)
        pixels = result.stdout
        if result.returncode or len(pixels) != 64 * 64 * 3:
            return False
        colors = Counter((pixels[i] // 32, pixels[i + 1] // 32, pixels[i + 2] // 32)
                         for i in range(0, len(pixels), 3))
        for (red, green, blue), count in colors.items():
            if green >= 5 and red <= 3 and blue <= 3 and green > red + 1 and green > blue + 1 and count > 4096 * .4:
                return False
    return True


async def fetch_pixabay(scene: dict[str, Any], target: Path, used: set[str], recent: set[str], catalog: dict) -> str | None:
    """Download a correctly oriented HD variant without ever preferring 4K bloat."""
    key = os.getenv("PIXABAY_API_KEY", "").strip()
    if not key:
        return None
    portrait = str(scene.get("format", "16:9")) == "9:16"
    queries = list(scene.get("search_queries", []))
    context = str(scene.get("visual_context", scene.get("narration", ""))).lower()
    medical = bool(re.search(r"\b(medical|health|hospital|doctor|patient|clinician|wearable)\b", context))
    queries = [str(q).strip() for q in queries if str(q).strip()]
    if medical:
        queries = [q for q in queries if not re.search(r"nature|landscape|river|subscribe|green.?screen", q, re.I)]
    if not queries:
        queries = [str(scene.get("visual_context") or scene.get("narration") or "").strip()[:70]]
    async with httpx.AsyncClient(timeout=22, follow_redirects=True) as client:
        for query in queries:
            if query not in catalog:
                response = await client.get("https://pixabay.com/api/videos/", params={
                    "key": key, "q": query[:100], "video_type": "all",
                    "safesearch": "true", "per_page": 25,
                })
                if response.status_code != 200:
                    continue
                catalog[query] = response.json().get("hits", [])
            query_words = set(re.findall(r"[a-z]{3,}", query.lower()))
            preferred = []
            fallback = []
            for hit in catalog[query]:
                tags = str(hit.get("tags", "")).lower()
                if re.search(r"green.?screen|chroma|subscribe|like button|countdown|logo reveal|intro|outro", tags):
                    continue
                if medical and not re.search(r"medic|doctor|patient|health|hospital|laborator|wearable|watch|fitness|heart|nurse|science|data|computer|technolog|artificial|brain|\bai\b", tags):
                    continue
                clip_id = str(hit.get("id") or "")
                if not clip_id or clip_id in used:
                    continue
                tag_words = set(re.findall(r"[a-z]{3,}", tags))
                score = len(query_words & tag_words) * 3 + len(set(context.split()) & tag_words)
                duration = float(hit.get("duration") or 0)
                if 0 < duration < 2.5:
                    continue
                variants = hit.get("videos", {})
                if not isinstance(variants, dict):
                    continue
                for variant in variants.values():
                    if not isinstance(variant, dict) or not variant.get("url"):
                        continue
                    width = int(variant.get("width", 0) or 0)
                    height = int(variant.get("height", 0) or 0)
                    if width < 1 or height < 1:
                        continue
                    # Landscape must never select vertical media.
                    # Portrait prefers true vertical; horizontal HD can be center cropped.
                    if portrait and height > width and width >= 480 and height >= 720:
                        preferred.append((clip_id in recent, -score, width * height, {**variant, "clip_id": clip_id}))
                    elif not portrait and width >= height and width >= 1280 and height >= 720:
                        preferred.append((clip_id in recent, -score, width * height, {**variant, "clip_id": clip_id}))
                    elif portrait and width >= height and width >= 1280 and height >= 720:
                        fallback.append((clip_id in recent, -score, width * height, {**variant, "clip_id": clip_id}))
            # Try distinct candidate clips in ranked order, using the smallest adequate variant.
            eligible = sorted(preferred, key=lambda item: item[:3]) + sorted(fallback, key=lambda item: item[:3])
            seen = set()
            for _, _, _, variant in eligible:
                clip_id = variant["clip_id"]
                if clip_id in seen:
                    continue
                if len(seen) >= 6:
                    break
                seen.add(clip_id)
                count = 0
                try:
                    async with client.stream("GET", variant["url"]) as download:
                        if download.status_code != 200:
                            continue
                        with target.open("wb") as handle:
                            async for chunk in download.aiter_bytes(chunk_size=262144):
                                count += len(chunk)
                                if count > 60_000_000:
                                    raise ValueError("Pixabay footage exceeds 60 MB safety limit")
                                handle.write(chunk)
                    if count > 10000 and ffprobe_duration(target) > 0.5 and usable_footage(target):
                        return clip_id
                except (OSError, ValueError, httpx.HTTPError, subprocess.SubprocessError) as error:
                    print(f"[b-roll] skipping unusable Pixabay clip: {str(error)[:180]}")
                finally:
                    if target.exists() and target.stat().st_size <= 10000:
                        target.unlink(missing_ok=True)
    return None


def fetch_broll(scene: dict[str, Any], directory: Path, used: set[str], recent: set[str], catalog: dict, shot: int):
    target = directory / f"raw_{int(scene['scene_number']):02d}_{shot:02d}.mp4"
    # A previous attempt can have left assets for another revision or a partial download.
    target.unlink(missing_ok=True)
    clip_id = asyncio.run(fetch_pixabay(scene, target, used, recent, catalog))
    if clip_id:
        used.add(clip_id)
        return target, clip_id
    target.unlink(missing_ok=True)
    return None


def video_size(scene):
    return (1080, 1920) if scene.get("format") == "9:16" else (1920, 1080)


def video_encoder():
    return ["-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-video_track_timescale", "90000"]


def prepare_scene_audio(audio, path, duration, *, normalize=True, tempo=1):
    filters = []
    if normalize:
        filters += ["highpass=f=65", "equalizer=f=3200:t=q:w=1.3:g=-1",
                    "loudnorm=I=-17:TP=-2:LRA=8"]
    if abs(tempo - 1) > .005:
        while tempo > 2:
            filters.append("atempo=2")
            tempo /= 2
        while tempo < .5:
            filters.append("atempo=0.5")
            tempo *= 2
        filters.append(f"atempo={tempo:.6f}")
    filters += ["aresample=48000", f"apad=whole_dur={duration:.6f}", f"atrim=duration={duration:.6f}"]
    if normalize:
        filters += ["afade=t=in:d=0.015", f"afade=t=out:st={max(0, duration - .028):.6f}:d=0.028"]
    run(["ffmpeg", "-y", "-v", "error", "-xerror", "-i", str(audio), "-map", "0:a:0", "-af", ",".join(filters),
         "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(path)])
    validate_audio(path, expected=duration)


def render_scene(scene: dict[str, Any], raw: Path | list[Path], audio: Path, srt: Path, directory: Path,
                 *, duration: float | None = None, normalized_audio=False) -> Path:
    output = directory / f"rendered_{int(scene['scene_number']):02d}.mp4"
    escaped = str(srt.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    width, height = video_size(scene)
    source_duration = validate_audio(audio)
    duration = duration or math.ceil(source_duration * 30) / 30
    pcm = output.with_suffix(".wav")
    prepare_scene_audio(audio, pcm, duration, normalize=not normalized_audio)
    sources = raw if isinstance(raw, list) else [raw]
    command = ["ffmpeg", "-y", "-v", "error"]
    filters = []
    frames = round(duration * 30)
    for i, source in enumerate(sources):
        command += ["-stream_loop", "-1", "-i", str(source)]
        shot_frames = round((i + 1) * frames / len(sources)) - round(i * frames / len(sources))
        scaled_w, scaled_h = int(width * 1.06) // 2 * 2, int(height * 1.06) // 2 * 2
        filters.append(f"[{i}:v:0]scale={scaled_w}:{scaled_h}:force_original_aspect_ratio=increase,"
                       f"crop={width}:{height}:x='(iw-ow)*(0.5+0.45*sin(t/3+{i}))':y='(ih-oh)/2',"
                       f"setsar=1,fps=30,trim=end_frame={shot_frames},setpts=PTS-STARTPTS,format=yuv420p[v{i}]")
    labels = "".join(f"[v{i}]" for i in range(len(sources)))
    filters.append(labels + f"concat=n={len(sources)}:v=1:a=0[visuals]")
    font_size, margin_v = (64, 300) if height > width else (54, 100)
    filters.append("[visuals]subtitles='" + escaped + "':force_style='"
                   f"PlayResX={width},PlayResY={height},FontName=DejaVu Sans,FontSize={font_size},"
                   "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,"
                   f"Alignment=2,MarginV={margin_v},MarginL=80,MarginR=80'[vout]")
    command += ["-i", str(pcm), "-filter_complex_threads", "1", "-filter_complex", ";".join(filters),
                "-map", "[vout]", "-map", f"{len(sources)}:a:0", "-t", f"{duration:.6f}",
                *video_encoder(), "-c:a", "aac", "-b:a", "160k", str(output)]
    run(command, timeout=max(300, int(duration * 20)))
    validate_audio(output, expected=duration)
    return output


def stitch(rendered: list[Path], directory: Path) -> Path:
    if not rendered:
        raise ValueError("Cannot join an empty video")
    final = directory / "final_video.mp4"
    concat = directory / "concat.txt"
    narration = directory / "master.wav"
    lengths = []
    with wave.open(str(narration), "wb") as master:
        master.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
        for clip in rendered:
            with wave.open(str(clip.with_suffix(".wav")), "rb") as pcm:
                if (pcm.getnchannels(), pcm.getsampwidth(), pcm.getframerate()) != (2, 2, 48000):
                    raise ValueError("Scene narration does not match the master audio format")
                lengths.append(pcm.getnframes() / 48000)
                master.writeframes(pcm.readframes(pcm.getnframes()))
    concat.write_text("".join(f"file '{clip.resolve()}'\nduration {length:.6f}\n" for clip, length in zip(rendered, lengths)), encoding="utf-8")
    run(["ffmpeg", "-y", "-v", "error", "-xerror", "-f", "concat", "-safe", "0", "-i", str(concat),
         "-i", str(narration), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
         "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(final)], timeout=600)
    validate_audio(final, expected=sum(lengths))
    media = probe(final)
    video = next((track for track in media.get("streams", []) if track.get("codec_type") == "video"), {})
    if abs(float(video.get("duration", 0)) - sum(lengths)) > .12:
        raise ValueError("The final picture and narration durations do not match")
    return final


def youtube_credentials() -> Credentials:
    token_json = os.getenv("YOUTUBE_TOKEN_JSON", "").strip()
    if not credential_version():
        raise RuntimeError("invalid_youtube_credentials")
    credentials = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
    if not credentials.valid or credentials.expiry is None:
        credentials.refresh(GoogleAuthRequest())
    if not credentials.valid:
        raise RuntimeError("invalid_grant: YouTube authorization must be renewed")
    return credentials


def upload_youtube(video: dict[str, Any], file_path: Path) -> str:
    youtube = build("youtube", "v3", credentials=youtube_credentials())
    request = youtube.videos().insert(part="snippet,status", body={"snippet": {
        "title": str(video["title"])[:100], "description": str(video.get("description", "")),
        "tags": video.get("tags", []), "categoryId": "27"}, "status": {
        "privacyStatus": "private", "selfDeclaredMadeForKids": False}},
        media_body=MediaFileUpload(str(file_path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True))
    response = None
    failures = 0
    while response is None:
        try:
            _, response = request.next_chunk()
            failures = 0
        except Exception as error:
            status = getattr(getattr(error, "resp", None), "status", None)
            if failures >= 3 or (status is not None and status not in (429, 500, 502, 503, 504)):
                raise
            time.sleep(2 ** failures)
            failures += 1
    return response["id"]


def set_youtube_thumbnail(video_id: str, image_path: Path) -> None:
    """Apply the custom JPEG to the uploaded YouTube video if the channel permits it."""
    youtube = build("youtube", "v3", credentials=youtube_credentials())
    youtube.thumbnails().set(
        videoId=video_id,
        media_body=MediaFileUpload(str(image_path), mimetype="image/jpeg", resumable=False),
    ).execute()


def decode(value: Any) -> dict[str, Any]:
    if isinstance(value, dict): return value
    if isinstance(value, str):
        try:
            item = json.loads(value)
            return item if isinstance(item, dict) else {}
        except ValueError: return {}
    return {}


def video_details(row: dict[str, Any]) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    script = decode(row.get("script"))
    scenes = row.get("scenes")
    if isinstance(scenes, str):
        try: scenes = json.loads(scenes)
        except ValueError: scenes = None
    metadata = decode(row.get("metadata"))
    if isinstance(scenes, dict):
        metadata.update(scenes)
        scenes = scenes.get("scenes")
    if not isinstance(scenes, list):
        scenes = script.get("scenes", metadata.get("scenes", []))
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("This video has no scenes; submit it from Scene Editor.")
    first_metadata = decode(scenes[0].get("_studio")) if isinstance(scenes, list) and scenes and isinstance(scenes[0], dict) else {}
    merged = {**first_metadata, **metadata, **script, **{k: v for k, v in row.items() if v is not None and v != ""}}
    ratio = merged.get("aspect_ratio") or merged.get("format") or "16:9"
    ratio = "9:16" if ratio == "9:16" else "16:9"
    result = []
    for i, raw in enumerate(scenes):
        item = raw if isinstance(raw, dict) else {"narration": str(raw)}
        narration = str(item.get("narration") or item.get("voiceover") or item.get("text") or "").strip()
        if not narration: raise ValueError(f"Scene {i + 1} is missing narration.")
        queries = item.get("search_queries", [])
        if isinstance(queries, str):
            queries = queries.split(",")
        queries = [str(q).strip() for q in queries if str(q).strip()] if isinstance(queries, list) else []
        chunks, current = [], []
        for sentence in re.split(r"(?<=[.!?])\s+", narration):
            words = sentence.split()
            while len(words) > 40:
                if current:
                    chunks.append(" ".join(current)); current = []
                chunks.append(" ".join(words[:35])); words = words[35:]
            if len(current) + len(words) > 40 and current:
                chunks.append(" ".join(current)); current = []
            current.extend(words)
        if current:
            chunks.append(" ".join(current))
        for chunk in chunks:
            result.append({"scene_number": len(result) + 1, "narration": chunk,
                           "search_queries": queries, "format": ratio, "logical_scene": i + 1})
    for item in result:
        item["visual_context"] = (str(merged.get("title", "")) + " " + item["narration"])[:1200]
    return result, ratio, merged


def save_fields(client, row: dict[str, Any], **changes: Any) -> None:
    """Update existing columns and embed optional fields in available JSON."""
    if "progress_note" in changes:
        changes["worker_heartbeat_at"] = datetime.now(timezone.utc).isoformat()
    known = set(row.keys())
    payload = {k: v for k, v in changes.items() if k in known}
    extras = {k: v for k, v in changes.items() if k not in known}
    if extras and "metadata" in known:
        payload["metadata"] = {**decode(row.get("metadata")), **extras}
    elif extras and "script" in known:
        existing = {**decode(row.get("script")), **extras}
        payload["script"] = json.dumps(existing) if isinstance(row.get("script"), str) else existing
    elif extras and "scenes" in known:
        scenes = row.get("scenes")
        if isinstance(scenes, list) and scenes and isinstance(scenes[0], dict):
            scenes = [dict(x) for x in scenes]
            scenes[0]["_studio"] = {**decode(scenes[0].get("_studio")), **extras}
            payload["scenes"] = scenes
        elif isinstance(scenes, dict):
            payload["scenes"] = {**scenes, **extras}
    if not payload: raise RuntimeError("videos table has no supported status column.")
    for attempt in range(3):
        try:
            answer = client.table("videos").update(payload).eq("id", row["id"]).execute()
            if not answer.data:
                raise RuntimeError("Could not persist video status.")
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    row.update(payload)


def storage_preview(path: Path) -> Path:
    """Create a playable preview under the Supabase Free 50 MB single-file limit.

    The 1080p master stays on disk for optional YouTube upload and Actions artifact.
    """
    maximum = 48_000_000
    if path.stat().st_size < maximum:
        return path
    seconds = ffprobe_duration(path)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True)
    width, height = [int(v) for v in probe.stdout.strip().split(",")[:2]]
    portrait = height > width
    # Long-form: favor a crisp and small 480p preview over a 1080p upload rejection.
    target_width, target_height = (480, 854) if portrait else (854, 480)
    if seconds < 300:
        target_width, target_height = ((720, 1280) if portrait else (1280, 720))
    preview = path.with_name("stream_preview.mp4")
    nominal_kbps = int((45_000_000 * 8 / seconds / 1000 - 160) * 0.84)
    for shrink in (1.0, 0.70, 0.50):
        bitrate = max(120, min(3200, int(nominal_kbps * shrink)))
        run(["ffmpeg", "-y", "-i", str(path),
             "-vf", f"scale={target_width}:{target_height}:flags=lanczos",
             "-c:v", "libx264", "-preset", "veryfast",
             "-b:v", f"{bitrate}k", "-maxrate", f"{bitrate}k",
             "-bufsize", f"{bitrate * 2}k", "-pix_fmt", "yuv420p",
             "-c:a", "copy",
             "-movflags", "+faststart", str(preview)], timeout=1200)
        if preview.stat().st_size < maximum:
            print(f"[storage] preview {preview.stat().st_size:,} bytes for {seconds:.1f}s", flush=True)
            return preview
    raise RuntimeError("Preview could not fit under the 50 MB Supabase Free file limit. The master MP4 is saved as a GitHub Actions artifact.")


def upload_mp4(client, path: Path, video_id: str) -> str:
    bucket = os.getenv("SUPABASE_VIDEO_BUCKET", "renders")
    try:
        client.storage.get_bucket(bucket)
    except Exception as err:
        if "404" in str(err) or "not found" in str(err).lower():
            client.storage.create_bucket(bucket, options={"public": True})
        else: raise
    filename = f"{video_id}.mp4"
    preview = storage_preview(path)
    validate_audio(preview, expected=ffprobe_duration(path))
    client.storage.from_(bucket).upload(filename, preview.read_bytes(),
        file_options={"content-type": "video/mp4", "upsert": "true"})
    return client.storage.from_(bucket).get_public_url(filename)


def upload_thumbnail(client, path: Path, video_id: str) -> str:
    bucket = os.getenv("SUPABASE_THUMBNAIL_BUCKET", "thumbnails")
    try:
        client.storage.get_bucket(bucket)
    except Exception as error:
        if "404" in str(error) or "not found" in str(error).lower():
            client.storage.create_bucket(bucket, options={"public": True})
        else:
            raise
    name = f"{video_id}.jpg"
    client.storage.from_(bucket).upload(name, path.read_bytes(),
        file_options={"content-type": "image/jpeg", "upsert": "true"})
    return client.storage.from_(bucket).get_public_url(name)


def process(video: dict[str, Any], supabase) -> None:
    video_id = str(video["id"])
    directory = ROOT / video_id
    directory.mkdir(parents=True, exist_ok=True)
    scenes, ratio, details = video_details(video)
    revision = details.get("revision") or {}
    target_scene = revision.get("scene_number")
    mode = revision.get("mode")
    parent, original_scenes, used_footage = prepare_revision(supabase, video, details, directory)
    recent_footage = footage_history(supabase, video_id)
    catalog = {}
    rendered = []
    timeline = []
    positions = {}
    cursor = 0.0
    for scene in scenes:
        number = scene["scene_number"]
        logical = scene["logical_scene"]
        chunk = positions.get(logical, 0)
        positions[logical] = chunk + 1
        changed = logical == target_scene
        entries = original_scenes.get(logical, [])
        entry = entries[chunk] if chunk < len(entries) else None
        if parent and not entry and (not changed or mode in ("audio", "visual")):
            raise ValueError(f"Saved edit points no longer match scene {logical}")
        save_fields(supabase, video, progress_note=f"Preparing scene {number} of {len(scenes)}")
        if parent and entry and (not changed or mode == "audio"):
            replacement = make_audio(scene, directory)[0] if changed else None
            clip = reuse_visual(parent, entry, scene, directory, replacement)
            captions = entry.get("captions", "")
            clip_ids = entry.get("footage", [])
        else:
            keep_audio = bool(parent and entry and changed and mode == "visual")
            if keep_audio:
                audio = directory / f"source_audio_{number:02d}.wav"
                srt = audio.with_suffix(".srt")
                extract_audio(parent, entry, audio)
                srt.write_text(entry["captions"], encoding="utf-8")
            else:
                audio, srt = make_audio(scene, directory)
            seconds = ffprobe_duration(audio)
            count = max(1, math.ceil(seconds / SHOT_SECONDS))
            raw, clip_ids = [], []
            for shot in range(count):
                queries = scene["search_queries"]
                shifted = queries[shot % len(queries):] + queries[:shot % len(queries)] if queries else []
                match = fetch_broll({**scene, "search_queries": shifted}, directory, used_footage, recent_footage, catalog, shot)
                if not match:
                    break
                source, clip_id = match
                raw.append(source)
                clip_ids.append(clip_id)
            if not raw:
                raise RuntimeError(f"No suitable footage for scene {logical}. Edit its footage search terms and try again.")
            clip = render_scene(scene, raw, audio, srt, directory,
                                duration=float(entry["duration"]) if keep_audio else None,
                                normalized_audio=keep_audio)
            captions = srt.read_text(encoding="utf-8")
            for source in raw:
                if source.name != "raw_01_00.mp4":
                    source.unlink(missing_ok=True)
        with wave.open(str(clip.with_suffix(".wav")), "rb") as pcm:
            duration = pcm.getnframes() / pcm.getframerate()
        timeline.append({"scene_number": logical, "start": round(cursor, 6), "duration": round(duration, 6),
                         "captions": captions, "footage": clip_ids})
        cursor += duration
        rendered.append(clip)
    save_fields(supabase, video, progress_note="Joining scenes")
    final = stitch(rendered, directory)
    headline = str(details.get("thumbnail_title") or details.get("title") or "New story")
    thumbnail_path = directory / "thumbnail.jpg"
    thumbnail_source = directory / "raw_01_00.mp4"
    if parent and (target_scene != 1 or mode == "audio"):
        try:
            thumbnail_path.write_bytes(supabase.storage.from_(
                os.getenv("SUPABASE_THUMBNAIL_BUCKET", "thumbnails")).download(revision["parent_id"] + ".jpg"))
        except Exception:
            create_thumbnail(rendered[0], headline, thumbnail_path)
    else:
        create_thumbnail(thumbnail_source, headline, thumbnail_path)
    thumbnail_url, thumbnail_error = "", ""
    try:
        thumbnail_url = upload_thumbnail(supabase, thumbnail_path, video_id)
    except Exception as error:
        thumbnail_error = f"Thumbnail storage: {str(error)[:180]}"
        print(f"[thumbnail] {thumbnail_error}", flush=True)

    save_fields(supabase, video, progress_note="Saving MP4 preview")
    storage_url, storage_error = "", ""
    try:
        storage_url = upload_mp4(supabase, final, video_id)
    except Exception as error:
        storage_error = f"MP4 preview storage: {str(error)[:250]}"
        print(f"[storage] {storage_error}", flush=True)

    if not storage_url:
        raise RuntimeError("MP4 could not be saved: " + storage_error)
    warnings = [x for x in (thumbnail_error, storage_error) if x]
    save_fields(supabase, video, status="completed", video_url=storage_url, storage_url=storage_url,
                thumbnail_url=thumbnail_url or None, actual_duration_seconds=ffprobe_duration(final),
                completed_at=datetime.now(timezone.utc).isoformat(), render_version="studio-v6", scene_timeline=timeline,
                publish_status="awaiting_review", publish_attempts=0, render_attempts=0,
                publish_requested=False, publish_approved_at=None, publish_approved_by=None,
                progress_note="MP4 ready for review. " + " ".join(warnings), error_message=None)
    print(f"[completed] {video_id}", flush=True)


def safe_publish_error(error: Exception) -> tuple[str, str]:
    text = str(error)
    if "invalid_youtube_credentials" in text or "invalid_client" in text or "unauthorized_client" in text:
        return "auth_required", "YouTube credentials are incomplete or invalid. Update YOUTUBE_TOKEN_JSON in GitHub Actions."
    if "invalid_grant" in text or "expired or revoked" in text:
        return "auth_required", "YouTube authorization expired or was revoked. Reconnect YouTube and update YOUTUBE_TOKEN_JSON in GitHub Actions."
    if "quotaExceeded" in text or "uploadLimitExceeded" in text:
        return "retry", "YouTube upload quota reached; the saved MP4 will be retried later."
    if "insufficientPermissions" in text:
        return "auth_required", "YouTube upload permission is missing. Reconnect with the youtube.upload scope."
    return "retry", "YouTube upload temporarily unavailable (" + type(error).__name__ + "). The MP4 remains available."


def publish_saved(video: dict[str, Any], supabase, master: Path | None = None, thumbnail: Path | None = None) -> None:
    details = stored_metadata(video)
    if (not has_approval(details) or details.get("youtube_url")
            or details.get("youtube_id") or details.get("youtube_video_id")):
        return
    directory = ROOT / str(video["id"])
    directory.mkdir(parents=True, exist_ok=True)
    version = credential_version()
    new_auth = details.get("publish_status") == "auth_required" and details.get("publish_auth_version") != version
    attempts = 1 if new_auth else int(details.get("publish_attempts") or 0) + 1
    save_fields(supabase, video, publish_status="uploading", publish_attempts=attempts,
                publish_requested=False, publish_auth_version=version,
                publish_error=None, progress_note="MP4 ready. Uploading to YouTube.")
    try:
        if master is None:
            # Fetch only this job's trusted storage object, never a row-supplied URL.
            master = directory / "publish_preview.mp4"
            content = supabase.storage.from_(os.getenv("SUPABASE_VIDEO_BUCKET", "renders")).download(str(video["id"]) + ".mp4")
            master.write_bytes(content)
        validate_audio(master)
        youtube_id = upload_youtube(details, master)
    except Exception as error:
        state, message = safe_publish_error(error)
        save_fields(supabase, video, publish_status=state, publish_error=message,
                    publish_retry_at=datetime.fromtimestamp(time.time() + min(86400, 900 * 2 ** min(attempts, 6)), timezone.utc).isoformat(),
                    progress_note="MP4 ready. " + message)
        print("[youtube]", message, flush=True)
        return
    # A failed database write after YouTube accepts a video must never trigger a duplicate upload.
    save_fields(supabase, video, youtube_id=youtube_id,
                youtube_url="https://www.youtube.com/watch?v=" + youtube_id,
                publish_status="uploaded", published_at=datetime.now(timezone.utc).isoformat(),
                publish_error=None, progress_note="MP4 ready. Uploaded to YouTube privately.")
    print("[youtube] Private upload confirmed:", video["id"], flush=True)
    try:
        if thumbnail is None:
            thumbnail = directory / "thumbnail.jpg"
            thumbnail.write_bytes(supabase.storage.from_(os.getenv("SUPABASE_THUMBNAIL_BUCKET", "thumbnails")).download(str(video["id"]) + ".jpg"))
        set_youtube_thumbnail(youtube_id, thumbnail)
        save_fields(supabase, video, thumbnail_publish_status="uploaded")
    except Exception as error:
        save_fields(supabase, video, thumbnail_publish_status="failed",
                    progress_note="MP4 ready. Uploaded to YouTube privately; custom thumbnail could not be applied (" + type(error).__name__ + ").")


def publish_due(client, limit: int = 3) -> None:
    version = credential_version()
    if not version:
        return
    rows = client.table("videos").select("*").eq("status", "completed").order("created_at", desc=True).limit(100).execute().data or []
    rows.sort(key=lambda row: not stored_metadata(row).get("publish_requested"))
    now = datetime.now(timezone.utc)
    done = 0
    for row in rows:
        if not publication_due(row, version, now):
            continue
        if stored_metadata(row).get("publish_status") == "uploading":
            message = "The upload was interrupted. Check YouTube Studio for a private copy before retrying."
            save_fields(client, row, publish_status="upload_uncertain", publish_error=message, progress_note=message)
            continue
        publish_saved(row, client)
        done += 1
        if done >= limit:
            break


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--drain", action="store_true")
    parser.add_argument("--publish-only", action="store_true")
    parser.add_argument("--max-jobs", type=int, default=3)
    parser.add_argument("--max-uploads", type=int, default=8)
    args = parser.parse_args(argv)
    url = os.getenv("SUPABASE_URL") or require_env("NEXT_PUBLIC_SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or require_env("SUPABASE_KEY")
    supabase = create_client(url, key)
    ROOT.mkdir(parents=True, exist_ok=True)
    publish_due(supabase, max(1, min(12, args.max_uploads)))
    if args.publish_only:
        return
    processed, failures = 0, 0
    while True:
        result = supabase.table("videos").select("*").eq("status", "pending").order("created_at").limit(1).execute()
        video = None
        for candidate in result.data or []:
            changed = supabase.table("videos").update({"status": "rendering"}).eq("id", candidate["id"]).eq("status", "pending").execute()
            if changed.data:
                video = changed.data[0]
                save_fields(supabase, video, render_started_at=datetime.now(timezone.utc).isoformat(), progress_note="Starting narration and footage preparation")
                break
        if not video:
            # All production workflows share one concurrency group. A stale heartbeat
            # recovers a killed runner, rather than judging a newly-started old draft by creation time.
            stale = supabase.table("videos").select("*").eq("status", "rendering").order("created_at").limit(20).execute()
            for candidate in stale.data or []:
                data = stored_metadata(candidate)
                try:
                    started = datetime.fromisoformat(str(data.get("worker_heartbeat_at") or data.get("render_started_at") or candidate["created_at"]).replace("Z", "+00:00"))
                    if started.tzinfo is None:
                        started = started.replace(tzinfo=timezone.utc)
                    if (datetime.now(timezone.utc) - started).total_seconds() > 7200:
                        if int(data.get("render_attempts") or 0) >= 3:
                            save_fields(supabase, candidate, status="failed", error_message="Render interrupted repeatedly. Retry after checking the worker logs.")
                            continue
                        video = candidate
                        break
                except (KeyError, TypeError, ValueError):
                    continue
        if video:
            attempts = int(stored_metadata(video).get("render_attempts") or 0) + 1
            save_fields(supabase, video, render_attempts=attempts)
            try:
                process(video, supabase)
            except Exception as error:
                failures += 1
                print(f"[failed] {video.get('id')}: {type(error).__name__}: {str(error)[:300]}", flush=True)
                # Once the MP4 is saved, upload or thumbnail failures cannot requeue the render.
                if video.get("status") == "completed":
                    break
                save_fields(supabase, video, status="pending" if attempts < 3 else "failed",
                            error_message=str(error)[:700],
                            progress_note="Automatic render retry scheduled" if attempts < 3 else "Rendering failed; open the scenes and retry")
                break
            processed += 1
        elif args.once or args.drain:
            break
        else:
            time.sleep(POLL_SECONDS)
        if args.once or (args.drain and processed >= args.max_jobs):
            break
    if failures:
        raise SystemExit(1)  # CI must not report a failed render as green.


if __name__ == "__main__":
    main()
