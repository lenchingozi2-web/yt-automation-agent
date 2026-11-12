"""Consistent neural narration, atomic caching and audio quality checks."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

VOICE = os.getenv("EDGE_TTS_VOICE", "en-US-AndrewMultilingualNeural")
RATE = os.getenv("EDGE_TTS_RATE", "-4%")
AUDIO_VERSION = "natural-phrases-v2"


def probe(path: Path) -> dict:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                             "-of", "json", str(path)], capture_output=True, text=True,
                            check=True, timeout=30)
    return json.loads(result.stdout)


def validate_audio(path: Path, expected: float | None = None, words: int = 0) -> float:
    info = probe(path)
    streams = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    if not streams:
        raise ValueError("Narration audio stream is missing")
    seconds = float(streams[0].get("duration") or info["format"]["duration"])
    if seconds < max(0.25, words / 6):
        raise ValueError("Narration is empty or truncated")
    if expected is not None and abs(seconds - expected) > max(0.35, expected * .02):
        raise ValueError(f"Narration length mismatch: {seconds:.2f}s vs {expected:.2f}s")
    result = subprocess.run(["ffmpeg", "-hide_banner", "-v", "info", "-xerror", "-i", str(path),
                             "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"],
                            capture_output=True, text=True, timeout=180)
    match = re.search(r"mean_volume: ([-\w.]+) dB", result.stderr)
    if result.returncode or not match or float(match[1]) < -55:
        raise ValueError("Narration failed decoding or is silent")
    return seconds


def write_srt(cues: list[tuple[float, float, str]], path: Path) -> None:
    def stamp(seconds: float) -> str:
        millis = round(max(0, seconds) * 1000)
        return f"{millis // 3600000:02d}:{millis // 60000 % 60:02d}:{millis // 1000 % 60:02d},{millis % 1000:03d}"
    lines = []
    for i, (start, end, text) in enumerate(cues, 1):
        lines += [str(i), f"{stamp(start)} --> {stamp(max(start + .08, end))}", text.strip(), ""]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def synthesize(text: str, path: Path) -> list[tuple[float, float, str]]:
    import edge_tts
    boundaries = []
    communicate = edge_tts.Communicate(text, VOICE, rate=RATE, boundary="WordBoundary")
    with path.open("wb") as handle:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                handle.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 10_000_000
                boundaries.append((start, start + chunk["duration"] / 10_000_000, chunk["text"]))
    return boundaries


def phrase_cues(boundaries: list[tuple[float, float, str]], seconds: float, portrait: bool = False):
    """Keep captions timed to speech, breaking on clauses and natural pauses."""
    lines = []
    group = []
    limit = 31 if portrait else 44
    for word in boundaries:
        if not word[2].strip() or word[0] >= seconds:
            continue
        if group:
            phrase = " ".join(part[2] for part in group)
            boundary = phrase.endswith((".", "!", "?", ",", ":", "—")) and len(group) >= 2
            if (boundary or word[0] - group[-1][1] > .36 or
                    len(phrase) + len(word[2]) + 1 > limit or word[1] - group[0][0] > 2.8):
                lines.append(group)
                group = []
        group.append(word)
    if group:
        lines.append(group)
    cues = []
    for index, group in enumerate(lines):
        end = lines[index + 1][0][0] if index + 1 < len(lines) else seconds
        text = " ".join(part[2] for part in group)
        text = re.sub(r"\s+([,.;:!?%])", r"\1", text)
        cues.append((group[0][0], min(end, group[-1][1] + .10), text))
    return cues


def complete_boundaries(boundaries: list[tuple[float, float, str]], text: str, seconds: float) -> None:
    def characters(value):
        return len(re.findall(r"\w", value))
    expected = characters(text)
    reported = sum(characters(word[2]) for word in boundaries)
    if (not boundaries or reported < expected * .73 or
            boundaries[-1][1] > seconds + .45 or seconds - boundaries[-1][1] > 2.5 or
            any(second[0] < first[0] for first, second in zip(boundaries, boundaries[1:]))):
        raise ValueError("Neural narration has incomplete or inconsistent word timings")


def make_audio(scene: dict, directory: Path) -> tuple[Path, Path]:
    number = int(scene["scene_number"])
    text = re.sub(r"\s+", " ", str(scene["narration"])).strip()
    if not text:
        raise ValueError(f"Scene {number} has no narration")
    audio = directory / f"scene_{number:02d}.wav"
    srt = audio.with_suffix(".srt")
    manifest = audio.with_suffix(".audio.json")
    fingerprint = hashlib.sha256(json.dumps([AUDIO_VERSION, VOICE, RATE, scene.get("format"), text]).encode()).hexdigest()
    if manifest.exists() and audio.exists() and srt.exists():
        try:
            if json.loads(manifest.read_text())["fingerprint"] == fingerprint:
                validate_audio(audio, words=len(text.split()))
                return audio, srt
        except (ValueError, KeyError, OSError, subprocess.SubprocessError):
            pass
    temporary = audio.with_suffix(".part.mp3")
    decoded = audio.with_suffix(".part.wav")
    for attempt in range(3):
        try:
            boundaries = asyncio.run(asyncio.wait_for(synthesize(text, temporary), 90))
            seconds = validate_audio(temporary, words=len(text.split()))
            complete_boundaries(boundaries, text, seconds)
            subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-y", "-i", str(temporary),
                            "-map", "0:a:0", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(decoded)],
                           capture_output=True, check=True, timeout=30)
            seconds = validate_audio(decoded, expected=seconds, words=len(text.split()))
            write_srt(phrase_cues(boundaries, seconds, scene.get("format") == "9:16"), srt)
            decoded.replace(audio)
            temporary.unlink(missing_ok=True)
            manifest.write_text(json.dumps({"fingerprint": fingerprint, "voice": VOICE,
                                             "rate": RATE, "seconds": seconds}))
            return audio, srt
        except Exception as error:
            temporary.unlink(missing_ok=True)
            decoded.unlink(missing_ok=True)
            if attempt == 2:
                raise RuntimeError(f"Neural narration unavailable for scene {number}; retry queued job") from error
            time.sleep(2 ** attempt)
    raise RuntimeError("Narration was not generated")
