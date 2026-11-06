"""Unattended daily producer using the same Supabase queue and renderer as Studio."""
from __future__ import annotations

import json
import math
import os
import re
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from supabase import create_client

BRAND = ("LenxiQ: practical AI in medicine and healthcare; learning and education; "
         "building AI products and agents; human intelligence and common sense.")


def parse_json(text: str) -> dict:
    data = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip()))
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    return data


def provider_error(error: Exception) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        try:
            details = error.response.json().get("error", {})
            kind = details.get("code") or details.get("type") or details.get("status") if isinstance(details, dict) else ""
        except Exception:
            kind = ""
        return f"HTTP {error.response.status_code} {kind}"
    return type(error).__name__


def generate_json(instruction: str) -> dict:
    errors = []
    with httpx.Client(timeout=45) as client:
        key = os.getenv("GROQ_API_KEY", "").strip()
        if key:
            # Never choose the first catalog entry: it may be a speech recognizer.
            preferred = list(dict.fromkeys([os.getenv("GROQ_CHAT_MODEL", "openai/gpt-oss-20b"), "openai/gpt-oss-120b"]))
            # Confirm account access when the catalog is reachable. Never route
            # scripts to an audio or moderation model, including an invalid override.
            preferred = [n for n in preferred if n.startswith("openai/gpt-oss-") and "safeguard" not in n]
            try:
                catalog = client.get("https://api.groq.com/openai/v1/models", headers={"Authorization": "Bearer " + key}, timeout=10)
                catalog.raise_for_status()
                active = {m["id"] for m in catalog.json().get("data", [])}
                preferred = [n for n in preferred if n in active]
            except Exception:
                pass
            for name in preferred:
                try:
                    reply = client.post("https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": "Bearer " + key}, json={"model": name,
                        "messages": [{"role": "user", "content": instruction}],
                        "response_format": {"type": "json_object"}, "max_tokens": 8192})
                    reply.raise_for_status()
                    return parse_json(reply.json()["choices"][0]["message"]["content"])
                except Exception as error:
                    errors.append("Groq " + name + " " + provider_error(error))
        key = os.getenv("GEMINI_API_KEY", "").strip()
        if key:
            try:
                response = client.get("https://generativelanguage.googleapis.com/v1beta/models",
                    headers={"x-goog-api-key": key}, params={"pageSize": 1000}, timeout=10)
                response.raise_for_status()
                names = [m["name"] for m in response.json().get("models", [])
                         if "generateContent" in m.get("supportedGenerationMethods", [])
                         and "flash" in m["name"] and not re.search("image|tts|audio|live|embedding", m["name"])]
                names.sort(key=lambda n: ("latest" in n, "preview" not in n,
                    tuple(int(x) for x in re.findall(r"\d+", n)), "lite" not in n), reverse=True)
            except Exception:
                names = ["models/gemini-flash-latest"]
            for name in names[:3]:
                try:
                    reply = client.post("https://generativelanguage.googleapis.com/v1beta/" + name + ":generateContent",
                        headers={"x-goog-api-key": key}, json={
                            "contents": [{"parts": [{"text": instruction}]}],
                            "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": 16384}})
                    reply.raise_for_status()
                    parts = reply.json()["candidates"][0]["content"]["parts"]
                    return parse_json("".join(p.get("text", "") for p in parts if not p.get("thought")))
                except Exception as error:
                    errors.append("Gemini " + name + " " + provider_error(error))
    raise RuntimeError("Script generation unavailable: " + ", ".join(errors) + ". Check model access and quota.")


def validate_script(data: dict, seconds: int, ratio: str) -> dict:
    if not str(data.get("title", "")).strip():
        raise ValueError("Generated video has no title")
    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not 3 <= len(scenes) <= 60:
        raise ValueError("Generated video must contain 3 to 60 narrated scenes")
    for i, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict) or not str(scene.get("narration", "")).strip():
            raise ValueError(f"Scene {i} has no narration")
        if not isinstance(scene.get("search_queries"), list) or not any(str(q).strip() for q in scene["search_queries"]):
            raise ValueError(f"Scene {i} has no relevant footage search")
        scene["scene_number"] = i
        scene["format"] = ratio
    narration = " ".join(s["narration"] for s in scenes)
    if re.search(r"\bstud(?:y|ies)\s+(?:found|shows?|suggests?|where|reported|demonstrated)|\bclinical trial|\d+\s*%|\bpercent(?:age)?\b|reduces? hospital visits|lower hospital visits", narration, re.I):
        raise ValueError("Unverified study or outcome claim in automatic narration")
    count = sum(len(s["narration"].split()) for s in scenes)
    if count < seconds * 1.6 or count > seconds * 3.4:
        raise ValueError("Script word count does not match the requested duration")
    return {**data, "scenes": scenes, "format": ratio, "duration_seconds": seconds}


