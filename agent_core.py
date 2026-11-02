import os
import shutil
import json
import re
import asyncio
import subprocess
import urllib.parse
import textwrap
from pathlib import Path
from typing import List
from pydantic import BaseModel, Field
from dotenv import load_dotenv
import httpx
import edge_tts
from groq import Groq
from google import genai

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
    narration: str = Field(description="Spoken voiceover text for this scene.")
    search_queries: List[str] = Field(
        description="2-3 simple stock video search keywords, e.g. ['doctor technology', 'brain network']"
    )


class VideoScript(BaseModel):
    title: str
    description: str
    tags: List[str]
    bgm_query: str = Field(description="2-3 keywords for background music vibe, e.g., 'lofi cozy study' or 'cinematic tense'")
    scenes: List[Scene]


def get_groq_model(client: Groq) -> str:
    try:
        models = client.models.list().data
        model_ids = [m.id for m in models]
        for keyword in ["llama-3.1-8b", "llama-3.3", "gemma2"]:
            for m_id in model_ids:
                if keyword in m_id.lower():
                    return m_id
        return model_ids[0]
    except Exception:
        return "llama-3.1-8b-instant"


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

    topic = None
    try:
        print("[*] Generating topic via Gemini...")
        client = genai.Client(api_key=GEMINI_KEY)
        resp = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        topic = resp.text.strip().strip('"')
    except Exception as e:
        print(f"[!] Gemini topic failed ({e}). Falling back to Groq...")
        groq_client = Groq(api_key=GROQ_KEY)
        model_name = get_groq_model(groq_client)
        completion = groq_client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
        )
        topic = completion.choices[0].message.content.strip().strip('"')

    with open(USED_TOPICS_FILE, "a", encoding="utf-8") as f:
        f.write(topic + "\n")

    print(f"[★] LenxiQ Auto-Generated Topic: '{topic}'")
    return topic


def generate_script(topic: str, is_shorts: bool = True) -> VideoScript:
    script_path = WORK_DIR / "script.json"
    num_scenes = 5 if is_shorts else 9
    scene_length_hint = "15-25 words (short punchy pacing)" if is_shorts else "35-50 words (deep-dive long-form pacing)"

    prompt = (
        f"You are writing for the 'LenxiQ' brand.\n"
        f"Write a sharp, insightful YouTube script about: '{topic}'. "
        f"Break it into exactly {num_scenes} scenes. Each scene narration must be around {scene_length_hint}. "
        "Provide 2-3 broad stock video search keywords per scene, and a 'bgm_query' for the background music vibe.\n"
        "CRITICAL: Return ONLY a valid JSON object with keys: title, description, tags, bgm_query, and scenes (array of objects with scene_number, narration, search_queries)."
    )

    raw_content = ""
    try:
        print("[*] Generating structured script via Gemini...")
        client = genai.Client(api_key=GEMINI_KEY)
        resp = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        raw_content = resp.text.strip()
    except Exception as e:
        print(f"[!] Gemini script failed ({e}). Falling back to Groq...")
        groq_client = Groq(api_key=GROQ_KEY)
        model_name = get_groq_model(groq_client)
        completion = groq_client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_content = completion.choices[0].message.content.strip()
    
    match = re.search(r'(\{.*\})', raw_content, re.DOTALL)
    json_str = match.group(1) if match else raw_content

    data = json.loads(json_str)
    script_data = VideoScript(**data)
    script_path.write_text(script_data.model_dump_json(indent=2))
    print(f"[✓] Script saved: '{script_data.title}'")
    return script_data


async def generate_audio(scene: Scene, voice: str = "en-US-ChristopherNeural") -> Path:
    audio_path = WORK_DIR / f"scene_{scene.scene_number:02d}.mp3"
    for attempt in range(3):
        print(f"[*] Synthesizing voiceover for Scene {scene.scene_number} (Attempt {attempt + 1})...")
        communicate = edge_tts.Communicate(scene.narration, voice)
        await communicate.save(str(audio_path))
        if audio_path.exists() and audio_path.stat().st_size > 1000:
            break
        await asyncio.sleep(2)
    return audio_path


