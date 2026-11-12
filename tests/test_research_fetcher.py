import copy
import json
import os
import unittest
from datetime import date
from unittest.mock import Mock, patch

import httpx
from google.genai.errors import ClientError

from research_fetcher import BEATS, Source, ask, eric_search, related_search, resolve_story_sources, search, validate_batch, write_briefing


class ResearchBriefingTest(unittest.TestCase):
    def setUp(self):
        abstract = "The case report describes a patient with symptoms observed by a hospital team during routine screening."
        self.groups = {}
        self.stories = []
        for index, beat in enumerate(BEATS):
            lead = Source(str(index + 10), "Lead report", "Journal One", "2026-11-01", abstract, beat=beat)
            context = Source(str(index + 100), "Supporting report", "Journal Two", "2026-11-02", abstract)
            self.groups[lead.url] = [lead, context]
            self.stories.append({
                "title": f"An unexpected pattern in case {index}",
                "hook_summary": "A routine check uncovered a pattern the care team could investigate. Later findings added context to the result.",
                "references": [lead.url, context.url],
                "topic_area": beat,
            })
        self.numbered = [dict(title=story["title"], hook_summary=story["hook_summary"], pair=index)
                         for index, story in enumerate(self.stories, 1)]

    def test_validated_story_preserves_exact_urls_and_order(self):
        self.assertEqual(validate_batch(self.stories, self.groups), self.stories)

    def test_rejects_a_story_reusing_an_earlier_title(self):
        with self.assertRaisesRegex(ValueError, "repeated"):
            validate_batch(self.stories, self.groups, [self.stories[1]["title"].upper()])

    def test_numbered_sources_resolve_to_original_urls(self):
        pairs = {index: tuple(sources) for index, sources in enumerate(self.groups.values(), 1)}
        self.assertEqual(validate_batch(resolve_story_sources(self.numbered, pairs), self.groups), self.stories)
        with self.assertRaisesRegex(ValueError, "source pair"):
            resolve_story_sources([dict(self.numbered[0], pair=100)], pairs)

    def test_reads_canonical_peer_reviewed_eric_records(self):
        record = {"id": "EJ1234", "title": "Learning research", "source": "Example Journal",
                  "description": "A description of a peer-reviewed study. " * 10, "publicationdateyear": 2026}

        def respond(request):
            self.assertIn("peerreviewed:T", request.url.params["search"])
            return httpx.Response(200, json={"response": {"docs": [record]}})

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            self.assertEqual(eric_search(client, "retrieval practice", 3)[0].url, "https://eric.ed.gov/?id=EJ1234")

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

    @patch("research_fetcher.search_safely")
    def test_related_search_can_match_either_concept_but_excludes_lead_journal(self, fetch):
        primary, support = next(iter(self.groups.values()))
        same_journal = Source("99", "Same journal", primary.publisher, primary.published, primary.abstract)
        fetch.return_value = [primary, same_journal, support]
        self.assertEqual(related_search(None, primary, ["botulism", "foodborne illness"], date(2026, 11, 12)), [support])
        self.assertIn('TITLE_ABS:"botulism" OR TITLE_ABS:"foodborne illness"', fetch.call_args.args[1])

    @patch.dict(os.environ, {"GROQ_API_KEY": "test", "GEMINI_RESEARCH_MODEL": ""})
    @patch("research_fetcher.httpx.post")
    def test_model_unavailability_uses_fallback_with_same_evidence(self, post):
        gemini = Mock()
        gemini.models.generate_content.side_effect = ClientError(404, {"error": {"message": "Unavailable"}})
        payload = {"reports": [{"pmid": "123"}]}
        content = json.dumps({"items": [{"pmid": "123"}]})
        post.return_value = httpx.Response(200, json={"choices": [{"message": {"content": content}}]},
                                           request=httpx.Request("POST", "https://example.org"))
        self.assertEqual(ask(gemini, "Select a report.", payload), [{"pmid": "123"}])
        self.assertIn(json.dumps(payload), post.call_args.kwargs["json"]["messages"][0]["content"])

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
        ask.side_effect = [self.numbered, [{"passed": True}] * 2, self.numbered, [{"passed": False}] * 3]
        with self.assertRaisesRegex(RuntimeError, "did not pass verification"):
            write_briefing(None, self.groups, [])

    @patch("research_fetcher.ask")
    def test_successful_source_audit_returns_complete_batch(self, ask):
        ask.side_effect = [self.numbered, [{"passed": True}] * 3]
        self.assertEqual(write_briefing(None, self.groups, []), self.stories)
