"""Offline, quota-free Kokoro ONNX narration for the Studio rendering worker.

Model and voices are pinned to the signed upstream model-files-v1.1 release.
The FP16 model is used for narration quality. On a worker, --prepare populates
the cached assets; the normal synthesis path makes no model API requests.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import sys
import tempfile
import time
import urllib.request
import wave
from functools import lru_cache
from pathlib import Path

MODEL_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/"
FILES = {
    "kokoro-v1.0.fp16.onnx": ("f3a290d384fbb27966d462905c71a46cef9e5fd00516b40df32a0b4afe77ac96", 120_000_000),
    "voices-v1.0.bin": ("bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d", 20_000_000),
}
DEFAULT_VOICE = "am_michael"
DEFAULT_SPEED = 1.0
VOICE_ALLOWLIST = {
    "af_heart", "af_bella", "af_sarah", "af_nicole", "af_aoede", "af_kore",
    "am_michael", "am_fenrir", "am_puck", "bf_emma", "bf_isabella", "bm_george",
}
ROOT = Path(os.getenv("STUDIO_KOKORO_MODEL_DIR", ".kokoro-models")).expanduser()
MAX_FRAGMENT_WORDS = 58


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while fragment := stream.read(1024 * 1024):
            h.update(fragment)
    return h.hexdigest()


def assets_valid(directory: Path = ROOT) -> bool:
    return all(
        (directory / name).is_file()
        and (directory / name).stat().st_size >= smallest
        and _digest(directory / name) == expected
        for name, (expected, smallest) in FILES.items()
    )


def _download(name: str, directory: Path, *, retries: int = 3) -> None:
    expected, minimum = FILES[name]
    target = directory / name
    if target.exists() and target.stat().st_size >= minimum and _digest(target) == expected:
        return
    directory.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        temporary = target.with_suffix(target.suffix + ".download")
        try:
            # Only the hardcoded upstream release is ever downloaded.
            request = urllib.request.Request(MODEL_BASE + name, headers={"User-Agent": "LenxiQ-Kokoro/1.0"})
            with urllib.request.urlopen(request, timeout=90) as response, temporary.open("wb") as dst:
                while chunk := response.read(1024 * 1024):
                    dst.write(chunk)
            if temporary.stat().st_size < minimum or _digest(temporary) != expected:
                raise ValueError("Kokoro model asset failed checksum validation")
            temporary.replace(target)
            print(f"[kokoro] verified model asset {name}", flush=True)
            return
        except Exception:
            temporary.unlink(missing_ok=True)
            if attempt == retries - 1:
                raise RuntimeError("Could not download the verified Kokoro model from its upstream release")
            time.sleep(2 ** attempt)


def prepare(directory: Path = ROOT) -> None:
    for name in FILES:
        _download(name, directory)
    if not assets_valid(directory):
        raise RuntimeError("Kokoro model integrity verification failed")
    print("[kokoro] full-quality local model and voices verified", flush=True)


def available(directory: Path = ROOT) -> bool:
    # Asset verification is done once per worker start, not before every scene.
    try:
        return assets_valid(directory)
    except (OSError, ValueError):
        return False


@lru_cache(maxsize=1)
def _model(directory: str):
    try:
        from kokoro_onnx import Kokoro
    except ImportError as error:
        raise RuntimeError("Offline Kokoro dependencies are missing from the render worker") from error
    root = Path(directory)
    if not assets_valid(root):
        raise RuntimeError("Offline Kokoro assets are missing or corrupted")
    # Constrain internal CPU parallelism so FFmpeg stays responsive.
    try:
        import onnxruntime as ort
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, min(4, os.cpu_count() or 2))
        options.inter_op_num_threads = 1
        return Kokoro(str(root / "kokoro-v1.0.fp16.onnx"), str(root / "voices-v1.0.bin"),
                      sess_options=options)
    except TypeError:
        # Older wheel versions do not expose SessionOptions.
        return Kokoro(str(root / "kokoro-v1.0.fp16.onnx"), str(root / "voices-v1.0.bin"))


def _fragments(text: str, max_words: int = MAX_FRAGMENT_WORDS) -> list[str]:
    words = text.split()
    if not words:
        raise ValueError("A Kokoro scene cannot have empty narration")
    output: list[str] = []
    cursor = 0
    while cursor < len(words):
        end = min(cursor + max_words, len(words))
        if end < len(words):
            for i in range(end - 1, max(cursor + 15, end - 16), -1):
                if re.search(r"[.!?][\"')\]]*$", words[i]):
                    end = i + 1
                    break
        output.append(" ".join(words[cursor:end]))
        cursor = end
    return output


def _boundaries(words: list[str], seconds: float, timings=None) -> list[tuple[float, float, str]]:
    """Use timed phoneme groups if they correspond to all words; otherwise
    preserve complete captions with conservative weighted timing estimates.
    """
    if timings:
        spans = []
        start = None
        end = 0.0
        for t in [*timings, None]:
            if t is not None and str(getattr(t, "phoneme", "")) != " ":
                start = float(t.start) if start is None else start
                end = float(t.end)
            elif start is not None:
                spans.append((start, end))
                start = None
        if (len(spans) == len(words) and all(0 <= a < b <= seconds + .1 for a, b in spans)
                and all(b[0] >= a[0] for a, b in zip(spans, spans[1:]))):
            return [(a, b, word) for (a, b), word in zip(spans, words)]
    # Timing is approximate here; actual audio is still checked for non-silence.
    from studio_speech_backup import approximate_boundaries
    return approximate_boundaries(" ".join(words), seconds)


def generate(text: str, wav: Path, voice: str = DEFAULT_VOICE,
             *, directory: Path = ROOT) -> tuple[list[tuple[float, float, str]], float]:
    if voice not in VOICE_ALLOWLIST:
        raise ValueError("Unsupported Kokoro narration voice")
    if not text.strip():
        raise ValueError("Empty narration")
    import numpy as np

    try:
        speed = float(os.getenv("STUDIO_KOKORO_SPEED", str(DEFAULT_SPEED)))
    except ValueError:
        speed = DEFAULT_SPEED
    if not .85 <= speed <= 1.15:
        raise ValueError("Kokoro narration speed must remain between 0.85 and 1.15")
    model = _model(str(directory))
    spoken = []
    all_timings = []
    sample_rate = 0
    elapsed = 0.0
    for index, fragment in enumerate(_fragments(text)):
        try:
            audio, rate, timings = model.create_timed(
                fragment, voice=voice, speed=speed, lang="en-us",
                sentence_pause=.22, clause_pause=.08, continuous=True,
            )
        except Exception as error:
            raise RuntimeError(f"Offline Kokoro failed synthesizing narration fragment {index + 1}") from error
        audio = np.asarray(audio, dtype=np.float32).flatten()
        if rate < 16000 or not len(audio) or not np.isfinite(audio).all() or float(np.max(np.abs(audio))) < .001:
            raise ValueError("Offline Kokoro returned empty or invalid speech")
        if sample_rate and sample_rate != rate:
            raise ValueError("Offline Kokoro sample rate changed across scene fragments")
        sample_rate = int(rate)
        if index:
            # One subtle between-fragment pause. No voice switching.
            silence = np.zeros(int(sample_rate * .1), dtype=np.float32)
            spoken.append(silence)
            elapsed += len(silence) / sample_rate
        fragment_seconds = len(audio) / sample_rate
        # Per-fragment timing avoids mapping multi-fragment phonemes to a
        # single gigantic word span.
        for a, b, word in _boundaries(fragment.split(), fragment_seconds, timings):
            all_timings.append((elapsed + a, elapsed + b, word))
        spoken.append(audio)
        elapsed += fragment_seconds
    joined = np.concatenate(spoken)
    joined = np.clip(joined, -1.0, 1.0)
    if len(joined) < len(text.split()) * sample_rate / 6:
        raise ValueError("Kokoro output appears truncated")
    wav.parent.mkdir(parents=True, exist_ok=True)
    temporary = wav.with_suffix(".kokoro.tmp.wav")
    try:
        with wave.open(str(temporary), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            output.writeframes((joined * 32767).astype("<i2").tobytes())
        temporary.replace(wav)
    finally:
        temporary.unlink(missing_ok=True)
    return all_timings, len(joined) / sample_rate


def smoke() -> None:
    from studio_audio import validate_audio
    phrase = ("Welcome back. Today we are building an intelligent video studio "
              "that works offline, sounds natural, and keeps every word in the script.")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "sample.wav"
        start = time.monotonic()
        marks, seconds = generate(phrase, path)
        validate_audio(path, words=len(phrase.split()))
        assert " ".join(word for _, _, word in marks) == phrase
        assert seconds > 3
        print(f"[kokoro] PASS: {seconds:.1f}s of non-silent narration in {time.monotonic()-start:.1f}s", flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"--prepare", "--smoke"}:
        raise SystemExit("Use --prepare to verify assets or --smoke to synthesize one speech sample")
    if sys.argv[1] == "--prepare":
        prepare()
    else:
        smoke()
