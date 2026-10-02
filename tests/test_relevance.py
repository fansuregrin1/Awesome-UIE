"""Tests for the optional LLM relevance scoring (offline)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".dev_scripts"))

from uie import relevance  # noqa: E402
from uie.sources.base import SourceRecord  # noqa: E402


class FakeLlm:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def complete(self, system, user):
        self.calls.append((system, user))
        return self.response


class BuildPromptTest(unittest.TestCase):
    def test_truncates_abstract_and_indexes(self):
        records = [
            SourceRecord(source="x", title="A", year=2005, abstract="x" * 500),
            SourceRecord(source="x", title="B", year=2006, abstract=None),
        ]
        _, user = relevance.build_batch_prompt(records, abstract_chars=10)
        self.assertIn("0 | 2005 | A | xxxxxxxxxx", user)  # abstract capped at 10
        self.assertIn("1 | 2006 | B | ", user)


class ParseBatchTest(unittest.TestCase):
    def test_valid(self):
        text = (
            '{"results": [{"i": 0, "s": 0.9, "t": "Traditional"}, '
            '{"i": 1, "s": 0.1, "t": "DeepLearning"}]}'
        )
        results = relevance.parse_batch(text, 2)
        self.assertEqual(results[0], relevance.Assessment(0.9, "Traditional"))
        self.assertEqual(results[1], relevance.Assessment(0.1, "DeepLearning"))

    def test_clamps_and_missing(self):
        results = relevance.parse_batch('{"results": [{"i": 0, "s": 2.0}]}', 2)
        self.assertEqual(results[0], relevance.Assessment(1.0, None))
        self.assertIsNone(results[1])

    def test_invalid_type_becomes_none(self):
        results = relevance.parse_batch('{"results": [{"i": 0, "s": 0.5, "t": "Nonsense"}]}', 1)
        self.assertEqual(results[0], relevance.Assessment(0.5, None))

    def test_invalid(self):
        self.assertEqual(relevance.parse_batch("not json", 2), [None, None])


class AssessRecordsTest(unittest.TestCase):
    def test_batches(self):
        records = [SourceRecord(source="x", title=f"t{i}") for i in range(3)]
        llm = FakeLlm('{"results": [{"i": 0, "s": 0.7, "t": "Traditional"}, {"i": 1, "s": 0.8}]}')
        results = relevance.assess_records(records, llm, batch=2)
        self.assertEqual(results[0], relevance.Assessment(0.7, "Traditional"))
        self.assertEqual(results[1], relevance.Assessment(0.8, None))
        self.assertEqual(len(llm.calls), 2)  # 3 records / batch 2 -> 2 requests

    def test_failure_returns_none(self):
        class Boom:
            def complete(self, system, user):
                raise RuntimeError("boom")

        records = [SourceRecord(source="x", title="t")]
        self.assertEqual(relevance.assess_records(records, Boom()), [None])


class VenueTierTest(unittest.TestCase):
    def test_parse_venue_batch(self):
        venues = ["OCEANS 2010", "Some Journal"]
        text = '{"results": [{"i": 0, "tier": "B"}, {"i": 1, "tier": "C"}]}'
        self.assertEqual(
            relevance.parse_venue_batch(text, venues),
            {"OCEANS 2010": "B", "Some Journal": "C"},
        )

    def test_parse_venue_batch_ignores_bad_tier(self):
        self.assertEqual(
            relevance.parse_venue_batch('{"results": [{"i": 0, "tier": "Z"}]}', ["v"]),
            {},
        )

    def test_score_venues_dedupes_and_batches(self):
        llm = FakeLlm('{"results": [{"i": 0, "tier": "A"}]}')
        tiers = relevance.score_venues(["V", "V", "W"], llm, batch=2)
        self.assertEqual(tiers, {"V": "A"})
        self.assertEqual(len(llm.calls), 1)  # two unique venues -> one batch

    def test_score_venues_failure(self):
        class Boom:
            def complete(self, system, user):
                raise RuntimeError("boom")

        self.assertEqual(relevance.score_venues(["V"], Boom()), {})


if __name__ == "__main__":
    unittest.main()
