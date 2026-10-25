import os
import json
import asyncio
import subprocess
from pathlib import Path
from typing import List
from pydantic import BaseModel, Field
from dotenv import load_dotenv
import httpx
import edge_tts
from google import genai
from google.genai import types
from groq import Groq

load_dotenv()

WORK_DIR = Path("output_run")
WORK_DIR.mkdir(exist_ok=True)


class Scene(BaseModel):
    scene_number: int
    narration: str = Field(description="Spoken voiceover text for this scene (15-25 words).")
    search_queries: List[str] = Field(description="2-3 specific Pexels video search keywords.")


class VideoScript(BaseModel):
    title: str
    description: str
    tags: List[str]
    scenes: List[Scene]


def generate_script(topic: str, num_scenes: int = 5) -> VideoScript:
    script_path = WORK_DIR / "script.json"
    if script_path.exists():
        print("[✓] Loaded cached script.json")
        return VideoScript.model_validate_json(script_path.read_text())

    prompt = (
        f"Write a high-retention YouTube script about: '{topic}'. "
        f"Break it into exactly {num_scenes} scenes with punchy hooks and clear storytelling. "
        "Provide 2-3 specific stock video search queries per scene."
    )

    try:
        print("[*] Generating script via Gemini 2.5 Flash...")
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=VideoScript,
                temperature=0.7,
            ),
        )
        script_data = VideoScript.model_validate_json(response.text)
    except Exception as e:
        print(f"[!] Gemini failed ({e}). Falling back to Groq Llama-3.3-70B...")
        groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])
        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": f"Output ONLY valid JSON matching this schema: {json.dumps(VideoScript.model_json_schema())}",
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )
        script_data = VideoScript.model_validate_json(completion.choices[0].message.content)

    script_path.write_text(script_data.model_dump_json(indent=2))
    print(f"[✓] Script saved: '{script_data.title}'")
    return script_data


async def generate_audio_and_subs(scene: Scene, voice: str = "en-US-ChristopherNeural") -> tuple[Path, Path]:
    audio_path = WORK_DIR / f"scene_{scene.scene_number:02d}.mp3"
    srt_path = WORK_DIR / f"scene_{scene.scene_number:02d}.srt"

    if audio_path.exists() and srt_path.exists():
        print(f"[✓] Cached audio/subs for Scene {scene.scene_number}")
        return audio_path, srt_path

    print(f"[*] Synthesizing voiceover for Scene {scene.scene_number}...")
    communicate = edge_tts.Communicate(scene.narration, voice)
    submaker = edge_tts.SubMaker()

    with open(audio_path, "wb") as audio_file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_file.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                submaker.feed(chunk)

    with open(srt_path, "w", encoding="utf-8") as srt_file:
        srt_file.write(submaker.get_srt())

    return audio_path, srt_path


async def fetch_broll(scene: Scene, orientation: str = "portrait") -> Path:
    raw_video_path = WORK_DIR / f"raw_{scene.scene_number:02d}.mp4"
    if raw_video_path.exists():
        print(f"[✓] Cached B-Roll for Scene {scene.scene_number}")
        return raw_video_path

    pexels_key = os.getenv("PEXELS_API_KEY")
    headers = {"Authorization": pexels_key} if pexels_key else {}

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for query in scene.search_queries:
            print(f"[*] Searching Pexels for Scene {scene.scene_number}: '{query}'")
            resp = await client.get(
                "https://api.pexels.com/videos/search",
                params={"query": query, "per_page": 3, "orientation": orientation, "size": "medium"},
                headers=headers,
            )
            if resp.status_code == 200:
                videos = resp.json().get("videos", [])
                if videos:
                    video_files = videos[0].get("video_files", [])
                    best_file = next((f for f in video_files if f.get("height", 0) >= 720), video_files[0])
                    dl_resp = await client.get(best_file["link"])
                    raw_video_path.write_bytes(dl_resp.content)
                    return raw_video_path

    print(f"[!] No stock clip found for Scene {scene.scene_number}, generating dark fallback canvas...")
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=0x111827:s=1080x1920:d=10",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(raw_video_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=True,
    )
    return raw_video_path


def get_audio_duration(audio_path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path),
    ]
    return float(subprocess.check_output(cmd).decode().strip())


def render_scene(scene_num: int, raw_video: Path, audio: Path, srt: Path, is_shorts: bool = True) -> Path:
    out_scene = WORK_DIR / f"rendered_{scene_num:02d}.mp4"
    if out_scene.exists():
        return out_scene

    duration = get_audio_duration(audio) + 0.25
    width, height = (1080, 1920) if is_shorts else (1920, 1080)
    srt_escaped = str(srt).replace("\\", "/").replace(":", "\\:")

    vf_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,fps=30,"
        f"subtitles='{srt_escaped}':force_style='FontName=DejaVu Sans,FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Alignment=2,MarginV=65'"
    )

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", str(raw_video),
        "-i", str(audio),
        "-t", str(duration),
        "-vf", vf_filter,
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", str(out_scene),
    ]
    print(f"[*] Rendering Scene {scene_num} with FFmpeg...")
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
    return out_scene


def stitch_final_video(rendered_scenes: List[Path]) -> Path:
    concat_file = WORK_DIR / "concat_list.txt"
    final_output = WORK_DIR / "final_video.mp4"

    lines = [f"file '{scene.name}'" for scene in rendered_scenes]
    concat_file.write_text("\n".join(lines))

    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_file), "-c", "copy", str(final_output),
    ]
    subprocess.run(cmd, cwd=WORK_DIR, check=True)
    print(f"\n[✓] SUCCESS! Final video rendered at: {final_output}")
    return final_output


async def run_pipeline():
    topic = os.getenv("VIDEO_TOPIC", "Why Sleep Deprivation Destroys Your Brain Within 48 Hours")
    is_shorts = os.getenv("IS_SHORTS", "true").lower() == "true"
    orientation = "portrait" if is_shorts else "landscape"

    script = generate_script(topic, num_scenes=5)
    rendered_scenes = []

    for scene in script.scenes:
        audio_path, srt_path = await generate_audio_and_subs(scene)
        raw_video_path = await fetch_broll(scene, orientation=orientation)
        rendered_path = render_scene(scene.scene_number, raw_video_path, audio_path, srt_path, is_shorts)
        rendered_scenes.append(rendered_path)

    stitch_final_video(rendered_scenes)


if __name__ == "__main__":
    asyncio.run(run_pipeline())
