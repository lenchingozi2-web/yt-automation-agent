"""Unit tests for offline Kokoro speech; no provider or model downloads needed."""
from __future__ import annotations

import hashlib
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio_kokoro import _boundaries, _fragments, assets_valid, voice_profile, speech_speed
from studio_audio import make_audio, validate_audio


class FakeKokoro:
    def __init__(self):
        self.calls = []

    def create_timed(self, text, *, voice, speed, lang, **kwargs):
        import numpy as np
        self.calls.append((text, voice, lang, speed))
        seconds = max(2.8, len(text.split()) / 2.5)
        samples = np.array([.18 * math.sin(2 * math.pi * 160 * n / 24000)
                            for n in range(round(seconds * 24000))], dtype="float32")
        return samples, 24000, []


class LocalKokoroTests(unittest.TestCase):
    def test_voice_fragments_preserve_the_complete_script(self):
        passage = " ".join("Narration number %s explains the problem clearly." % i for i in range(40))
        fragments = _fragments(passage)
        self.assertGreater(len(fragments), 1)
        self.assertEqual(" ".join(fragments), passage)
        self.assertTrue(all(len(fragment.split()) <= 58 for fragment in fragments))

    def test_timed_captions_cover_all_words_even_if_phonemes_are_missing(self):
        passage = "A reliable narrator explains every detail, without losing context."
        results = _boundaries(passage.split(), 4.0, [])
        self.assertEqual(" ".join(piece[2] for piece in results), passage)
        self.assertAlmostEqual(results[-1][1], 4.0, places=4)

    def test_model_assets_validate_hashes_before_use(self):
        import studio_kokoro
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = studio_kokoro.FILES
            studio_kokoro.FILES = {
                "voice.bin": (hashlib.sha256(b"voice-data").hexdigest(), 4),
                "model.onnx": (hashlib.sha256(b"fake-model").hexdigest(), 4),
            }
            try:
                (root / "voice.bin").write_bytes(b"voice-data")
                (root / "model.onnx").write_bytes(b"fake-model")
                self.assertTrue(assets_valid(root))
                (root / "model.onnx").write_bytes(b"fake-model-corrupted")
                self.assertFalse(assets_valid(root))
            finally:
                studio_kokoro.FILES = original

    def test_kokoro_one_scene_audio_and_cache_are_consistent(self):
        model = FakeKokoro()
        scene = {"scene_number": 3, "narration": "Kokoro is speaking a complete sentence for this tutorial.",
                 "format": "16:9"}
        with tempfile.TemporaryDirectory() as tmp, patch("studio_kokoro._model", return_value=model):
            root = Path(tmp)
            audio, captions = make_audio(scene, root, "am_michael", engine="kokoro")
            self.assertGreater(validate_audio(audio), 2.5)
            self.assertTrue(captions.exists())
            self.assertIn("Kokoro is speaking", captions.read_text())
            audio_2, captions_2 = make_audio(scene, root, "am_michael", engine="kokoro")
            self.assertEqual(audio_2, audio)
            self.assertEqual(captions_2, captions)
            self.assertEqual(len(model.calls), 1)
            self.assertEqual(model.calls[0][1], "am_michael")
            self.assertEqual(model.calls[0][2], "en-us")

    def test_faster_voice_settings_invalidate_previous_audio_cache(self):
        self.assertAlmostEqual(speech_speed(), 1.0, places=2)
        self.assertIn("speed=1.000", voice_profile())
        model = FakeKokoro()
        scene = {"scene_number": 18, "narration": "A slightly faster but consistent narrator.", "format": "16:9"}
        with tempfile.TemporaryDirectory() as tmp, patch("studio_kokoro._model", return_value=model):
            first, _ = make_audio(scene, Path(tmp), "am_michael", engine="kokoro")
            with patch.dict("os.environ", {"STUDIO_KOKORO_SPEED": "1.12"}):
                second, _ = make_audio(scene, Path(tmp), "am_michael", engine="kokoro")
                self.assertIn("speed=1.120", voice_profile())
            self.assertNotEqual(first, second)
            self.assertEqual(len(model.calls), 2)

    def test_same_voice_is_reused_across_long_scene_fragments(self):
        model = FakeKokoro()
        passage = " ".join("Each numbered example builds on the last." for _ in range(24))
        scene = {"scene_number": 4, "narration": passage, "format": "16:9"}
        with tempfile.TemporaryDirectory() as tmp, patch("studio_kokoro._model", return_value=model):
            audio, captions = make_audio(scene, Path(tmp), "am_michael", engine="kokoro")
            self.assertGreater(len(model.calls), 1)
            self.assertEqual({call[1] for call in model.calls}, {"am_michael"})
            self.assertIn("Each numbered example", captions.read_text())
            self.assertGreater(validate_audio(audio, words=len(passage.split())), 20)

    def test_british_voice_keeps_its_native_pronunciation(self):
        from studio_kokoro import generate
        model = FakeKokoro()
        with tempfile.TemporaryDirectory() as tmp, patch("studio_kokoro._model", return_value=model):
            generate("The teacher explains this example clearly.", Path(tmp) / "voice.wav", "bm_george")
        self.assertEqual(model.calls[0][2], "en-gb")


if __name__ == "__main__":
    unittest.main()
