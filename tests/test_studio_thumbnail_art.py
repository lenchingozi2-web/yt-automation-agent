from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from PIL import Image
from studio_thumbnail_art import make_prader_willi_thumbnail

class ThumbnailArtworkTests(unittest.TestCase):
    def test_prader_willi_thumbnail_is_supported_jpeg(self):
        with TemporaryDirectory() as directory:
            image = Path(directory) / "thumbnail.jpg"
            make_prader_willi_thumbnail(image)
            with Image.open(image) as poster:
                self.assertEqual(poster.size, (1280, 720))
                self.assertEqual(poster.format, "JPEG")
            self.assertGreater(image.stat().st_size, 0)
            self.assertLess(image.stat().st_size, 2_000_000)

if __name__ == "__main__":
    unittest.main()
