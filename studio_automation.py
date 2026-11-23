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
from studio_master import MASTER_BUCKET, STORAGE_BUDGET, bucket_exists, media_objects

BRAND = ("LenxiQ: practical AI in medicine and healthcare; learning and education; "
         "building AI products and agents; human intelligence and common sense.")


class ResearchDeferred(RuntimeError):
    """Editorial or upstream failure, not a queue/storage failure."""


def daily_automation_key(now: datetime, event: str, run_id: str = "") -> str:
    """Use one repeatable slot per scheduled attempt, but never swallow manual requests."""
    if event == "workflow_dispatch" and run_id.isdecimal():
        return f"manual:{run_id}"
    return f"{now.date().isoformat()}:{now.hour // 4}"



def generate_researched_draft(brief: str, seconds: int, ratio: str, recent: list[str]) -> dict:
    command = Path(__file__).resolve().parent / "src/lib/studio-daily.ts"
    result = subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", str(command)],
        input=json.dumps({"brief": brief, "seconds": seconds, "ratio": ratio, "recent": recent}),
        capture_output=True, text=True, timeout=320,
    )
    if result.returncode:
        detail = result.stderr.strip()
        final = detail.splitlines()[-1] if detail else ""
        retryable = (
            "No video was queued.", "No new topic passed the source-quality check",
            "Studio found too little source material", "Studio’s writing model is unavailable",
            "The research plan was incomplete",
        )
        if any(marker in final for marker in retryable):
            raise ResearchDeferred(final)
        raise RuntimeError(detail[-1400:] or "Studio’s researched script failed.")
    if result.stderr.strip():
        print(result.stderr.strip(), flush=True)
    return json.loads(result.stdout)


def queue_daily(client, *, now: datetime | None = None) -> dict | None:
    now = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Africa/Lagos"))
    start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    recent = client.table("videos").select("*").order("created_at", desc=True).limit(100).execute().data or []
    job_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "lenxiq-studio-daily:" + daily_automation_key(
        now, os.getenv("GITHUB_EVENT_NAME", ""), os.getenv("GITHUB_RUN_ID", ""))))
    existing = client.table("videos").select("*").eq("id", job_id).execute().data or []
    if existing:
        print("[automation] Daily video already queued; no duplicate created.", flush=True)
        return existing[0]
    limit = max(1, min(20, int(os.getenv("STUDIO_DAILY_LIMIT", "6"))))
    quota = client.table("videos").select("id", count="exact").gte("created_at", start).execute()
    if (quota.count or 0) >= limit:
        print("[automation] Daily allowance reached; keeping existing jobs.", flush=True)
        return None
    # Include private upload masters, not just public preview objects. Preserve
    # headroom for the owner's manual videos instead of consuming it on automation.
    preview_bytes = sum(int((o.get("metadata") or {}).get("size") or 0)
                        for o in media_objects(client, os.getenv("SUPABASE_VIDEO_BUCKET", "renders")))
    master_bytes = (sum(int((o.get("metadata") or {}).get("size") or 0)
                        for o in media_objects(client, MASTER_BUCKET))
                    if bucket_exists(client, MASTER_BUCKET) else 0)
    if preview_bytes + master_bytes >= STORAGE_BUDGET - 160 * 1024 * 1024:
        print("[automation] Skipping daily render: storage headroom reserved for manual production.", flush=True)
        return None
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
    try:
        draft = generate_researched_draft(brief, seconds, ratio, history)
    except (ResearchDeferred, subprocess.TimeoutExpired) as error:
        # A scheduled run can safely defer; manual requests must report failure.
        # No queue entry exists yet, so the next scheduled attempt is idempotent.
        if os.getenv("GITHUB_EVENT_NAME") != "schedule":
            raise
        message = ("Automatic video deferred: research timed out." if isinstance(error, subprocess.TimeoutExpired)
                   else "Automatic video deferred: " + str(error))
        print("::warning::" + message, flush=True)
        if os.getenv("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
                summary.write("### Automatic video deferred\n" + message + "\n")
        return None
    scenes = draft["scenes"]
    scenes[0]["_studio"] = {k: v for k, v in draft.items() if k != "scenes"}
    scenes[0]["_studio"].update({"aspect_ratio": ratio, "automation": "daily", "automation_date": now.date().isoformat(), "automation_slot": now.hour // 4})
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
    queued = queue_daily(create_client(url, key))
    if queued and os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write("queued=true\n")


if __name__ == "__main__":
    main()
