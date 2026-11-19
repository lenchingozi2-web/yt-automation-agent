"""Exercise the real worker preview path without any paid services."""
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worker import preview_bitrates, storage_preview, ffprobe_duration
from studio_master import OBJECT_LIMIT
from studio_audio import validate_audio

class LongPreviewTests(unittest.TestCase):
    def test_budget_at_actual_failed_duration_and_maximum(self):
        for seconds in (1358, 1763, 1800):
            for fraction in (1, .82, .65):
                picture, audio = preview_bitrates(seconds, fraction)
                self.assertGreaterEqual(picture, 24)
                self.assertLess((picture+audio)*1000*seconds/8, OBJECT_LIMIT)
                self.assertLessEqual(audio, 96)

    def test_real_22_minute_master_unchanged_and_review_under_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            master=Path(tmp)/"master.mp4"
            # Complex noise audio at a high bitrate produces a realistic oversized source,
            # with a 30fps picture and exact 22:38 timeline. No external media required.
            subprocess.run(["ffmpeg","-v","error","-y","-f","lavfi","-i","testsrc2=s=160x90:r=30",
                "-f","lavfi","-i","anoisesrc=color=white:amplitude=0.25:r=48000",
                "-t","1358","-c:v","libx264","-threads","2","-preset","ultrafast",
                "-pix_fmt","yuv420p","-b:v","280k","-c:a","aac","-b:a","256k","-ac","2",str(master)],
                check=True,timeout=240)
            self.assertGreater(master.stat().st_size, OBJECT_LIMIT)
            with master.open("rb") as source:
                digest=hashlib.file_digest(source,"sha256").hexdigest()
            review=storage_preview(master,[{"duration":679},{"duration":679}])
            self.assertNotEqual(master,review)
            self.assertLess(review.stat().st_size,OBJECT_LIMIT)
            self.assertAlmostEqual(ffprobe_duration(review),1358,places=1)
            validate_audio(review,expected=1358)
            with master.open("rb") as source:
                self.assertEqual(hashlib.file_digest(source,"sha256").hexdigest(),digest)

    def test_small_source_is_not_reencoded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"small.mp4"
            path.write_bytes(b"small")
            self.assertEqual(storage_preview(path),path)

if __name__=="__main__":
    unittest.main()
