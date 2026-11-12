from pathlib import Path
from tempfile import TemporaryDirectory
from PIL import Image
from studio_thumbnail_art import make_prader_willi_thumbnail

def test_prader_willi_thumbnail_is_supported_jpeg():
    with TemporaryDirectory() as directory:
        image = Path(directory) / "thumbnail.jpg"
        make_prader_willi_thumbnail(image)
        with Image.open(image) as poster:
            assert poster.size == (1280, 720)
            assert poster.format == "JPEG"
        assert 0 < image.stat().st_size < 2_000_000
