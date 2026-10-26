"""LenxiQ Studio background renderer.

Run locally with `python worker.py --once` or continuously with `python worker.py`.
The process is intentionally outside Vercel: it needs FFmpeg, local disk, and CPU audio models.
"""
from __future__ import annotations

import argparse
import asyncio
import json
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

load_dotenv()
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
ROOT = Path(os.getenv("WORKER_OUTPUT_DIR", "output_run"))
VOICE = os.getenv("EDGE_TTS_VOICE", "en-US-ChristopherNeural")
POLL_SECONDS = int(os.getenv("WORKER_POLL_SECONDS", "20"))
MAX_SCENE_SECONDS = int(os.getenv("MAX_SCENE_SECONDS", "18"))


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def run(command: list[str], *, timeout: int = 180) -> None:
    subprocess.run(command, check=True, timeout=timeout, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


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
    """CPU fallback. Install kokoro-onnx and provide KOKORO_MODEL/KOKORO_VOICES."""
    import soundfile as sf
    from kokoro_onnx import Kokoro
    from faster_whisper import WhisperModel
    model = os.getenv("KOKORO_MODEL", "kokoro-v1.0.onnx")
    voices = os.getenv("KOKORO_VOICES", "voices.bin")
    voice = os.getenv("KOKORO_VOICE", "af_heart")
    samples, sample_rate = Kokoro(model, voices).create(text, voice=voice, speed=1.0, lang="en-us")
    wav = audio.with_suffix(".wav")
    sf.write(wav, samples, sample_rate)
    run(["ffmpeg", "-y", "-i", str(wav), "-codec:a", "libmp3lame", "-q:a", "3", str(audio)])
    whisper = WhisperModel(os.getenv("WHISPER_MODEL", "tiny.en"), device="cpu", compute_type="int8")
    segments, _ = whisper.transcribe(str(wav), word_timestamps=True)
    words: list[tuple[float, float, str]] = []
    for segment in segments:
        for word in segment.words or []:
            words.append((float(word.start), float(word.end), word.word))
    write_srt(words, srt)
    wav.unlink(missing_ok=True)


def make_audio(scene: dict[str, Any], directory: Path) -> tuple[Path, Path]:
    number = int(scene["scene_number"])
    audio, srt = directory / f"scene_{number:02d}.mp3", directory / f"scene_{number:02d}.srt"
    if audio.exists() and srt.exists() and audio.stat().st_size > 0: return audio, srt
    text = str(scene["narration"]).strip()
    try:
        asyncio.run(asyncio.wait_for(edge_tts_scene(text, audio, srt), timeout=45))
    except Exception as error:
        print(f"[audio] edge-tts failed for scene {number}: {error}; using Kokoro fallback")
        kokoro_scene(text, audio, srt)
    return audio, srt


async def fetch_pexels(scene: dict[str, Any], target: Path) -> bool:
    key = os.getenv("PEXELS_API_KEY", "").strip()
    if not key: return False
    headers = {"Authorization": key}
    async with httpx.AsyncClient(timeout=40, follow_redirects=True) as client:
        for query in scene.get("search_queries", []):
            response = await client.get("https://api.pexels.com/videos/search", headers=headers, params={"query": query, "orientation": "portrait", "size": "medium", "per_page": 10})
            if response.status_code != 200: continue
            candidates = []
            for item in response.json().get("videos", []):
                files = [f for f in item.get("video_files", []) if f.get("width", 0) < f.get("height", 0) and f.get("height", 0) >= 720 and f.get("link")]
                if files: candidates.append(max(files, key=lambda f: f.get("height", 0)))
            if not candidates: continue
            clip = max(candidates, key=lambda f: f.get("height", 0))
            download = await client.get(clip["link"])
            if download.status_code == 200 and len(download.content) > 10000:
                target.write_bytes(download.content)
                return True
    return False


def dark_canvas(target: Path) -> None:
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=0x101827:s=1080x1920:r=30", "-t", str(MAX_SCENE_SECONDS), "-pix_fmt", "yuv420p", "-c:v", "libx264", str(target)])


def fetch_broll(scene: dict[str, Any], directory: Path) -> Path:
    target = directory / f"raw_{int(scene['scene_number']):02d}.mp4"
    if target.exists() and target.stat().st_size > 0: return target
    try:
        if asyncio.run(fetch_pexels(scene, target)): return target
    except Exception as error: print(f"[b-roll] Pexels failed: {error}")
    print(f"[b-roll] using dark motion canvas for scene {scene['scene_number']}")
    dark_canvas(target)
    return target


def render_scene(scene: dict[str, Any], raw: Path, audio: Path, srt: Path, directory: Path) -> Path:
    output = directory / f"rendered_{int(scene['scene_number']):02d}.mp4"
    if output.exists() and output.stat().st_size > 0: return output
    escaped = str(srt.resolve()).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=30,subtitles='" + escaped + "':force_style='FontName=DejaVu Sans,FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginV=90'"
    duration = min(MAX_SCENE_SECONDS, ffprobe_duration(audio) + 0.35)
    run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(raw), "-i", str(audio), "-t", str(duration), "-vf", vf, "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-c:a", "aac", "-b:a", "192k", "-shortest", str(output)], timeout=240)
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


def process(video: dict[str, Any], supabase) -> None:
    video_id = str(video["id"])
    directory = ROOT / video_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "video.json").write_text(json.dumps(video, indent=2), encoding="utf-8")
    scenes = video.get("scenes") or []
    rendered: list[Path] = []
    for scene in scenes:
        audio, srt = make_audio(scene, directory)
        raw = fetch_broll(scene, directory)
        rendered.append(render_scene(scene, raw, audio, srt, directory))
    final = stitch(rendered, directory)
    youtube_id = upload_youtube(video, final)
    supabase.table("videos").update({"status": "completed", "youtube_video_id": youtube_id, "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "error_message": None}).eq("id", video_id).execute()
    shutil.rmtree(directory, ignore_errors=True)
    print(f"[complete] {video_id} uploaded privately as https://youtu.be/{youtube_id}")


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--once", action="store_true"); args = parser.parse_args()
    supabase = create_client(require_env("NEXT_PUBLIC_SUPABASE_URL"), require_env("SUPABASE_SERVICE_ROLE_KEY"))
    ROOT.mkdir(parents=True, exist_ok=True)
    while True:
        result = supabase.table("videos").select("*").eq("status", "rendering").order("created_at").limit(1).execute()
        video = (result.data or [None])[0]
        if video:
            try: process(video, supabase)
            except Exception as error:
                print(f"[failed] {video.get('id')}: {error}")
                supabase.table("videos").update({"status": "failed", "error_message": str(error)[:1000]}).eq("id", video["id"]).eq("status", "rendering").execute()
        elif args.once: return
        else: time.sleep(POLL_SECONDS)

if __name__ == "__main__": main()
