"""Offline thumbnail smoke test (no tokens, storage or network calls)."""
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from PIL import Image
from studio_audio import spoken_shots, write_srt
from studio_media import contextual_searches, create_thumbnail, footage_intent, footage_relevance, narration_motion, verify_thumbnail


def test_thumbnail_image():
    with TemporaryDirectory() as tmp:
        footage = Path(tmp) / "footage.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30",
                        "-t", "2", "-c:v", "libx264", str(footage)], check=True, timeout=20)
        for ratio, size in [("16:9", (1280, 720)), ("9:16", (1080, 1920))]:
            image_path = Path(tmp) / f"thumb-{size[0]}.jpg"
            create_thumbnail(footage, "Why medical AI will change how we learn", image_path, ratio=ratio)
            with Image.open(image_path) as image:
                assert image.size == size
                assert image.format == "JPEG"
            verify_thumbnail(image_path, ratio)


if __name__ == "__main__":
    test_thumbnail_image()
    narration = "A kidney stone forms. The laboratory examines a urine sample."
    with TemporaryDirectory() as tmp:
        path = Path(tmp)
        cues = path / "timed.srt"
        write_srt([(0, 3.5, "A kidney stone forms."), (4.1, 7.9, "The laboratory examines a urine sample.")], cues)
        shots = spoken_shots(cues, 8, 5, narration)
        assert shots == [(120, "A kidney stone forms."), (120, "The laboratory examines a urine sample.")]
        terms, subjects, words = footage_intent(shots[1][1], narration, ["mountain landscape", "kidney stone", "urine sample"])
        assert terms[0] == "urine sample" and "mountain landscape" not in terms and "kidney stone" not in terms
        assert footage_relevance("mountain, sunset, hospital", subjects, words, terms[0]) == 0
        assert footage_relevance("kidney stone, anatomy", subjects, words, terms[0]) == 0
        assert footage_relevance("urine sample, specimen cup, laboratory", subjects, words, terms[0]) > 0
        terms, subjects, words = footage_intent("forest trees", narration, ["mountain waterfall"], override=True)
        assert footage_relevance("mountain, landscape", subjects, words, terms[0], override=True) == 0
        assert footage_relevance("mountain, waterfall", subjects, words, terms[0], override=True) > 0
        assert contextual_searches("A better estimate was possible.", "A better estimate was possible.", "AI and kidney stones")[0] == "laboratory equipment"
        assert contextual_searches("Students studied in groups.", "Students studied in groups.", "AI and kidney stones")[0] == "student notebook"
        card = narration_motion(shots[1][1], path / "spoken.mp4", (1080, 1920), 90)
        def frame_at(seconds):
            return subprocess.check_output(["ffmpeg", "-v", "error", "-ss", str(seconds), "-i", str(card),
                "-frames:v", "1", "-pix_fmt", "rgb24", "-threads", "1", "-f", "rawvideo", "-"], timeout=15)
        first, settled = frame_at(0), frame_at(1.3)
        assert len(first) == len(settled) > 0 and first != settled
        create_thumbnail(card, "Urine sample", path / "poster.jpg", ratio="9:16")
        verify_thumbnail(path / "poster.jpg", "9:16")
    print("Passed thumbnail and timed footage relevance checks")