def queue_daily(client, *, now: datetime | None = None) -> dict | None:
    now = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Africa/Lagos"))
    start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    recent = client.table("videos").select("*").order("created_at", desc=True).limit(100).execute().data or []
    job_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "lenxiq-studio-daily:" + now.date().isoformat()))
    existing = client.table("videos").select("*").eq("id", job_id).execute().data or []
    refresh = None
    if existing:
        from studio_maintenance import metadata
        meta = metadata(existing[0])
        if meta.get("automation") == "daily" and meta.get("render_version") == "studio-v4" and not meta.get("youtube_url") and existing[0].get("status") == "completed":
            refresh = existing[0]
        else:
            print("[automation] Daily video already queued; no duplicate created.", flush=True)
            return existing[0]
    limit = max(1, min(20, int(os.getenv("STUDIO_DAILY_LIMIT", "3"))))
    quota = client.table("videos").select("id", count="exact").gte("created_at", start).execute()
    if not refresh and (quota.count or 0) >= limit:
        print("[automation] Daily allowance reached; keeping existing jobs.", flush=True)
        return None
    objects = client.storage.from_(os.getenv("SUPABASE_VIDEO_BUCKET", "renders")).list(options={"limit": 1000})
    if sum(int((o.get("metadata") or {}).get("size") or 0) for o in objects) >= 800 * 1024 * 1024:
        raise RuntimeError("Preview storage nearly full; archive older videos before producing another")
    owners = {r["user_id"] for r in recent if r.get("user_id")}
    owner = str(refresh.get("user_id") or "") if refresh else os.getenv("STUDIO_OWNER_USER_ID", "")
    if not owner:
        if len(owners) == 1:
            owner = next(iter(owners))
        elif not owners:
            users = client.auth.admin.list_users(page=1, per_page=2)
            if len(users) == 1:
                owner = str(users[0].id)
    if not owner:
        raise RuntimeError("Cannot identify one Studio owner; configure STUDIO_OWNER_USER_ID")
    seconds = max(60, min(720, int(os.getenv("AUTO_VIDEO_SECONDS", "60"))))
    ratio = "9:16" if os.getenv("IS_SHORTS", "true").lower() == "true" else "16:9"
    topic = os.getenv("VIDEO_TOPIC", "AUTO").strip()
    brief = "Choose a fresh topic under " + os.getenv("CHANNEL_NICHE", BRAND) if topic.upper() == "AUTO" or not topic else topic
    history = json.dumps([r.get("title", "") for r in recent[:40]])
    prompt = (f"Create one original {seconds}-second YouTube video. Brief: {brief}. Avoid these prior titles: {history}. "
              f"Return JSON: title, description, tags array, thumbnail_title (3-6 words), scenes array of approximately {math.ceil(seconds / 10)} scenes. "
              "Each scene has scene_number, narration, search_queries (2-3 specific simple Pixabay video phrases). "
              f"Use about {round(seconds * 2.45)} spoken words in total. Natural conversational English, contractions, varied sentence lengths, "
              "a strong opening hook, one concrete example, and a useful closing thought. No markdown, stage directions, canned intros or invented studies/statistics. "
              "Do not mention studies, trials, percentages or clinical outcomes. Do not claim AI predicts or prevents heart attacks, improves treatment or reduces hospital visits. "
              "For health topics explain basic technology and limits; examples must be explicitly hypothetical and must not imply a diagnosis or proven clinical benefit. Every narration must be nonempty. "
              "Footage queries must describe visible objects or actions: doctor patient, smart watch, medical laboratory, computer code, student studying. "
              "Never request green screen, countdowns, like/subscribe graphics, rivers, scenery or abstract concepts for health content. "
              "Use meaningful sentences rather than dictation. Visual sources must be Pixabay only.")
    try:
        draft = validate_script(generate_json(prompt), seconds, ratio)
    except (ValueError, KeyError):
        draft = validate_script(generate_json(prompt + " Ensure every scene is fully narrated and the word count matches the target."), seconds, ratio)
    scenes = draft["scenes"]
    scenes[0]["_studio"] = {k: v for k, v in draft.items() if k != "scenes"}
    scenes[0]["_studio"].update({"aspect_ratio": ratio, "automation": "daily", "automation_date": now.date().isoformat()})
    payload = {"id": job_id, "title": draft["title"], "description": str(draft.get("description", "")),
               "status": "pending", "user_id": owner, "scenes": scenes}
    if refresh:
        scenes[0]["_studio"]["previous_narration"] = [x.get("narration", "") for x in refresh.get("scenes", [])]
        saved = client.table("videos").update({k: v for k, v in payload.items() if k != "id"}).eq("id", job_id).eq("status", "completed").execute().data
        if not saved:
            raise RuntimeError("Could not queue the unpublished quality upgrade")
        print("[automation] Unpublished daily video queued for footage and caption quality upgrade.", flush=True)
        return saved[0]
    try:
        saved = client.table("videos").insert(payload).execute().data
    except Exception:
        found = client.table("videos").select("*").eq("id", job_id).execute().data
        if found:
            return found[0]
        raise
    if not saved:
        raise RuntimeError("Daily job was not saved")
    print("[automation] Daily narrated video queued in Studio.", flush=True)
    return saved[0]


def main() -> None:
    url = os.getenv("SUPABASE_URL") or os.environ["NEXT_PUBLIC_SUPABASE_URL"]
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.environ["SUPABASE_KEY"]
    queue_daily(create_client(url, key))


if __name__ == "__main__":
    main()
