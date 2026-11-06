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
RATE = os.getenv("EDGE_TTS_RATE", "-2%")


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
    result = subprocess.run(["ffmpeg", "-hide_banner", "-v", "info", "-i", str(path),
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


def make_audio(scene: dict, directory: Path) -> tuple[Path, Path]:
    number = int(scene["scene_number"])
    text = re.sub(r"\s+", " ", str(scene["narration"])).strip()
    if not text:
        raise ValueError(f"Scene {number} has no narration")
    audio = directory / f"scene_{number:02d}.mp3"
    srt = audio.with_suffix(".srt")
    manifest = audio.with_suffix(".audio.json")
    fingerprint = hashlib.sha256((VOICE + RATE + text).encode()).hexdigest()
    if manifest.exists() and audio.exists() and srt.exists():
        try:
            if json.loads(manifest.read_text())["fingerprint"] == fingerprint:
                validate_audio(audio, words=len(text.split()))
                return audio, srt
        except (ValueError, KeyError, OSError, subprocess.SubprocessError):
            pass
    temporary = audio.with_suffix(".part.mp3")
    for attempt in range(3):
        try:
            boundaries = asyncio.run(asyncio.wait_for(synthesize(text, temporary), 90))
            seconds = validate_audio(temporary, words=len(text.split()))
            cues = []
            # Preserve actual word timing, grouping short readable captions.
            for start in range(0, len(boundaries), 5):
                group = boundaries[start:start + 5]
                cues.append((group[0][0], min(seconds, group[-1][1]), " ".join(x[2] for x in group)))
            if not cues:
                words = text.split()
                groups = [" ".join(words[i:i + 5]) for i in range(0, len(words), 5)]
                cues = [(i * seconds / len(groups), (i + 1) * seconds / len(groups), x)
                        for i, x in enumerate(groups)]
            write_srt(cues, srt)
            temporary.replace(audio)
            manifest.write_text(json.dumps({"fingerprint": fingerprint, "voice": VOICE,
                                             "rate": RATE, "seconds": seconds}))
            return audio, srt
        except Exception as error:
            temporary.unlink(missing_ok=True)
            if attempt == 2:
                raise RuntimeError(f"Neural narration unavailable for scene {number}; retry queued job") from error
            time.sleep(2 ** attempt)
    raise RuntimeError("Narration was not generated")
