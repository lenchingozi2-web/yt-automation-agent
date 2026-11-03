"""LenxiQ Studio background renderer.

Run locally with `python worker.py --once` or continuously with `python worker.py`.
The process is intentionally outside Vercel: it needs FFmpeg, local disk, and CPU audio models.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from supabase import create_client
from studio_media import create_thumbnail

load_dotenv()
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
ROOT = Path(os.getenv("WORKER_OUTPUT_DIR", "output_run"))
VOICE = os.getenv("EDGE_TTS_VOICE", "en-US-ChristopherNeural")
POLL_SECONDS = int(os.getenv("WORKER_POLL_SECONDS", "20"))
MAX_SCENE_SECONDS = int(os.getenv("MAX_SCENE_SECONDS", "30"))


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


def write_srt(words: list[tuple[float, float, str]], path: Path) -> None:
    def stamp(seconds: float) -> str:
        millis = int(max(0, seconds) * 1000)
        return f"{millis // 3600000:02d}:{(millis // 60000) % 60:02d}:{(millis // 1000) % 60:02d},{millis % 1000:03d}"
    lines: list[str] = []
    for index, (start, end, word) in enumerate(words, 1):
        lines.extend([str(index), f"{stamp(start)} --> {stamp(max(end, start + 0.08))}", word.strip(), ""])
    path.write_text("\n".join(lines), encoding="utf-8")


async def edge_tts_scene(text: str, audio: Path, srt: Path) -> None:
    import edge_tts
    communicate = edge_tts.Communicate(text, VOICE)
    submaker = edge_tts.SubMaker()
    with audio.open("wb") as handle:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio": handle.write(chunk["data"])
            elif chunk["type"] == "WordBoundary": submaker.feed(chunk)
    srt.write_text(submaker.get_srt(), encoding="utf-8")


def kokoro_scene(text: str, audio: Path, srt: Path) -> None:
    """Small fallback when Edge TTS is unavailable."""
    from gtts import gTTS
    gTTS(text=text, lang="en").save(str(audio))
    seconds = ffprobe_duration(audio)
    words = text.split()
    groups = [" ".join(words[i:i + 10]) for i in range(0, len(words), 10)] or [text]
    entries = [(i * seconds / len(groups), (i + 1) * seconds / len(groups), group)
               for i, group in enumerate(groups)]
    write_srt(entries, srt)


def make_audio(scene: dict[str, Any], directory: Path) -> tuple[Path, Path]:
    number = int(scene["scene_number"])
    audio, srt = directory / f"scene_{number:02d}.mp3", directory / f"scene_{number:02d}.srt"
    if audio.exists() and srt.exists() and audio.stat().st_size > 0: return audio, srt
    text = str(scene["narration"]).strip()
    try:
        asyncio.run(asyncio.wait_for(edge_tts_scene(text, audio, srt), timeout=120))
    except Exception as error:
        print(f"[audio] edge-tts failed for scene {number}: {error}; using gTTS fallback")
        kokoro_scene(text, audio, srt)
    # Edge TTS WordBoundary events are not guaranteed. Always write a valid,
    # deterministic SRT synchronized to measured audio length so libass works.
    actual_seconds = ffprobe_duration(audio)
    tokens = text.split()
    phrases = [" ".join(tokens[i:i + 9]) for i in range(0, len(tokens), 9)] or [text]
    captions = [(i * actual_seconds / len(phrases),
                 (i + 1) * actual_seconds / len(phrases),
                 phrase) for i, phrase in enumerate(phrases)]
    write_srt(captions, srt)
    if not srt.is_file() or srt.stat().st_size < 35:
        raise RuntimeError("Failed to generate usable subtitles for scene " + str(number))
    return audio, srt


async def fetch_pixabay(scene: dict[str, Any], target: Path) -> bool:
    """Download a correctly oriented HD variant without ever preferring 4K bloat."""
    key = os.getenv("PIXABAY_API_KEY", "").strip()
    if not key:
        return False
    portrait = str(scene.get("format", "16:9")) == "9:16"
    queries = list(scene.get("search_queries", [])) + ["nature", "technology", "cinematic background"]
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
                        preferred.append((width * height, variant))
                    elif not portrait and width >= height and width >= 1280 and height >= 720:
                        preferred.append((width * height, variant))
                    elif portrait and width >= height and width >= 1280 and height >= 720:
                        fallback.append((width * height, variant))
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
                    if count > 10000 and ffprobe_duration(target) > 0.5:
                        return True
                except (OSError, ValueError, httpx.HTTPError, subprocess.SubprocessError) as error:
                    print(f"[b-roll] skipping unusable Pixabay clip: {str(error)[:180]}")
                finally:
                    if target.exists() and target.stat().st_size <= 10000:
                        target.unlink(missing_ok=True)
    return False


def dark_canvas(target: Path, format: str = "16:9") -> None:
    size = "1080x1920" if format == "9:16" else "1920x1080"
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=0x101827:s={size}:r=30", "-t", "2", "-vf", "noise=alls=3:allf=t", "-pix_fmt", "yuv420p", "-c:v", "libx264", str(target)])


def fetch_broll(scene: dict[str, Any], directory: Path) -> Path:
    target = directory / f"raw_{int(scene['scene_number']):02d}.mp4"
    if target.exists() and target.stat().st_size > 0: return target
    try:
        if asyncio.run(fetch_pixabay(scene, target)): return target
    except Exception as error: print(f"[b-roll] Pixabay failed: {error}")
    print(f"[b-roll] using dark motion canvas for scene {scene['scene_number']}")
    dark_canvas(target, str(scene.get("format", "16:9")))
    return target


def render_scene(scene: dict[str, Any], raw: Path, audio: Path, srt: Path, directory: Path) -> Path:
    output = directory / f"rendered_{int(scene['scene_number']):02d}.mp4"
    if output.exists() and output.stat().st_size > 0: return output
    escaped = str(srt.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    portrait = str(scene.get("format", "16:9")) == "9:16"
    width, height = (1080, 1920) if portrait else (1920, 1080)
    vf = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,fps=30,subtitles='" + escaped + "':force_style='FontName=DejaVu Sans,FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginV=90'"
    duration = ffprobe_duration(audio)
    run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(raw), "-i", str(audio), "-t", str(duration), "-vf", vf, "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "192k", "-shortest", str(output)], timeout=300)
    return output


def stitch(rendered: list[Path], directory: Path) -> Path:
    final = directory / "final_video.mp4"
    if final.exists() and final.stat().st_size > 0: return final
    concat = directory / "concat.txt"
    concat.write_text("\n".join(f"file '{p.resolve()}'" for p in rendered), encoding="utf-8")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(final)], timeout=300)
    return final


def youtube_credentials() -> Credentials:
    token_json = os.getenv("YOUTUBE_TOKEN_JSON", "").strip()
    if token_json:
        credentials = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
        if credentials.expired and credentials.refresh_token: credentials.refresh(GoogleAuthRequest())
        return credentials
    client_json = os.getenv("YOUTUBE_CLIENT_SECRET_JSON", "").strip()
    if not client_json: raise RuntimeError("Set YOUTUBE_TOKEN_JSON for CI or YOUTUBE_CLIENT_SECRET_JSON for first local OAuth setup.")
    secret_path = Path("client_secret.json"); secret_path.write_text(client_json, encoding="utf-8")
    token_path = Path(os.getenv("YOUTUBE_TOKEN_PATH", ".youtube-token.json"))
    credentials = None
    if token_path.exists(): credentials = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token: credentials.refresh(GoogleAuthRequest())
        else: credentials = InstalledAppFlow.from_client_secrets_file(str(secret_path), SCOPES).run_local_server(port=0)
        token_path.write_text(credentials.to_json(), encoding="utf-8")
    return credentials


def upload_youtube(video: dict[str, Any], file_path: Path) -> str:
    youtube = build("youtube", "v3", credentials=youtube_credentials())
    request = youtube.videos().insert(part="snippet,status", body={"snippet": {"title": str(video["title"])[:100], "description": str(video.get("description", "")), "categoryId": "27"}, "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False}}, media_body=MediaFileUpload(str(file_path), mimetype="video/mp4", resumable=True))
    return request.execute()["id"]


def set_youtube_thumbnail(video_id: str, image_path: Path) -> None:
    """Apply the custom JPEG to the uploaded YouTube video if the channel permits it."""
    youtube = build("youtube", "v3", credentials=youtube_credentials())
    youtube.thumbnails().set(
        videoId=video_id,
        media_body=MediaFileUpload(str(image_path), mimetype="image/jpeg", resumable=False),
    ).execute()


def mark_completed(supabase, video_id: str, youtube_id: str) -> None:
    completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    full_update = {
        "status": "completed",
        "youtube_id": youtube_id,
        "youtube_video_id": youtube_id,
        "youtube_url": f"https://www.youtube.com/watch?v={youtube_id}",
        "completed_at": completed_at,
        "error_message": None,
    }
    try:
        result = supabase.table("videos").update(full_update).eq("id", video_id).execute()
        if not getattr(result, "error", None): return
        print(f"[status] full completion update failed: {result.error}; retrying core fields")
    except Exception as error:
        print(f"[status] full completion update failed: {error}; retrying core fields")
    fallback = supabase.table("videos").update({
        "status": "completed",
        "youtube_url": f"https://www.youtube.com/watch?v={youtube_id}",
    }).eq("id", video_id).execute()
    if getattr(fallback, "error", None):
        raise RuntimeError(f"Could not save completed video status: {fallback.error}")


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
    merged = {**first_metadata, **metadata, **script, **row}
    ratio = merged.get("aspect_ratio") or merged.get("format") or "16:9"
    ratio = "9:16" if ratio == "9:16" else "16:9"
    result = []
    for i, raw in enumerate(scenes):
        item = raw if isinstance(raw, dict) else {"narration": str(raw)}
        narration = str(item.get("narration", "")).strip()
        if not narration: raise ValueError(f"Scene {i + 1} is missing narration.")
        result.append({"scene_number": i + 1, "narration": narration,
                       "search_queries": item.get("search_queries", []) or [],
                       "format": ratio})
    return result, ratio, merged


def save_fields(client, row: dict[str, Any], **changes: Any) -> None:
    """Update existing columns and embed optional fields in available JSON."""
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
    answer = client.table("videos").update(payload).eq("id", row["id"]).execute()
    if answer.data is None: raise RuntimeError("Could not persist video status.")
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
    for scene in scenes:
        save_fields(supabase, video, progress_note=f"Rendering scene {scene['scene_number']} of {len(scenes)}")
        audio, srt = make_audio(scene, directory)
        raw = fetch_broll(scene, directory)
        rendered.append(render_scene(scene, raw, audio, srt, directory))
    save_fields(supabase, video, progress_note="Joining scenes")
    final = stitch(rendered, directory)
    # Render the thumbnail from the same Pixabay B-roll rather than paying for another image API.
    headline = str(details.get("thumbnail_title") or details.get("title") or "New story")
    thumbnail_path = directory / "thumbnail.jpg"
    create_thumbnail(directory / "raw_01.mp4", headline, thumbnail_path)
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

    youtube_url, youtube_error, thumb_publish_error = "", "", ""
    if os.getenv("YOUTUBE_TOKEN_JSON", "").strip():
        try:
            youtube_id = upload_youtube(details, final)
            youtube_url = f"https://www.youtube.com/watch?v={youtube_id}"
            try:
                set_youtube_thumbnail(youtube_id, thumbnail_path)
            except Exception as error:
                thumb_publish_error = f"YouTube thumbnail: {str(error)[:180]}"
                print(f"[thumbnail] {thumb_publish_error}", flush=True)
        except Exception as error:
            youtube_error = f"YouTube upload: {str(error)[:250]}"
            print(f"[youtube] {youtube_error}", flush=True)

    warnings = [text for text in (thumbnail_error, storage_error, youtube_error, thumb_publish_error) if text]
    if not storage_url and not youtube_url:
        raise RuntimeError("Rendered master exists, but neither Supabase preview nor YouTube saved the video. " +
                           " ".join(warnings) + " If the repository is private, download the short-lived GitHub artifact; otherwise retry after fixing publishing.")
    note = "MP4 ready." if storage_url else "Full-size MP4 uploaded only to YouTube."
    if youtube_url:
        note += " YouTube upload saved privately, with custom thumbnail where permitted."
    elif not os.getenv("YOUTUBE_TOKEN_JSON", "").strip():
        note += " YouTube publishing not configured."
    if warnings:
        note += " " + " ".join(warnings)
    save_fields(supabase, video, status="completed", video_url=storage_url or None,
                storage_url=storage_url or None, youtube_url=youtube_url or None,
                thumbnail_url=thumbnail_url or None,
                completed_at=datetime.now(timezone.utc).isoformat(),
                progress_note=note[:1000], error_message=None)
    # Retain local master until workflow ends. Artifact backup is private-repository-only.
    print(f"[completed] {video_id} {storage_url}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    url = os.getenv("SUPABASE_URL") or require_env("NEXT_PUBLIC_SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or require_env("SUPABASE_KEY")
    supabase = create_client(url, key)
    ROOT.mkdir(parents=True, exist_ok=True)
    while True:
        result = supabase.table("videos").select("*").eq("status", "pending").order("created_at").limit(1).execute()
        video = None
        for candidate in result.data or []:
            changed = supabase.table("videos").update({"status": "rendering"}).eq("id", candidate["id"]).eq("status", "pending").execute()
            if changed.data:
                video = {**candidate, "status": "rendering"}
                break
        if not video:
            # Recover a genuinely abandoned render, never an actively running one.
            stale = supabase.table("videos").select("*").eq("status", "rendering").order("created_at").limit(5).execute()
            for candidate in stale.data or []:
                try:
                    started = datetime.fromisoformat(str(candidate["created_at"]).replace("Z", "+00:00"))
                    if started.tzinfo is None: started = started.replace(tzinfo=timezone.utc)
                    age_seconds = (datetime.now(timezone.utc) - started).total_seconds()
                    if age_seconds > 7200:
                        video = candidate
                        print(f"[recovery] retrying abandoned job {candidate['id']}", flush=True)
                        break
                except (KeyError, TypeError, ValueError):
                    continue
        if video:
            try:
                process(video, supabase)
            except Exception as error:
                print(f"[failed] {video.get('id')}: {error}", flush=True)
                try:
                    artifact = ROOT / str(video["id"]) / "final_video.mp4"
                    run_url = "https://github.com/" + os.getenv("GITHUB_REPOSITORY", "lenchingozi2-web/yt-automation-agent") + "/actions/runs/" + os.getenv("GITHUB_RUN_ID", "")
                    note = ("MP4 preserved as workflow artifact: " + run_url) if artifact.exists() else "Rendering failed"
                    save_fields(supabase, video, status="failed",
                                error_message=str(error)[:1000], progress_note=note)
                except Exception as status_error:
                    print(f"[failed] cannot persist error: {status_error}", flush=True)
        elif args.once: return
        else: time.sleep(POLL_SECONDS)
        if args.once: return


if __name__ == "__main__":
    main()
