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
import wave

from studio_retry import TemporaryRenderError

VOICE = os.getenv("EDGE_TTS_VOICE", "en-US-AndrewMultilingualNeural")
BACKUP_VOICE = os.getenv("EDGE_TTS_BACKUP_VOICE", "en-US-AndrewNeural")
RATE = os.getenv("EDGE_TTS_RATE", "-4%")
AUDIO_VERSION = "natural-phrases-v3"


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


def srt_stamp(seconds: float) -> str:
    millis = round(max(0, seconds) * 1000)
    return f"{millis // 3600000:02d}:{millis // 60000 % 60:02d}:{millis // 1000 % 60:02d},{millis % 1000:03d}"


def write_srt(cues: list[tuple[float, float, str]], path: Path) -> None:
    lines = []
    for i, (start, end, text) in enumerate(cues, 1):
        lines += [str(i), f"{srt_stamp(start)} --> {srt_stamp(max(start + .08, end))}", text.strip(), ""]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def retime_srt(source: Path, target: Path, tempo: float, duration: float) -> Path:
    def seconds(stamp: str) -> float:
        hours, minutes, seconds, millis = map(int, re.split("[:,]", stamp))
        return hours * 3600 + minutes * 60 + seconds + millis / 1000

    def retime(match: re.Match) -> str:
        start = min(seconds(match[1]) / tempo, duration - .001)
        end = min(max(start + .001, seconds(match[2]) / tempo), duration)
        return f"{srt_stamp(start)} --> {srt_stamp(end)}"

    stamp = r"\d{2}:\d{2}:\d{2},\d{3}"
    target.write_text(re.sub(rf"^({stamp}) --> ({stamp})$", retime, source.read_text(encoding="utf-8"),
                             flags=re.MULTILINE), encoding="utf-8")
    return target


async def synthesize(text: str, path: Path, voice: str = VOICE) -> list[tuple[float, float, str]]:
    import edge_tts
    boundaries = []
    communicate = edge_tts.Communicate(text, voice, rate=RATE, boundary="WordBoundary", connect_timeout=12, receive_timeout=15)
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



def synthesize_part(text: str, mp3: Path, wav: Path, voice: str) -> tuple[list, float]:
    request = synthesize(text, mp3) if voice == VOICE else synthesize(text, mp3, voice)
    boundaries = asyncio.run(asyncio.wait_for(request, 30))
    seconds = validate_audio(mp3, words=len(text.split()))
    complete_boundaries(boundaries, text, seconds)
    subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-y", "-i", str(mp3),
                    "-map", "0:a:0", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(wav)],
                   capture_output=True, check=True, timeout=30)
    return boundaries, validate_audio(wav, expected=seconds, words=len(text.split()))


def recover_in_phrases(text: str, mp3: Path, wav: Path, voice: str) -> list:
    words = text.split()
    part = wav.with_suffix(".phrase.wav")
    boundaries = []
    elapsed = 0.0
    try:
        with wave.open(str(wav), "wb") as joined:
            joined.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
            def append_phrase(phrase: list[str]) -> None:
                nonlocal elapsed
                for attempt in range(2):
                    try:
                        timings, _ = synthesize_part(" ".join(phrase), mp3, part, voice)
                        break
                    except Exception:
                        if attempt == 0:
                            time.sleep(1)
                            continue
                        if len(phrase) == 1:
                            raise
                        midpoint = len(phrase) // 2
                        append_phrase(phrase[:midpoint])
                        append_phrase(phrase[midpoint:])
                        return
                with wave.open(str(part), "rb") as audio:
                    frames = audio.getnframes()
                    joined.writeframes(audio.readframes(frames))
                boundaries.extend((elapsed + start, elapsed + end, word) for start, end, word in timings)
                elapsed += frames / 48000

            while words:
                end = next((i for i in range(min(12, len(words)), 3, -1)
                            if words[i - 1].endswith((".", ",", ";", "?", "!", ":"))), min(10, len(words)))
                append_phrase(words[:end])
                words = words[end:]
        return boundaries
    finally:
        part.unlink(missing_ok=True)

class NarrationUnavailable(TemporaryRenderError):
    """The selected neural voice failed to deliver validated audio."""


def make_audio(scene: dict, directory: Path, voice: str = VOICE) -> tuple[Path, Path]:
    number = int(scene["scene_number"])
    text = re.sub(r"\s+", " ", str(scene["narration"])).strip()
    if not text:
        raise ValueError(f"Scene {number} has no narration")
    fingerprint = hashlib.sha256(json.dumps([AUDIO_VERSION, voice, RATE, scene.get("format"), text]).encode()).hexdigest()
    audio = directory / f"scene_{number:02d}_{fingerprint[:16]}.wav"
    srt = audio.with_suffix(".srt")
    manifest = audio.with_suffix(".audio.json")
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
            if attempt < 2:
                boundaries, seconds = synthesize_part(text, temporary, decoded, voice)
            else:
                boundaries = recover_in_phrases(text, temporary, decoded, voice)
                seconds = validate_audio(decoded, words=len(text.split()))
                complete_boundaries(boundaries, text, seconds)
                if len(text.split()) >= 6:
                    print(f"[narration] scene {number} recovered with the same voice in short phrases.", flush=True)
            write_srt(phrase_cues(boundaries, seconds, scene.get("format") == "9:16"), srt)
            decoded.replace(audio)
            temporary.unlink(missing_ok=True)
            manifest.write_text(json.dumps({"fingerprint": fingerprint, "voice": voice,
                                             "rate": RATE, "seconds": seconds}))
            return audio, srt
        except Exception as error:
            temporary.unlink(missing_ok=True)
            decoded.unlink(missing_ok=True)
            reason = str(error) if isinstance(error, ValueError) else type(error).__name__
            print(f"[narration] scene {number}, attempt {attempt + 1}: {reason}", flush=True)
            if attempt == 2:
                raise NarrationUnavailable(f"Neural narration unavailable for scene {number} after automatic retries.") from error
            time.sleep(2 ** attempt)
    raise RuntimeError("Narration was not generated")
