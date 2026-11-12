"""Unattended daily producer using the same Supabase queue and renderer as Studio."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from supabase import create_client

BRAND = ("LenxiQ: practical AI in medicine and healthcare; learning and education; "
         "building AI products and agents; human intelligence and common sense.")


def generate_researched_draft(brief: str, seconds: int, ratio: str, recent: list[str]) -> dict:
    command = Path(__file__).resolve().parent / "src/lib/studio-daily.ts"
    result = subprocess.run(
        ["node", "--experimental-strip-types", "--disable-warning=ExperimentalWarning", str(command)],
        input=json.dumps({"brief": brief, "seconds": seconds, "ratio": ratio, "recent": recent}),
        capture_output=True, text=True, timeout=220,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[:400] or "Studio’s researched script failed.")
    return json.loads(result.stdout)


def queue_daily(client, *, now: datetime | None = None) -> dict | None:
    now = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Africa/Lagos"))
    start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    recent = client.table("videos").select("*").order("created_at", desc=True).limit(100).execute().data or []
    job_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "lenxiq-studio-daily:" + now.date().isoformat()))
    existing = client.table("videos").select("*").eq("id", job_id).execute().data or []
    if existing:
        print("[automation] Daily video already queued; no duplicate created.", flush=True)
        return existing[0]
    limit = max(1, min(20, int(os.getenv("STUDIO_DAILY_LIMIT", "3"))))
    quota = client.table("videos").select("id", count="exact").gte("created_at", start).execute()
    if (quota.count or 0) >= limit:
        print("[automation] Daily allowance reached; keeping existing jobs.", flush=True)
        return None
    objects = client.storage.from_(os.getenv("SUPABASE_VIDEO_BUCKET", "renders")).list(options={"limit": 1000})
    if sum(int((o.get("metadata") or {}).get("size") or 0) for o in objects) >= 800 * 1024 * 1024:
        raise RuntimeError("Preview storage nearly full; archive older videos before producing another")
    owners = {r["user_id"] for r in recent if r.get("user_id")}
    owner = os.getenv("STUDIO_OWNER_USER_ID", "")
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
    history = list(dict.fromkeys(str(row.get("title") or "")[:150] for row in recent[:40]))
    draft = generate_researched_draft(brief, seconds, ratio, history)
    scenes = draft["scenes"]
    scenes[0]["_studio"] = {k: v for k, v in draft.items() if k != "scenes"}
    scenes[0]["_studio"].update({"aspect_ratio": ratio, "automation": "daily", "automation_date": now.date().isoformat()})
    payload = {"id": job_id, "title": draft["title"], "description": str(draft.get("description", "")),
               "status": "pending", "user_id": owner, "scenes": scenes}
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
