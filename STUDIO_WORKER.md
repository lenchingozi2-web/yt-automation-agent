# Studio operations

The dashboard and scheduled producer use one Supabase `videos` queue and one FFmpeg renderer.

- Daily production: 08:17 UTC (09:17 Nigeria), with retries at 10:17 and 12:17 UTC. A deterministic daily job ID prevents repeated runs from generating duplicate videos. Default: one 60-second portrait video per day within the existing three-video daily allowance. GitHub schedules can start late.
- The daily producer dispatches the render workflow with its short-lived repository token, so queue polling does not cancel scheduled production. Daily topics use the LenxiQ brand pillars and exclude recent video titles. Generation uses current Groq production chat models for speed, with available Gemini text models as fallback. There are no interactive approvals or repository history commits in CI.
- Manual Generate video saves the job on the server before responding. Review mode opens the scene editor. The worker checks the queue on its fallback schedule; an optional Vercel GH_PAT/GITHUB_TOKEN with Actions write access enables immediate dispatch.
- Narration: `en-US-AndrewMultilingualNeural`, rate `-2%`, configurable with EDGE_TTS_VOICE/EDGE_TTS_RATE. Three attempts use the same neural voice. No gTTS fallback or silent substitution. Atomic audio caching includes text, voice and rate. Real word boundaries drive five-word captions.
- Visuals: exclusively Pixabay video, new shots about every five seconds, clip ID deduplication, subtle camera movement. Long pasted narration is split without deleting words. No blank canvas or unrelated generated image fallback.
- Rendering explicitly maps narration and excludes footage audio. Every scene, joined master and compressed preview is checked for audio, duration, decoding and silence. Audio uses stereo AAC at 48 kHz. MP4s include faststart metadata for streaming.
- Finished MP4s and branded thumbnails are saved in Supabase. Rendering completion and YouTube upload status are independent. YouTube defaults to private; thumbnails are applied where the channel permits them.
- Upload failures retain the MP4. Transient failures back off, with at most five automatic attempts. Expired/revoked OAuth sets `auth_required`, shown in the dashboard. After renewing YOUTUBE_TOKEN_JSON in GitHub repository secrets, Retry YouTube upload reuses the saved MP4.
- Worker drains up to three queued videos per run. Failed renders retry up to three times. Heartbeats recover interrupted jobs after two hours. Failed rendering exits nonzero so Actions reports the error.
- Schema compatibility: operational metadata lives in the first scene's `_studio` JSON when dedicated columns do not exist. No unrelated Supabase database is modified.

## Storage and retention

Previews stay below the Supabase Free 50 MB object limit; new jobs reserve space below 800 MiB in the render bucket. Only media with a saved YouTube copy can expire: MP4 after seven days, thumbnail after 90 days. Unpublished media is retained. Rows and scripts remain. Public renders/thumbnails buckets retain the existing application's access model. Masters are uploaded as Actions artifacts only for private repositories; this repository is public.

## Configuration

Vercel: NEXT_PUBLIC_SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY, STUDIO_ALLOWED_EMAILS, GEMINI_API_KEY. Optional GH_PAT/GITHUB_TOKEN for immediate worker dispatch.

GitHub: NEXT_PUBLIC_SUPABASE_URL or SUPABASE_URL; SUPABASE_SERVICE_ROLE_KEY or SUPABASE_KEY; PIXABAY_API_KEY; GEMINI_API_KEY and/or GROQ_API_KEY. YOUTUBE_TOKEN_JSON enables private upload. Set STUDIO_OWNER_USER_ID as a repository variable only if the existing queue does not identify a single owner.

`studio_health.py` checks the actual schema, storage, credential refresh and aggregate production state without printing credentials or row contents. `studio_smoke.py` creates a disposable two-scene video, validates audio and stored media, and deletes its test row/files; it never uploads to YouTube.

## Validation

Run npm lint/build, Node tests in tests/studio.test.ts, Python unittest discovery for test_pipeline.py, and the thumbnail/retention checks. Media tests use stereo stock audio against mono narration and assert actual output frequencies and visual cuts. Run the real render smoke workflow for cloud credentials and providers.
