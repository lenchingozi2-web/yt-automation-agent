import asyncio
from io import BytesIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx
from PIL import Image
from studio_media import footage_relevance, prepare_stock_image
from studio_retry import TemporaryRenderError
from worker import fetch_pixabay, validate_visual_coverage
from research_fetcher import Source, appropriate_beat


class VisualQualityTests(unittest.TestCase):
    def test_search_moves_past_used_first_page_and_accepts_illustrations(self):
        pages = []
        image = BytesIO()
        Image.new("RGB", (1280, 720), "blue").save(image, "JPEG")

        def respond(request):
            if request.url.host == "pixabay.com":
                self.assertEqual(request.url.params["image_type"], "all")
                page = int(request.url.params["page"])
                pages.append(page)
                return httpx.Response(200, json={"hits": [{"id": page, "tags": "ocean wave", "imageWidth": 1280,
                    "imageHeight": 720, "largeImageURL": "https://stock.invalid/photo.jpg"}]})
            return httpx.Response(200, content=image.getvalue())

        async def perform():
            with TemporaryDirectory() as tmp:
                async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                    return await fetch_pixabay({"scene_number": 2, "format": "9:16", "narration": "Ocean waves.",
                        "search_queries": ["ocean wave"], "thumbnail_search": True, "footage_override": True},
                        Path(tmp) / "stock.mp4", set(), {"photo-1"}, {}, shared_client=client)

        with patch.dict(os.environ, {"PIXABAY_API_KEY": "fixture"}):
            self.assertEqual(asyncio.run(perform()), "photo-2")
        self.assertEqual(pages, [1, 2])

    def test_landscape_photos_can_fill_portrait_without_losing_subject(self):
        image = Image.new("RGB", (1280, 720), "red")
        data = BytesIO()
        image.save(data, "JPEG")
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "portrait.png"
            prepare_stock_image(data.getvalue(), output, (1080, 1920))
            with Image.open(output) as rendered:
                self.assertEqual(rendered.size, (1080, 1920))
                self.assertGreater(rendered.getpixel((540, 960))[0], 240)

    def test_context_tags_need_one_anchor_but_explicit_replacement_needs_exact_subject(self):
        self.assertGreater(footage_relevance("laboratory, scientist", [], {"laboratory", "equipment"}, "laboratory equipment", True, contextual=True), 0)
        self.assertEqual(footage_relevance("mountain, landscape", [], {"mountain", "waterfall"}, "mountain waterfall", True), 0)
        self.assertEqual(footage_relevance("mountain", [], {"laboratory"}, "laboratory equipment", True, contextual=True), 0)

    def test_visual_quality_is_weighted_by_duration_and_checks_consecutive_cards(self):
        self.assertEqual(validate_visual_coverage([(80, True), (20, False)])["stock_fraction"], .8)
        for windows in [[(20, True), (30, False)], [(90, True), (11, False), (11, False)]]:
            with self.assertRaisesRegex(ValueError, "Visual quality check"):
                validate_visual_coverage(windows)

    def test_rate_limit_is_retryable_instead_of_becoming_text_cards(self):
        async def perform():
            with TemporaryDirectory() as tmp:
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(429, headers={"X-RateLimit-Reset": "30"}))) as client:
                    await fetch_pixabay({"narration": "A student studies in class.", "search_queries": ["student classroom"]},
                                        Path(tmp) / "stock.mp4", set(), set(), {}, shared_client=client)
        with patch.dict(os.environ, {"PIXABAY_API_KEY": "fixture"}), self.assertRaises(TemporaryRenderError) as caught:
            asyncio.run(perform())
        self.assertGreaterEqual(caught.exception.retry_after, 29)

    def test_worker_respects_media_rate_limit_before_retrying_cached_work(self):
        from worker import render_job
        with (patch("worker.process", side_effect=[TemporaryRenderError("Rate limited", retry_after=30), None]),
              patch("worker.save_fields"), patch("worker.time.sleep") as sleep):
            self.assertTrue(render_job({"id": "fixture", "status": "rendering"}, None))
        sleep.assert_called_once_with(30)

    def test_medical_ai_benchmark_does_not_fill_education_slot(self):
        benchmark = Source("1", "Medical image reasoning", "Journal", "2026", "Radiologists benchmark AI diagnosis.")
        learning = Source("2", "Learning with feedback", "Journal", "2026", "Students used retrieval practice.")
        self.assertFalse(appropriate_beat(benchmark, "education"))
        self.assertTrue(appropriate_beat(learning, "education"))
        self.assertTrue(appropriate_beat(benchmark, "health"))


if __name__ == "__main__":
    unittest.main()
