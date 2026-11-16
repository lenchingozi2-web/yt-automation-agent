# LenxiQ Studio — autonomous YouTube production

The Next.js dashboard queues video jobs in Supabase, while a GitHub Actions Python worker generates speech, footage, captions, thumbnails and full-quality video files. Uploading to YouTube requires an explicit review and approval.

## Narration stack (November 2026)

**Primary: Kokoro ONNX CPU (offline).** This is a local speech model on the GitHub Actions worker, not a paid speech API. No Gemini or Edge TTS requests are made during successful Kokoro narration. The default voice is `am_michael`, an American English male speaker. The FP16 Kokoro model and voice embeddings are downloaded once from the upstream release, verified using hardcoded SHA-256 checksums, and cached between Actions runs.

**Fallback: Edge TTS, then Gemini neural speech.** These are only attempted if offline Kokoro is unavailable or fails. If the provider changes, Studio regenerates the *entire narration track* with one speaker; it does not insert visibly different voices halfway through a video. Both online providers have their own service limitations.

The upstream Kokoro ONNX Python implementation is MIT licensed, and its model is Apache-2.0 licensed. See [thewh1teagle/kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) for the official package and model release.

### Worker setup

The normal `studio_worker.yml` automatically installs `requirements-kokoro.txt`, restores the cached model in `.kokoro-models/`, downloads missing assets with verification, and checks queued jobs before doing any heavy installation.

To verify manually on a CPU machine with Python 3.11 and FFmpeg:

```bash
pip install -r requirements.txt -r requirements-kokoro.txt
python studio_kokoro.py --prepare
python studio_kokoro.py --smoke
```

The smoke check synthesizes real speech, verifies non-silence and intact captions, and checks longer multi-fragment narration. It does not create, delete or upload a Studio production.

To configure a different single voice for future videos, set `STUDIO_KOKORO_VOICE` in the **worker environment** to one of the supported Kokoro English voices, for example `af_heart`, `af_bella`, `bf_emma`, `am_michael`, or `am_fenrir`. `STUDIO_KOKORO_SPEED` defaults to `1.0` (supported `0.85–1.15`). Do not configure a voice that changes mid-video.

### Review and retry

A script is preserved when generation fails, and an unfinished video does not upload automatically. After a narration failure, select **Retry render** on the same production. Do not delete and paste the script again. A new GitHub Actions worker will use Kokoro as its primary voice when the checked model is present. Finished videos retain one manual YouTube approval and the private full-resolution master; preview files may be smaller for browser playback.

**Limitations:** Offline voice synthesis uses GitHub Actions CPU time and storage caching even though it does not use API speech credits. Full-length videos still depend on available media storage and rendering capacity. Model voice quality is subjective; the smoke check validates audio output and timing rather than guaranteeing every word is perfectly pronounced.
