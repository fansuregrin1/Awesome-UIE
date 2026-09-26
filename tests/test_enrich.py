"""Tests for the enrichment matching / field-resolution logic (offline)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".dev_scripts"))

from uie.enrich import (  # noqa: E402
    apply_suggestions,
    enrich_papers,
    normalize_arxiv,
    normalize_doi,
    similarity,
)
from uie.schema import Paper, PaperType  # noqa: E402
from uie.sources.base import SourceRecord  # noqa: E402

TITLE = "WaterMamba: Visual State Space Model for Underwater Image Enhancement"


class FakeSource:
    name = "fake"

    def __init__(self, records):
        self.records = records

    def lookup(self, **kwargs):
        return list(self.records)


def make_paper(**overrides):
    base = dict(
        id="2024-watermamba",
        title=TITLE,
        year=2024,
        venue="arXiv",
        type=PaperType.DEEP_LEARNING,
        url="https://arxiv.org/abs/2405.08419",
    )
    base.update(overrides)
    return Paper(**base)


class NormalizeTest(unittest.TestCase):
    def test_normalize_doi(self):
        self.assertEqual(normalize_doi("https://doi.org/10.1109/TIP.2019.1"), "10.1109/tip.2019.1")
        self.assertEqual(normalize_doi("10.1109/ABC"), "10.1109/abc")
        self.assertIsNone(normalize_doi(None))

    def test_normalize_doi_http_prefix(self):
        self.assertEqual(normalize_doi("http://doi.org/10.1/AbC"), "10.1/abc")
        self.assertEqual(normalize_doi("doi:10.1/XyZ"), "10.1/xyz")

    def test_normalize_arxiv_edges(self):
        self.assertIsNone(normalize_arxiv(None))
        self.assertEqual(normalize_arxiv("2405.08419v10"), "2405.08419")

    def test_similarity(self):
        self.assertEqual(similarity(TITLE, TITLE), 1.0)
        self.assertLess(similarity(TITLE, "A totally different paper"), 0.9)


class EnrichTest(unittest.TestCase):
    def test_fills_missing_fields(self):
        record = SourceRecord(
            source="crossref",
            title=TITLE,
            year=2024,
            doi="10.1109/XYZ.2024.1",
            authors=["A B", "C D"],
            abstract="abstract text",
        )
        result = enrich_papers([make_paper()], sources=[FakeSource([record])])
        fields = {suggestion.field: suggestion.suggested for suggestion in result.suggestions}
        self.assertEqual(fields.get("doi"), "10.1109/XYZ.2024.1")
        self.assertEqual(fields.get("authors"), ["A B", "C D"])
        self.assertEqual(result.summary()["matched"], 1)

    def test_skips_arxiv_datacite_doi(self):
        record = SourceRecord(
            source="openalex",
            title=TITLE,
            year=2024,
            doi="10.48550/arXiv.2405.08419",
            authors=["A B"],
        )
        result = enrich_papers([make_paper()], sources=[FakeSource([record])])
        self.assertFalse(any(suggestion.field == "doi" for suggestion in result.suggestions))

    def test_does_not_overwrite_existing(self):
        record = SourceRecord(source="crossref", title=TITLE, year=2024, doi="10.1109/new")
        paper = make_paper(doi="10.1109/existing")
        result = enrich_papers([paper], sources=[FakeSource([record])])
        self.assertFalse(any(suggestion.field == "doi" for suggestion in result.suggestions))

    def test_ambiguous_below_threshold(self):
        record = SourceRecord(
            source="crossref",
            title="WaterMamba: Visual State Space Model for Underwater Image",
            year=2024,
        )
        result = enrich_papers([make_paper()], sources=[FakeSource([record])], threshold=0.99)
        self.assertEqual(result.summary()["matched"], 0)
        self.assertEqual(result.summary()["ambiguous"], 1)

    def test_unmatched_when_no_candidates(self):
        result = enrich_papers([make_paper()], sources=[FakeSource([])])
        self.assertEqual(result.summary()["unmatched"], 1)


class ApplyTest(unittest.TestCase):
    def test_apply_fills_empty_fields(self):
        record = SourceRecord(source="crossref", title=TITLE, year=2024, doi="10.1109/new", authors=["A B"])
        paper = make_paper()
        result = enrich_papers([paper], sources=[FakeSource([record])])
        applied = apply_suggestions([paper], result)
        self.assertIn(("2024-watermamba", "doi", "10.1109/new"), applied)
        self.assertEqual(paper.doi, "10.1109/new")
        self.assertEqual(paper.authors, ["A B"])

    def test_apply_respects_min_score(self):
        record = SourceRecord(
            source="crossref",
            title="WaterMamba: Visual State Space Model for Underwater Image",
            year=2024,
            doi="10.1109/new",
        )
        result = enrich_papers([make_paper()], sources=[FakeSource([record])], threshold=0.5)
        self.assertEqual(apply_suggestions([make_paper()], result, min_score=0.999), [])

    def test_apply_limited_to_fields(self):
        record = SourceRecord(source="crossref", title=TITLE, year=2024, doi="10.1109/new", authors=["A B"])
        paper = make_paper()
        result = enrich_papers([paper], sources=[FakeSource([record])])
        applied = apply_suggestions([paper], result, fields=["authors"])
        self.assertEqual([entry[1] for entry in applied], ["authors"])
        self.assertIsNone(paper.doi)

    def test_apply_empty_suggestions(self):
        result = enrich_papers([make_paper()], sources=[FakeSource([])])
        self.assertEqual(apply_suggestions([make_paper()], result), [])


if __name__ == "__main__":
    unittest.main()
