"""Offline thumbnail smoke test (no tokens, storage or network calls)."""
from pathlib import Path
from tempfile import TemporaryDirectory
from PIL import Image
from studio_media import create_thumbnail


def test_thumbnail_image():
    with TemporaryDirectory() as tmp:
        image_path = Path(tmp) / "thumb.jpg"
        create_thumbnail(Path(tmp) / "nonexistent.mp4", "Why medical AI will change how we learn", image_path)
        with Image.open(image_path) as image:
            assert image.size == (1280, 720)
            assert image.format == "JPEG"
        assert image_path.stat().st_size < 2_000_000


if __name__ == "__main__":
    test_thumbnail_image()
    print("Thumbnail generation smoke test passed")
