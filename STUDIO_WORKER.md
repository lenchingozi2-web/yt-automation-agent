# YT Automation Studio — operational handbook

## Video production
- Root Next.js handles signed-in Studio drafts, scene editor, queue, daily allowance and media previews.
- GitHub Actions runs root worker.py on dispatch or its five-minute fallback schedule.
- Worker claims pending jobs, downloads B-roll strictly from Pixabay, generates voiceover and subtitles,
  builds the MP4 with FFmpeg, and generates a 1280x720 branded JPEG thumbnail from the first scene.
- The Scene Editor includes an editable thumbnail headline.
- Previews are saved under the public renders bucket, and thumbnails under public thumbnails.
- Full-resolution masters are retained as seven-day GitHub Actions artifacts ONLY when the repository is private; public repository artifacts can expose unpublished media.
- YouTube uploads default to private; YouTube thumbnails are uploaded where channel permissions allow.
- Default new-video allowance: 3 per day in Africa/Lagos, configurable with STUDIO_DAILY_LIMIT (1-20).
- A successful YouTube upload is independent of thumbnail publishing or Supabase preview availability.

## Safe retention policy
- studio_maintenance.py runs daily at 02:15 UTC through studio_maintenance.yml.
- Only a completed video with a saved YouTube URL/ID is eligible for automatic media deletion:
  preview after 7 days, thumbnail after 90 days.
- If YouTube fails, Supabase preview remains as a recovery copy, not deleted by this workflow.
- The videos database row, title, description, scenes and YouTube URL remain indefinitely.
- Manual workflow_dispatch defaults to dry-run so operators can review eligible files.
- Database and storage usage can be checked in the dashboard; the Billing dashboard is authoritative.

## Required secrets
Vercel: NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY,
STUDIO_ALLOWED_EMAILS, GEMINI_API_KEY. Optional GITHUB_TOKEN/GH_PAT for fast dispatch.
GitHub Actions: SUPABASE_URL or NEXT_PUBLIC_SUPABASE_URL; SUPABASE_SERVICE_ROLE_KEY or SUPABASE_KEY;
PIXABAY_API_KEY. Optional YOUTUBE_TOKEN_JSON enables private upload and custom thumbnail publishing.
See Studio integration probe workflow for read-only diagnostics. Never paste keys into logs or commits.
