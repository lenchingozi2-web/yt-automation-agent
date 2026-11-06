"""LenxiQ Studio background renderer.

Run locally with `python worker.py --once` or continuously with `python worker.py`.
The process is intentionally outside Vercel: it needs FFmpeg, local disk, and CPU audio models.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime, timezone
import os
import subprocess
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
from studio_audio import make_audio, validate_audio
from studio_maintenance import metadata as stored_metadata

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


async def fetch_pixabay(scene: dict[str, Any], target: Path, used: set[str] | None = None) -> bool:
    """Download a correctly oriented HD variant without ever preferring 4K bloat."""
    key = os.getenv("PIXABAY_API_KEY", "").strip()
    if not key:
        return False
    used = used if used is not None else set()
    portrait = str(scene.get("format", "16:9")) == "9:16"
    queries = list(scene.get("search_queries", []))
    context = str(scene.get("visual_context", scene.get("narration", ""))).lower()
    medical = bool(re.search(r"\b(medical|health|hospital|doctor|patient|clinician|heart|wearable)\b", context))
    if medical:
        queries = [q for q in queries if not re.search(r"nature|landscape|river|subscribe|green.?screen", str(q), re.I)]
        queries += ["doctor patient", "hospital computer", "medical laboratory"]
    if not queries:
        queries = ["technology"]
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        for query in queries:
            if not str(query).strip():
                continue
            response = await client.get("https://pixabay.com/api/videos/", params={
                "key": key, "q": str(query).strip(), "video_type": "all",
                "safesearch": "true", "per_page": 20,
            })
            if response.status_code != 200:
                continue
            preferred = []
            fallback = []
            for hit in response.json().get("hits", []):
                tags = str(hit.get("tags", "")).lower()
                if re.search(r"green.?screen|chroma|subscribe|like button|countdown|logo reveal|intro|outro", tags):
                    continue
                if medical and not re.search(r"medic|doctor|patient|health|hospital|laborator|wearable|watch|fitness|heart|nurse|science|data|computer|technolog|artificial|brain|\bai\b", tags):
                    continue
                if str(hit.get("id")) in used:
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
                        preferred.append((width * height, {**variant, "clip_id": str(hit.get("id"))}))
                    elif not portrait and width >= height and width >= 1280 and height >= 720:
                        preferred.append((width * height, {**variant, "clip_id": str(hit.get("id"))}))
                    elif portrait and width >= height and width >= 1280 and height >= 720:
                        fallback.append((width * height, {**variant, "clip_id": str(hit.get("id"))}))
            eligible = sorted(preferred, key=lambda item: item[0])
            if portrait and not eligible:
                eligible = sorted(fallback, key=lambda item: item[0])
            # Try smallest adequate variants; cap response size to avoid 4K memory spikes.
            for _, variant in eligible[:4]:
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
                        used.add(variant["clip_id"])
                        return True
                except (OSError, ValueError, httpx.HTTPError, subprocess.SubprocessError) as error:
                    print(f"[b-roll] skipping unusable Pixabay clip: {str(error)[:180]}")
                finally:
                    if target.exists() and target.stat().st_size <= 10000:
                        target.unlink(missing_ok=True)
    return False


def fetch_broll(scene: dict[str, Any], directory: Path, used: set[str] | None = None, shot: int = 0) -> Path:
    target = directory / f"raw_{int(scene['scene_number']):02d}_{shot:02d}.mp4"
    if target.exists() and target.stat().st_size > 0 and usable_footage(target):
        return target
    if not asyncio.run(fetch_pixabay(scene, target, used)):
        raise RuntimeError(f"No usable Pixabay footage for scene {scene['scene_number']}; retry or edit its search keywords")
    return target


def render_scene(scene: dict[str, Any], raw: Path | list[Path], audio: Path, srt: Path, directory: Path) -> Path:
    output = directory / f"rendered_{int(scene['scene_number']):02d}.mp4"
    output.unlink(missing_ok=True)  # Never reuse a partial or outdated render.
    escaped = str(srt.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    portrait = str(scene.get("format", "16:9")) == "9:16"
    width, height = (1080, 1920) if portrait else (1920, 1080)
    duration = validate_audio(audio)
    sources = raw if isinstance(raw, list) else [raw]
    command = ["ffmpeg", "-y"]
    filters = []
    shot_duration = duration / len(sources)
    for i, source in enumerate(sources):
        # Continuous camera movement and new footage every few seconds.
        command += ["-stream_loop", "-1", "-i", str(source)]
        scaled_w, scaled_h = int(width * 1.06) // 2 * 2, int(height * 1.06) // 2 * 2
        filters.append(f"[{i}:v:0]scale={scaled_w}:{scaled_h}:force_original_aspect_ratio=increase,"
                       f"crop={width}:{height}:x='(iw-ow)*(0.5+0.45*sin(t/3+{i}))':y='(ih-oh)/2',"
                       f"setsar=1,fps=30,trim=duration={shot_duration:.6f},setpts=PTS-STARTPTS,format=yuv420p[v{i}]")
    labels = "".join(f"[v{i}]" for i in range(len(sources)))
    filters.append(labels + f"concat=n={len(sources)}:v=1:a=0[visuals]")
    font_size, margin_v = (64, 300) if portrait else (54, 100)
    filters.append("[visuals]subtitles='" + escaped + "':force_style='"
                   f"PlayResX={width},PlayResY={height},FontName=DejaVu Sans,FontSize={font_size},"
                   "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,"
                   f"Alignment=2,MarginV={margin_v},MarginL=80,MarginR=80'[vout]")
    command += ["-i", str(audio), "-filter_complex_threads", "1", "-filter_complex", ";".join(filters),
                "-map", "[vout]", "-map", f"{len(sources)}:a:0", "-t", str(duration),
                "-af", "loudnorm=I=-16:TP=-1.5:LRA=9,aresample=48000", "-ar", "48000", "-ac", "2",
                "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(output)]
    run(command, timeout=max(300, int(duration * 20)))
    validate_audio(output, expected=duration)
    return output


def stitch(rendered: list[Path], directory: Path) -> Path:
    if not rendered:
        raise ValueError("Cannot join an empty video")
    final = directory / "final_video.mp4"
    final.unlink(missing_ok=True)
    concat = directory / "concat.txt"
    concat.write_text("\n".join(f"file '{p.resolve()}'" for p in rendered), encoding="utf-8")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
         "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy", "-c:a", "aac", "-ar", "48000", "-ac", "2",
         "-af", "aresample=async=1:first_pts=0", "-b:a", "160k", "-movflags", "+faststart", str(final)], timeout=600)
    validate_audio(final, expected=sum(ffprobe_duration(p) for p in rendered))
    return final


def youtube_credentials() -> Credentials:
    token_json = os.getenv("YOUTUBE_TOKEN_JSON", "").strip()
    if token_json:
        credentials = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
        if credentials.refresh_token and (not credentials.valid or credentials.expiry is None or credentials.expired):
            credentials.refresh(GoogleAuthRequest())
        if not credentials.valid:
            raise RuntimeError("invalid_grant: YouTube authorization must be renewed")
        return credentials
    raise RuntimeError("YouTube is not connected. Reconnect and update the YOUTUBE_TOKEN_JSON repository secret.")


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
    import re
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
                           "search_queries": queries, "format": ratio})
    context = str(merged.get("title", "")) + " " + " ".join(x["narration"] for x in result)
    for item in result:
        item["visual_context"] = context[:12000]
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
    nominal_kbps = int((45_000_000 * 8 / seconds / 1000 - 64) * 0.84)
    for shrink in (1.0, 0.70, 0.50):
        bitrate = max(120, min(3200, int(nominal_kbps * shrink)))
        run(["ffmpeg", "-y", "-i", str(path),
             "-vf", f"scale={target_width}:{target_height}:flags=lanczos",
             "-c:v", "libx264", "-preset", "veryfast",
             "-b:v", f"{bitrate}k", "-maxrate", f"{bitrate}k",
             "-bufsize", f"{bitrate * 2}k", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "64k", "-ac", "2",
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
    rendered: list[Path] = []
    used_footage: set[str] = set()
    import math
    for scene in scenes:
        save_fields(supabase, video, progress_note=f"Rendering scene {scene['scene_number']} of {len(scenes)}")
        audio, srt = make_audio(scene, directory)
        seconds = ffprobe_duration(audio)
        count = max(1, math.ceil(seconds / SHOT_SECONDS))
        raw = []
        for shot in range(count):
            # Rotate relevant search phrases across shots, avoiding repeated clip IDs.
            queries = scene["search_queries"]
            shifted = queries[shot % len(queries):] + queries[:shot % len(queries)] if queries else []
            raw.append(fetch_broll({**scene, "search_queries": shifted}, directory, used_footage, shot))
        rendered.append(render_scene(scene, raw, audio, srt, directory))
        # Large productions must not accumulate hundreds of stock downloads.
        for source in raw:
            if source.name != "raw_01_00.mp4":
                source.unlink(missing_ok=True)
    save_fields(supabase, video, progress_note="Joining scenes")
    final = stitch(rendered, directory)
    # Render the thumbnail from the same Pixabay B-roll rather than paying for another image API.
    headline = str(details.get("thumbnail_title") or details.get("title") or "New story")
    thumbnail_path = directory / "thumbnail.jpg"
    create_thumbnail(directory / "raw_01_00.mp4", headline, thumbnail_path)
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
                completed_at=datetime.now(timezone.utc).isoformat(), render_version="studio-v5",
                publish_status="pending", publish_attempts=0, render_attempts=0,
                progress_note="MP4 ready. " + " ".join(warnings), error_message=None)
    if os.getenv("YOUTUBE_TOKEN_JSON", "").strip():
        publish_saved(video, supabase, master=final, thumbnail=thumbnail_path)
    else:
        save_fields(supabase, video, publish_status="not_configured",
                    progress_note="MP4 ready. YouTube is not connected.")
    print(f"[completed] {video_id}", flush=True)


def safe_publish_error(error: Exception) -> tuple[str, str]:
    text = str(error)
    if "invalid_grant" in text or "expired or revoked" in text:
        return "auth_required", "YouTube authorization expired or was revoked. Reconnect YouTube and update YOUTUBE_TOKEN_JSON in GitHub repository secrets, then retry upload."
    if "quotaExceeded" in text or "uploadLimitExceeded" in text:
        return "retry", "YouTube upload quota reached; the saved MP4 will be retried later."
    if "insufficientPermissions" in text:
        return "auth_required", "YouTube upload permission is missing. Reconnect with the youtube.upload scope."
    return "retry", "YouTube upload temporarily unavailable (" + type(error).__name__ + "). The MP4 remains available."


def publish_saved(video: dict[str, Any], supabase, master: Path | None = None, thumbnail: Path | None = None) -> None:
    details = stored_metadata(video)
    if details.get("youtube_url") or details.get("youtube_id"):
        return
    directory = ROOT / str(video["id"])
    directory.mkdir(parents=True, exist_ok=True)
    attempts = int(details.get("publish_attempts") or 0) + 1
    save_fields(supabase, video, publish_status="uploading", publish_attempts=attempts,
                publish_requested=False, progress_note="MP4 ready. Uploading to YouTube.")
    try:
        if master is None:
            # Fetch only this job's trusted storage object, never a row-supplied URL.
            master = directory / "publish_preview.mp4"
            content = supabase.storage.from_(os.getenv("SUPABASE_VIDEO_BUCKET", "renders")).download(str(video["id"]) + ".mp4")
            master.write_bytes(content)
        validate_audio(master)
        youtube_id = upload_youtube(details, master)
        # Save the ID immediately so a thumbnail or later status failure cannot duplicate uploads.
        save_fields(supabase, video, youtube_id=youtube_id,
                    youtube_url="https://www.youtube.com/watch?v=" + youtube_id,
                    publish_status="uploaded", published_at=datetime.now(timezone.utc).isoformat(),
                    publish_error=None, progress_note="MP4 ready. Uploaded to YouTube privately.")
    except Exception as error:
        state, message = safe_publish_error(error)
        save_fields(supabase, video, publish_status=state, publish_error=message,
                    publish_retry_at=datetime.fromtimestamp(time.time() + min(86400, 900 * 2 ** min(attempts, 6)), timezone.utc).isoformat(),
                    progress_note="MP4 ready. " + message)
        print("[youtube]", message, flush=True)
        return
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
    if not os.getenv("YOUTUBE_TOKEN_JSON", "").strip():
        return
    rows = client.table("videos").select("*").eq("status", "completed").order("created_at", desc=True).limit(100).execute().data or []
    done = 0
    for row in rows:
        data = stored_metadata(row)
        if data.get("youtube_url") or data.get("youtube_id") or not (data.get("video_url") or data.get("storage_url")):
            continue
        explicit = bool(data.get("publish_requested"))
        if not explicit and (data.get("publish_status") == "auth_required" or int(data.get("publish_attempts") or 0) >= 5):
            continue
        due = data.get("publish_retry_at")
        if due and not explicit and datetime.fromisoformat(due.replace("Z", "+00:00")) > datetime.now(timezone.utc):
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
    args = parser.parse_args(argv)
    url = os.getenv("SUPABASE_URL") or require_env("NEXT_PUBLIC_SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or require_env("SUPABASE_KEY")
    supabase = create_client(url, key)
    ROOT.mkdir(parents=True, exist_ok=True)
    publish_due(supabase, args.max_jobs)
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
                # Retry transient failures on the next scheduled run, with a hard limit.
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
