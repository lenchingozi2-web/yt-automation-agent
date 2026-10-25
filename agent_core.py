import os
import shutil
import json
import asyncio
import subprocess
import urllib.parse
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

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
GROQ_KEY = os.getenv("GROQ_API_KEY")
PIXABAY_KEY = os.getenv("PIXABAY_API_KEY")

LENXIQ_BRAND_IDENTITY = (
    "LenxiQ (Umbrella Brand): "
    "1. The advent of AI and its real-world impact on Medicine & Healthcare. "
    "2. AI's transformation of Learning, Education, and Accelerated Mastery. "
    "3. Building real AI products, autonomous agents, and software systems. "
    "4. Human Intelligence, First-Principles Thinking, and Common Sense in the AI era."
)

WORK_DIR = Path("output_run")
USED_TOPICS_FILE = Path("used_topics.txt")


class Scene(BaseModel):
    scene_number: int
    narration: str = Field(description="Spoken voiceover text for this scene (15-25 words).")
    search_queries: List[str] = Field(
        description="2-3 simple 2-to-3-word stock video search keywords, e.g. ['doctor technology', 'coding laptop', 'brain network']"
    )


class VideoScript(BaseModel):
    title: str
    description: str
    tags: List[str]
    scenes: List[Scene]


def generate_autonomous_topic(brand_pillars: str) -> str:
    past_topics = []
    if USED_TOPICS_FILE.exists():
        past_topics = [line.strip() for line in USED_TOPICS_FILE.read_text().splitlines() if line.strip()]

    avoid_str = ", ".join(past_topics[-30:]) if past_topics else "None yet"
    prompt = (
        f"You are the chief content strategist for the brand: {brand_pillars}\n"
        "Pick ONE of the brand's pillars and generate ONE sharp, thought-provoking, high-retention "
        "YouTube video topic idea (8-14 words max) that builds authority for the LenxiQ brand. "
        f"Do NOT repeat any of these previous topics: [{avoid_str}]. "
        "Reply with ONLY the raw topic title and nothing else."
    )

    try:
        client = genai.Client(api_key=GEMINI_KEY)
        resp = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        topic = resp.text.strip().strip('"')
    except Exception:
        groq_client = Groq(api_key=GROQ_KEY)
        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
        )
        topic = completion.choices[0].message.content.strip().strip('"')

    with open(USED_TOPICS_FILE, "a", encoding="utf-8") as f:
        f.write(topic + "\n")

    print(f"[★] LenxiQ Auto-Generated Topic: '{topic}'")
    return topic


def generate_script(topic: str, num_scenes: int = 5) -> VideoScript:
    script_path = WORK_DIR / "script.json"

    prompt = (
        f"You are writing for the 'LenxiQ' brand (focused on AI in Medicine, AI in Education, "
        f"Building AI Products, and Intelligence/Common Sense).\n"
        f"Write a sharp, insightful, high-retention YouTube script about: '{topic}'. "
        f"Break it into exactly {num_scenes} scenes with a strong hook, grounded practical insight, "
        "and a smart takeaway. Avoid cheesy clichés; sound like a builder and thinker. "
        "Provide 2-3 simple, broad stock video search keywords (2-3 words max each) per scene."
    )

    try:
        print("[*] Generating script via Gemini 2.5 Flash...")
        client = genai.Client(api_key=GEMINI_KEY)
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
        print(f"[!] Gemini unavailable ({e}). Switching to Groq Llama-3.3-70B...")
        groq_client = Groq(api_key=GROQ_KEY)
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


async def fetch_broll(scene: Scene, is_shorts: bool = True) -> Path:
    raw_video_path = WORK_DIR / f"raw_{scene.scene_number:02d}.mp4"
    width, height = (1080, 1920) if is_shorts else (1920, 1080)

    async with httpx.AsyncClient(timeout=35.0, follow_redirects=True) as client:
        if PIXABAY_KEY:
            for query in scene.search_queries:
                print(f"[*] Searching Pixabay Videos for Scene {scene.scene_number}: '{query}'")
                resp = await client.get(
                    "https://pixabay.com/api/videos/",
                    params={
                        "key": PIXABAY_KEY,
                        "q": query[:100],
                        "per_page": 5,
                        "safesearch": "true",
                    },
                )
                if resp.status_code == 200:
                    hits = resp.json().get("hits", [])
                    if hits:
                        vids = hits[0].get("videos", {})
                        video_url = (
                            vids.get("large", {}).get("url")
                            or vids.get("medium", {}).get("url")
                            or vids.get("small", {}).get("url")
                        )
                        if video_url:
                            dl_resp = await client.get(video_url)
                            raw_video_path.write_bytes(dl_resp.content)
                            print(f"[✓] Downloaded Pixabay video for Scene {scene.scene_number}")
                            return raw_video_path

        try:
            prompt_str = f"{scene.search_queries[0]}, futuristic clean aesthetic, cinematic lighting, 8k"
            encoded_prompt = urllib.parse.quote(prompt_str)
            img_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width={width}&height={height}&nologo=true&seed={scene.scene_number}"
            print(f"[*] Generating keyless AI visual for Scene {scene.scene_number}...")
            img_resp = await client.get(img_url)
            if img_resp.status_code == 200:
                img_path = WORK_DIR / f"temp_{scene.scene_number:02d}.jpg"
                img_path.write_bytes(img_resp.content)
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-loop", "1", "-i", str(img_path),
                        "-vf", f"scale={width}:{height},fps=30",
                        "-t", "10", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(raw_video_path),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    check=True,
                )
                return raw_video_path
        except Exception as e:
            print(f"[!] AI image fallback skipped ({e})")

    print(f"[!] Generating dark fallback canvas for Scene {scene.scene_number}...")
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=0x111827:s={width}x{height}:d=10",
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
    print(f"\n[✓] SUCCESS! Look at the left Explorer sidebar -> click 'output_run' -> click 'final_video.mp4'!")
    return final_output


async def run_pipeline():
    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR)
    WORK_DIR.mkdir(exist_ok=True)

    raw_topic = os.getenv("VIDEO_TOPIC", "AUTO").strip()
    brand_pillars = os.getenv("CHANNEL_NICHE", LENXIQ_BRAND_IDENTITY)
    is_shorts = os.getenv("IS_SHORTS", "true").lower() == "true"

    if not raw_topic or raw_topic.upper() == "AUTO":
        topic = generate_autonomous_topic(brand_pillars)
    else:
        topic = raw_topic
        with open(USED_TOPICS_FILE, "a", encoding="utf-8") as f:
            f.write(topic + "\n")

    script = generate_script(topic, num_scenes=5)
    rendered_scenes = []

    for scene in script.scenes:
        audio_path, srt_path = await generate_audio_and_subs(scene)
        raw_video_path = await fetch_broll(scene, is_shorts=is_shorts)
        rendered_path = render_scene(scene.scene_number, raw_video_path, audio_path, srt_path, is_shorts)
        rendered_scenes.append(rendered_path)

    stitch_final_video(rendered_scenes)


if __name__ == "__main__":
    asyncio.run(run_pipeline())