async def fetch_bgm(query: str) -> Path:
    bgm_path = WORK_DIR / "bgm.mp3"
    print(f"[*] Fetching background music for vibe: '{query}'...")
    async with httpx.AsyncClient(timeout=35.0, follow_redirects=True) as client:
        if PIXABAY_KEY:
            resp = await client.get(
                "https://pixabay.com/api/videos/",
                params={"key": PIXABAY_KEY, "q": query[:100], "per_page": 3}
            )
            if resp.status_code == 200:
                hits = resp.json().get("hits", [])
                if hits:
                    vids = hits[0].get("videos", {})
                    video_url = vids.get("small", {}).get("url") or vids.get("medium", {}).get("url")
                    if video_url:
                        dl_resp = await client.get(video_url)
                        temp_vid = WORK_DIR / "temp_bgm.mp4"
                        temp_vid.write_bytes(dl_resp.content)
                        subprocess.run(["ffmpeg", "-y", "-i", str(temp_vid), "-q:a", "0", "-map", "a", str(bgm_path)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                        if bgm_path.exists():
                            return bgm_path
    return None


async def fetch_broll(scene: Scene, is_shorts: bool = True) -> Path:
    raw_video_path = WORK_DIR / f"raw_{scene.scene_number:02d}.mp4"
    width, height = (1080, 1920) if is_shorts else (1920, 1080)

    async with httpx.AsyncClient(timeout=35.0, follow_redirects=True) as client:
        if PIXABAY_KEY:
            for query in scene.search_queries:
                print(f"[*] Searching Pixabay Videos for Scene {scene.scene_number}: '{query}'")
                resp = await client.get(
                    "https://pixabay.com/api/videos/",
                    params={"key": PIXABAY_KEY, "q": query[:100], "per_page": 5, "safesearch": "true"}
                )
                if resp.status_code == 200:
                    hits = resp.json().get("hits", [])
                    if hits:
                        vids = hits[0].get("videos", {})
                        video_url = vids.get("large", {}).get("url") or vids.get("medium", {}).get("url") or vids.get("small", {}).get("url")
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
                    ["ffmpeg", "-y", "-loop", "1", "-i", str(img_path), "-vf", f"scale={width}:{height},fps=30", "-t", "10", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(raw_video_path)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True
                )
                return raw_video_path
        except Exception:
            pass

    print(f"[!] Generating dark fallback canvas for Scene {scene.scene_number}...")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=0x111827:s={width}x{height}:d=10", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(raw_video_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True
    )
    return raw_video_path


def get_audio_duration(audio_path: Path) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)]
    return float(subprocess.check_output(cmd).decode().strip())


def render_scene(scene_num: int, raw_video: Path, audio: Path, narration: str, is_shorts: bool = True) -> Path:
    out_scene = WORK_DIR / f"rendered_{scene_num:02d}.mp4"
    duration = get_audio_duration(audio) + 0.25
    width, height = (1080, 1920) if is_shorts else (1920, 1080)

    clean_text = narration.replace("'", "").replace('"', "").replace(":", "-").replace("\n", " ")
    wrapped_text = "\\\n".join(textwrap.wrap(clean_text, width=35 if is_shorts else 50))

    vf_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1,fps=30,"
        f"drawtext=text='{wrapped_text}':fontcolor=white:fontsize=32:box=1:boxcolor=black@0.6:boxborderw=10:x=(w-text_w)/2:y=h-(h/4),"
        f"format=yuv420p"
    )

    cmd = [
        "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(raw_video), "-i", str(audio),
        "-t", str(duration), "-vf", vf_filter, "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-c:a", "aac", "-b:a", "192k",
        "-ar", "44100", "-ac", "2", "-shortest", str(out_scene),
    ]
    print(f"[*] Rendering Scene {scene_num} with Captions & Audio...")
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
    return out_scene


def stitch_final_video(rendered_scenes: List[Path], bgm_path: Path = None) -> Path:
    final_output = WORK_DIR / "final_video.mp4"
    inputs = []
    for scene in rendered_scenes:
        inputs.extend(["-i", str(scene)])

    n = len(rendered_scenes)
    filter_parts = "".join([f"[{i}:v][{i}:a]" for i in range(n)])
    
    if bgm_path and bgm_path.exists():
        inputs.extend(["-stream_loop", "-1", "-i", str(bgm_path)])
        filter_complex = f"{filter_parts}concat=n={n}:v=1:a=1[outv][concat_a];[concat_a][{n}:a]amix=inputs=2:duration=first:dropout_transition=2:weights=1 0.05[outa]"
    else:
        filter_complex = f"{filter_parts}concat=n={n}:v=1:a=1[outv][outa]"

    cmd = [
        "ffmpeg", "-y", *inputs, "-filter_complex", filter_complex,
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "192k", str(final_output)
    ]
    print(f"[*] Stitching {n} scenes into final video...")
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
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

    script = generate_script(topic, is_shorts=is_shorts)

    print("\n" + "="*50)
    print(f"📝 REVIEW GENERATED SCRIPT FOR LENXIQ")
    print(f"Title: {script.title}")
    print(f"BGM Vibe: {script.bgm_query}")
    print("-"*50)
    for s in script.scenes:
        print(f" Scene {s.scene_number}: {s.narration}")
    print("="*50 + "\n")

    auto_approve = os.getenv("AUTO_APPROVE", "false").lower() == "true"
    if not auto_approve:
        choice = input("Do you want to approve this script and proceed to rendering? (y/n): ").strip().lower()
        if choice != 'y':
            print("[!] Pipeline paused / aborted by user.")
            return
            
    bgm_task = asyncio.create_task(fetch_bgm(script.bgm_query))

    rendered_scenes = []
    for scene in script.scenes:
        audio_path = await generate_audio(scene)
        raw_video_path = await fetch_broll(scene, is_shorts=is_shorts)
        rendered_path = render_scene(scene.scene_number, raw_video_path, audio_path, scene.narration, is_shorts)
        rendered_scenes.append(rendered_path)

    bgm_path = await bgm_task
    stitch_final_video(rendered_scenes, bgm_path)

if __name__ == "__main__":
    asyncio.run(run_pipeline())
