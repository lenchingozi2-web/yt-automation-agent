"""Independent Gemini neural TTS fallback when Edge's WebSocket provider cannot serve audio.

This stays server-side. It uses the configured Gemini API key, never bills by
design, and verifies duration / word coverage before the output is accepted.
"""
from __future__ import annotations

import base64
import io
import os
import re
import subprocess
import wave
from pathlib import Path

import requests

MODEL = os.getenv("STUDIO_GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")
VOICE = os.getenv("STUDIO_GEMINI_TTS_VOICE", "Charon")
MODEL_ALLOWLIST = {"gemini-2.5-flash-preview-tts", "gemini-2.5-pro-preview-tts"}
VOICE_ALLOWLIST = {"Charon", "Puck", "Kore", "Aoede", "Fenrir", "Orus", "Schedar", "Achird", "Algenib"}
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/"


def available() -> bool:
    return bool(os.getenv("GEMINI_API_KEY", "").strip())


def approximate_boundaries(text: str, seconds: float) -> list[tuple[float, float, str]]:
    """Conservative caption cues for a voice provider without word timestamps.

    These are estimates, not forced alignment: do not label them word-accurate.
    """
    words = text.split()
    if not words:
        raise ValueError("Narration text is empty")
    # Do not pad silence or omit words to satisfy a requested script length.
    # Longer words and punctuation receive slightly more of the actual time.
    weights = [max(2.0, len(re.sub(r"[^\w]", "", word)) ** .65) +
               (0.6 if word.endswith((",", ";", ":")) else 1.2 if word.endswith((".", "!", "?")) else 0)
               for word in words]
    scale = seconds / sum(weights)
    boundaries = []
    cursor = 0.0
    for word, weight in zip(words, weights):
        end = cursor + weight * scale
        boundaries.append((cursor, end, word))
        cursor = end
    return boundaries


def generate(text: str, wav: Path, voice: str = VOICE) -> tuple[list[tuple[float, float, str]], float]:
    """Generate a full neural-voice WAV with a separate, authorized provider."""
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Gemini neural narration fallback is not configured")
    if MODEL not in MODEL_ALLOWLIST or voice not in VOICE_ALLOWLIST:
        raise ValueError("Unsupported Gemini narration model or voice")
    if len(text) > 1600 or len(text.split()) > 55:
        raise ValueError("Split the spoken scene into shorter narration segments")
    payload = {
        "contents": [{"parts": [{"text": "Speak the following verbatim, using a warm, steady natural English delivery. Do not add anything or read any instructions aloud.\n\nTRANSCRIPT:\n" + text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }
    response = requests.post(
        GEMINI_ENDPOINT + MODEL + ":generateContent",
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        json=payload, timeout=90,
    )
    if response.status_code != 200:
        # Never print API keys, the raw request or personally identifying script text.
        raise RuntimeError("Gemini narration temporarily unavailable (HTTP " + str(response.status_code) + ")")
    candidates = response.json().get("candidates") or []
    parts = (candidates[0].get("content") or {}).get("parts") or [] if candidates else []
    encoded = next((part.get("inlineData") for part in parts if part.get("inlineData")), None)
    if not encoded or not encoded.get("data"):
        raise RuntimeError("Gemini narration did not return an audio payload")
    raw = base64.b64decode(encoded["data"], validate=True)
    if len(raw) < 1500:
        raise ValueError("Gemini returned an incomplete audio segment")
    kind = (encoded.get("mimeType") or "audio/L16").lower()
    if raw[:4] == b"RIFF" or "wav" in kind:
        source = wav.with_suffix(".gemini-source.wav")
        source.write_bytes(raw)
        command = ["ffmpeg", "-y", "-v", "error", "-xerror", "-i", str(source)]
    elif "l16" in kind or "pcm" in kind:
        if len(raw) % 2:
            raise ValueError("Gemini returned invalid PCM samples")
        source = None
        command = ["ffmpeg", "-y", "-v", "error", "-xerror", "-f", "s16le",
                   "-ar", "24000", "-ac", "1", "-i", "pipe:0"]
    else:
        raise ValueError("Unsupported Gemini narration audio format")
    try:
        command += ["-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)]
        result = subprocess.run(command, input=raw if source is None else None,
                                capture_output=True, timeout=60)
        if result.returncode:
            raise ValueError("Gemini narration could not be decoded")
        with wave.open(str(wav), "rb") as track:
            seconds = track.getnframes() / track.getframerate()
        if not 0.5 <= seconds <= 120 or seconds < len(text.split()) / 6:
            raise ValueError("Gemini narration was empty or truncated")
        return approximate_boundaries(text, seconds), seconds
    finally:
        if source:
            source.unlink(missing_ok=True)
