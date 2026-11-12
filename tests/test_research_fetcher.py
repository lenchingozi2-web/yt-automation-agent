import copy
import unittest
from unittest.mock import patch

import httpx

from research_fetcher import Source, search, validate_batch, write_briefing


class ResearchBriefingTest(unittest.TestCase):
    def setUp(self):
        abstract = "The case report describes a patient with symptoms observed by a hospital team during routine screening."
        self.groups = {}
        self.stories = []
        for index in range(3):
            lead = Source(str(index + 10), "Lead report", "Journal One", "2026-11-01", abstract)
            context = Source(str(index + 100), "Supporting report", "Journal Two", "2026-11-02", abstract)
            self.groups[lead.url] = [lead, context]
            self.stories.append({
                "title": f"An unexpected pattern in case {index}",
                "hook_summary": "A routine check uncovered a pattern the care team could investigate. Later findings added context to the result.",
                "references": [lead.url, context.url],
            })

    def test_validated_story_preserves_exact_urls_and_order(self):
        self.assertEqual(validate_batch(self.stories, self.groups), self.stories)

    def test_recent_search_uses_supported_sort_and_reads_core_journal_metadata(self):
        record = {
            "pmid": "42", "title": "A case study", "firstPublicationDate": "2026-11-11",
            "abstractText": "The published abstract describes the case in detail. " * 5,
            "journalInfo": {"journal": {"title": "Example Journal"}},
        }

        def respond(request):
            self.assertTrue(request.url.params["query"].endswith(" sort_date:y"))
            self.assertNotIn("sort", request.url.params)
            return httpx.Response(200, json={"resultList": {"result": [record]}})

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            self.assertEqual(search(client, "malaria", 1, newest=True)[0].publisher, "Example Journal")

    def test_rejects_invented_url_duplicate_lead_and_copied_phrasing(self):
        for key, value in (
            ("references", [self.stories[0]["references"][0], "https://pubmed.ncbi.nlm.nih.gov/900/"]),
            ("references", self.stories[1]["references"]),
            ("hook_summary", next(iter(self.groups.values()))[0].abstract),
        ):
            with self.subTest(key=key, value=value):
                stories = copy.deepcopy(self.stories)
                stories[0][key] = value
                with self.assertRaises(ValueError):
                    validate_batch(stories, self.groups)

    @patch("research_fetcher.ask")
    def test_source_audit_rejects_incomplete_batch(self, ask):
        ask.side_effect = [self.stories, [{"passed": True}] * 2, self.stories, [{"passed": False}] * 3]
        with self.assertRaisesRegex(RuntimeError, "did not pass verification"):
            write_briefing(None, self.groups, [])

    @patch("research_fetcher.ask")
    def test_successful_source_audit_returns_complete_batch(self, ask):
        ask.side_effect = [self.stories, [{"passed": True}] * 3]
        self.assertEqual(write_briefing(None, self.groups, []), self.stories)
