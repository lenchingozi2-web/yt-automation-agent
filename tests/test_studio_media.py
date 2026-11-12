"""Offline thumbnail smoke test (no tokens, storage or network calls)."""
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from PIL import Image
from studio_media import create_thumbnail, verify_thumbnail


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
    print("Thumbnail generation smoke test passed")
