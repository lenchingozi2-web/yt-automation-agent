# LenxiQ Studio worker

`worker.py` is intentionally not deployed to Vercel. It polls the Supabase `videos` table for `rendering` jobs, checkpoints each scene under `output_run/<video-id>/`, uses edge-tts with a Kokoro/faster-whisper CPU fallback, downloads portrait 720p+ clips from Pexels, renders with FFmpeg, uploads privately to YouTube, and marks the row `completed`.

## One-time setup

1. Apply `LensiQ/supabase/migrations/0046_video_studio.sql` to the LensiQ Supabase project.
2. Install FFmpeg and Python dependencies with `pip install -r requirements.txt`.
3. Set `NEXT_PUBLIC_SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, and `PEXELS_API_KEY` locally. Set `YOUTUBE_TOKEN_JSON` for unattended GitHub Actions runs.
4. For the first local OAuth setup only, set `YOUTUBE_CLIENT_SECRET_JSON` to the contents of the Google `client_secret.json`; run the worker once interactively and keep the generated `.youtube-token.json` private. GitHub Actions cannot complete an interactive OAuth consent flow, so the refreshed authorized token must be stored as the `YOUTUBE_TOKEN_JSON` repository secret.
5. For Kokoro fallback, provide `KOKORO_MODEL` and `KOKORO_VOICES` model files locally or as GitHub Actions secrets/artifacts.

## Run

```bash
python worker.py --once   # process one rendering job
python worker.py          # poll continuously
```

The worker retries neither provider calls nor uploads blindly. Existing audio, subtitles, raw clips, rendered scenes, and final output are reused on restart. Failed rows are marked `failed` with a bounded error message.
