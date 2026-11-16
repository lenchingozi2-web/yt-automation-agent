"""Regression checks for independent and consistent neural narration recovery."""
from __future__ import annotations

import base64
import io
import math
import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

from studio_audio import make_audio, validate_audio
from studio_speech_backup import approximate_boundaries, generate


def tone_wav(seconds=2.0, rate=24000):
    import array
    samples = array.array("h", [int(4000 * math.sin(2 * math.pi * 220 * n / rate))
                                for n in range(round(seconds * rate))])
    f = io.BytesIO()
    with wave.open(f, "wb") as writer:
        writer.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        writer.writeframes(samples.tobytes())
    return f.getvalue()


class NeuralVoiceFallbackTests(unittest.TestCase):
    def test_word_boundaries_remain_ordered_and_cover_every_spoken_word(self):
        phrase = "One short speech sentence, with distinct words."
        points = approximate_boundaries(phrase, 2.2)
        self.assertEqual(" ".join(item[2] for item in points), phrase)
        self.assertAlmostEqual(points[-1][1], 2.2, places=4)
        self.assertTrue(all(b[0] >= a[1] - 0.0001 for a, b in zip(points, points[1:])))

    def test_backup_generates_decodable_neural_wav_and_does_not_expose_key(self):
        payload = tone_wav()
        result = {"candidates": [{"content": {"parts": [
            {"inlineData": {"mimeType": "audio/wav", "data": base64.b64encode(payload).decode()}}
        ]}}]}
        response = Mock(status_code=200)
        response.json.return_value = result
        with (tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, {"GEMINI_API_KEY": "private-fixture", "STUDIO_GEMINI_TTS_MODEL": "gemini-2.5-flash-preview-tts"}),
            patch("studio_speech_backup.requests.post", return_value=response) as call,
        ):
            path = Path(tmp) / "voice.wav"
            points, seconds = generate("A working neural fallback gives a consistent voice.", path)
            self.assertAlmostEqual(seconds, 2, places=2)
            self.assertAlmostEqual(validate_audio(path, words=8), 2, places=2)
            self.assertEqual(len(points), 8)
            self.assertEqual(call.call_args.kwargs["headers"]["x-goog-api-key"], "private-fixture")

    def test_unavailable_backup_fails_without_silent_or_partial_audio(self):
        response = Mock(status_code=429)
        with (tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, {"GEMINI_API_KEY": "private-fixture"}),
            patch("studio_speech_backup.requests.post", return_value=response),
        ):
            with self.assertRaisesRegex(RuntimeError, "429"):
                generate("Test the voice", Path(tmp) / "voice.wav")

    def test_repeated_edge_no_audio_trips_fast_failover_circuit(self):
        class NoAudioReceived(Exception):
            pass

        async def no_audio(*args, **kwargs):
            raise NoAudioReceived("The provider returned an empty stream")

        with tempfile.TemporaryDirectory() as tmp, (
            patch("studio_audio.synthesize", side_effect=no_audio) as edge,
            patch("studio_audio.time.sleep") as sleep,
        ):
            from studio_audio import NarrationUnavailable
            with self.assertRaisesRegex(NarrationUnavailable, "Edge neural voice stopped returning audio"):
                make_audio({"scene_number": 7, "narration": "No audio is available."}, Path(tmp))
            self.assertEqual(edge.call_count, 2, "Do not waste the phrase-recovery retries on a dead provider")
            self.assertEqual(sleep.call_count, 1)

    def test_fingerprints_distinguish_providers_and_cache_only_validated_audio(self):
        call_counter = {"count": 0}
        def fake_gemini(text, target, voice):
            call_counter["count"] += 1
            target.write_bytes(tone_wav(2.0))
            return approximate_boundaries(text, 2.0), 2.0
        scene = {"scene_number": 1, "narration": "A consistent narrated sentence with a friendly speaker.", "format": "16:9"}
        with tempfile.TemporaryDirectory() as tmp, patch("studio_audio.generate_gemini_speech", side_effect=fake_gemini):
            target = Path(tmp)
            first, srt = make_audio(scene, target, "Charon", engine="gemini")
            self.assertTrue(first.exists())
            self.assertTrue(srt.exists())
            self.assertEqual(call_counter["count"], 1)
            second, _ = make_audio(scene, target, "Charon", engine="gemini")
            self.assertEqual(second, first)
            self.assertEqual(call_counter["count"], 1)
            third, _ = make_audio({**scene, "narration": "Different narration needs a fresh audio file."},
                                  target, "Charon", engine="gemini")
            self.assertNotEqual(third, first)
            self.assertEqual(call_counter["count"], 2)


if __name__ == "__main__":
    unittest.main()
