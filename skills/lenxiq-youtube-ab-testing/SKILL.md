---
name: lenxiq-youtube-ab-testing
version: 1.0.0
description: Create, audit and interpret factual YouTube title/thumbnail A/B/C testing packages for LenxiQ Studio based on the actual video content and native YouTube Studio experiment results.
---

# LenxiQ YouTube A/B Testing Skill

## When to activate
Use on user requests to improve YouTube titles, thumbnails, video packaging, native A/B tests, underperforming videos, and interpretation of experiment results. This is an instruction skill; it does NOT silently modify YouTube videos or start native experiments.

## Inputs (use whatever is available)
- Video ID, working title, full script/transcript, description, chapters, citations, duration, actual footage and current thumbnail.
- Channel audience, content language and YouTube format (long-form vs Short).
- Optional: YouTube Studio title/thumbnail A/B settings, past native experiment results and analytics.
If only a title or thumbnail is available, explicitly mark all candidates as METADATA-ONLY / PROVISIONAL. Never claim to have reviewed a script that is not accessible.

## Process
1. Identify the video's true audience promise and 2–4 real differentiators. For medical/scientific topics, distinguish established facts from emerging claims and assess whether the narration actually supports the hook. Detect title/thumbnail contradictions.
2. Choose test mode. Default to TITLE ONLY (hold thumbnail unchanged) unless user asks for THUMBNAIL ONLY or PAIRED TITLE + THUMBNAIL. In paired mode output exactly 3 matched pairs, not a 3×3 grid. Explain that a winning pair does not identify which component caused the result.
3. Generate exactly three DIFFERENT hypotheses:
   - A / CLEAR AND SEARCHABLE: direct evergreen description, high-intent keyword near the front. Put the safest option first because YouTube may retain the first uploaded version if results are inconclusive.
   - B / GENUINE CURIOSITY: precise open question, tension or surprising angle explicitly addressed inside the video.
   - C / VIEWER VALUE: what the viewer learns or why the content matters; honest stakes without panic or exaggeration.
   Do not merely reword the same angle three times.
4. Quality gate every title: no more than 100 characters (YouTube's limit), preferably concise and front-loaded for mobile, no promises not delivered by the video, no false certainty, no unverified superlatives, no fake dates, no misleading clinical promises, no deceptive clickbait. Do not use 'I built' unless the creator really built the system.
5. For thumbnail tests, create three differentiated BRIEFS with authentic subject, high-contrast focal point, optional 0–4 word overlay, mobile-readable composition and matching video promise. Prefer a 1280×720 16:9 image. A brief is not a rendered thumbnail. Do not copy a recognizable creator's thumbnail or misrepresent clinical evidence.
6. Return a copy-ready A/B/C package using the output contract below. Do not assign made-up numerical probabilities of a variant winning.

## Output contract
VIDEO: <exact video title/ID>
TEST MODE: Title only | Thumbnail only | Title and thumbnail
SOURCE REVIEW: Script-reviewed | Metadata-only — provisional
A (Clear/Search): <copy-ready title or thumbnail brief>
B (Curiosity): <copy-ready title or thumbnail brief>
C (Value/Stakes): <copy-ready title or thumbnail brief>
HYPOTHESES: <one line explaining each distinct motivation>
TITLE/THUMBNAIL ALIGNMENT: <pass, or conflict to resolve>
CLAIM SAFETY: <pass or specific claims to check against script>
NEXT STEP: <safe native YouTube Studio test setup or next observation>

For batch requests, repeat the full output independently for every video; never compare variants between different videos.

## Interpret real YouTube A/B test data
Native YouTube Studio (not this skill) runs true concurrent experiments and uses WATCH-TIME SHARE, not CTR alone, to select a winner. Interpret only actual provided results:
- WINNER: preserve native winner unless it needs factual/policy correction; document what worked.
- PERFORMED SAME: no proven advantage; choose clear, truthful, evergreen packaging as an editorial judgment.
- INCONCLUSIVE: no clear statistical result; do not claim a winner, and consider retaining default A.
- IN PROGRESS: no early winner; avoid editing title or thumbnail because it automatically cancels the test.
- UNAVAILABLE: explain why; provide manual packaging ideas without calling it a native A/B experiment.
Keep log fields: video_id, mode, variant A/B/C, transcript/source status, experiment start/end if known, final native status, official watch-time share if provided, and next hypothesis. Never invent impressions, watch time, statistical significance or audience composition.

## Current official YouTube rules (verified November 17, 2026)
- Up to THREE title and/or thumbnail variants, including paired variants.
- A/B testing is offered through YouTube Studio's desktop interface with advanced features enabled. Mobile creators may try a desktop-site browser view but that may be inconvenient.
- Not supported for Shorts, scheduled livestreams, pre-conversion Premieres, private videos, made-for-kids videos, or mature/age-restricted videos. Live archives can be eligible.
- Test may run from a few days up to two weeks depending on impressions.
- Editing a video's title or thumbnail while a test runs stops that test.
- Native winner is based on watch-time share and differs from sequential thumbnail/title rotations or CTR-only comparisons.
- If there is no winner, the first-uploaded title/thumbnail can be the default.
- For thumbnail tests prefer high-resolution 1280×720 assets; smaller images can lower all test thumbnails' processing resolution.
Official reference: https://support.google.com/youtube/answer/16391400

## Critical boundaries
- Do not invent YouTube Data API endpoints for native A/B test creation/results.
- Do not upload, overwrite titles, modify live videos, buy promotion, or publish without an authorized action.
- Do not confuse A/B testing with paid YouTube Promote.
- No growth guarantees. On a low-traffic channel many tests may be inconclusive.

## Example (provisional without script review)
Input: Video covers surgeon-supervised robot-assisted procedures and the limits of autonomy.
A: Autonomous Surgery Explained: What AI Can Actually Do Today
B: Can a Robot Really Operate Without a Surgeon?
C: AI in the Operating Room: Progress, Risks and the Human Role
Safety: Do not imply that all surgery is fully autonomous.
