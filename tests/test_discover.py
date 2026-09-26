"""Tests for the discovery pipeline (offline)."""

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".dev_scripts"))

from uie.discover import (  # noqa: E402
    Candidate,
    candidates_to_papers,
    discover,
    is_relevant,
    keyword_tokens,
    match_status,
    relevance,
    suggest_classification,
)
from uie.render import published  # noqa: E402
from uie.schema import Paper, PaperType, Status  # noqa: E402
from uie.sources.base import SourceRecord  # noqa: E402


class FakeSource:
    name = "fake"

    def __init__(self, records):
        self.records = records

    def search(self, query, since=None, limit=25):
        return list(self.records)


def make_paper(**overrides):
    base = dict(
        id="2020-benchmark",
        title="Underwater Image Enhancement Benchmark",
        year=2020,
        venue="TIP",
        type=PaperType.DEEP_LEARNING,
        url="https://example.org/paper",
        doi="10.1109/existing",
    )
    base.update(overrides)
    return Paper(**base)


def make_candidate(candidate_id="2026-demo", url="https://arxiv.org/abs/2601.00001", doi=None, year=2026):
    record = SourceRecord(
        source="arxiv",
        title="A New Underwater Image Enhancement Method",
        year=year,
        date="2026-08-01",
        venue="arXiv",
        doi=doi,
        arxiv_id="2601.00001",
        authors=["A B"],
        url=url,
    )
    return Candidate(
        record=record,
        relevance=0.9,
        status="new",
        matched_id=None,
        suggested_type="DeepLearning",
        suggested_tags=["CNN"],
        suggested_id=candidate_id,
        venue="arXiv",
        venue_unknown=False,
    )


class KeywordTest(unittest.TestCase):
    def test_tokens_drop_stopwords(self):
        self.assertEqual(
            keyword_tokens(["underwater image enhancement", "of the"]),
            {"underwater", "image", "enhancement"},
        )


class RelevanceTest(unittest.TestCase):
    def test_requires_anchor(self):
        tokens = {"underwater", "image", "enhancement"}
        self.assertGreater(relevance(SourceRecord(source="x", title="Underwater Image Enhancement"), tokens), 0)
        self.assertEqual(relevance(SourceRecord(source="x", title="Image Enhancement"), tokens), 0.0)

    def test_fraction_of_tokens(self):
        tokens = {"underwater", "image", "enhancement", "restoration"}
        record = SourceRecord(source="x", title="Underwater Image Enhancement")
        self.assertEqual(relevance(record, tokens), 0.75)

    def test_title_gate(self):
        tokens = {"underwater", "image", "enhancement"}
        abstract_only = SourceRecord(
            source="x",
            title="A Study of Image Enhancement",
            abstract="This underwater study...",
        )
        ok, _ = is_relevant(abstract_only, tokens)
        self.assertFalse(ok)

        ok, score = is_relevant(SourceRecord(source="x", title="Underwater Image Enhancement"), tokens)
        self.assertTrue(ok)
        self.assertGreater(score, 0)

    def test_empty_tokens(self):
        self.assertEqual(relevance(SourceRecord(source="x", title="Underwater Image"), set()), 0.0)


class ClassificationTest(unittest.TestCase):
    def test_diffusion(self):
        paper_type, tags = suggest_classification(
            SourceRecord(source="x", title="Underwater Image Enhancement Diffusion Network")
        )
        self.assertEqual(paper_type, "DeepLearning")
        self.assertIn("Diffusion", tags)

    def test_hybrid(self):
        paper_type, tags = suggest_classification(
            SourceRecord(
                source="x",
                title="Underwater Image Enhancement",
                abstract="We combine a physical model with a deep network.",
            )
        )
        self.assertEqual(paper_type, "Hybrid")
        self.assertIn("Physical-Model", tags)

    def test_traditional(self):
        paper_type, _ = suggest_classification(
            SourceRecord(source="x", title="Underwater image enhancement via histogram and retinex fusion")
        )
        self.assertEqual(paper_type, "Traditional")


class MatchTest(unittest.TestCase):
    def test_existing_by_doi(self):
        status, _ = match_status(
            SourceRecord(source="x", title="Whatever", doi="10.1109/EXISTING"),
            {"10.1109/existing"},
            set(),
            [],
        )
        self.assertEqual(status, "existing")

    def test_existing_by_title(self):
        status, matched = match_status(
            SourceRecord(source="x", title="Underwater Image Enhancement Benchmark"),
            set(),
            set(),
            [("p1", "Underwater Image Enhancement Benchmark")],
        )
        self.assertEqual(status, "existing")
        self.assertEqual(matched, "p1")

    def test_similar(self):
        status, _ = match_status(
            SourceRecord(source="x", title="Underwater Image Enhancement Benchmarking Study"),
            set(),
            set(),
            [("p1", "Underwater Image Enhancement Benchmark")],
        )
        self.assertEqual(status, "similar")

    def test_new(self):
        status, _ = match_status(
            SourceRecord(source="x", title="A New Underwater Image Enhancement Network"),
            set(),
            set(),
            [("p1", "Underwater Image Enhancement Benchmark")],
        )
        self.assertEqual(status, "new")

    def test_no_titles_is_new(self):
        status, matched = match_status(
            SourceRecord(source="x", title="Underwater Image Enhancement"), set(), set(), []
        )
        self.assertEqual(status, "new")
        self.assertIsNone(matched)


class DiscoverTest(unittest.TestCase):
    def test_end_to_end(self):
        paper = make_paper()
        records = [
            SourceRecord(source="crossref", title="Underwater Image Enhancement Benchmark", doi="10.1109/existing"),
            SourceRecord(
                source="openalex",
                title="A New Underwater Image Enhancement Network",
                doi="10.1234/new",
                year=2026,
                date="2026-08-01",
            ),
            SourceRecord(
                source="arxiv",
                title="Underwater Image Enhancement Benchmarking Study",
                arxiv_id="2601.00001",
                year=2026,
                date="2026-08-02",
            ),
            SourceRecord(
                source="crossref",
                title="A Novel Image Enhancement Method",
                doi="10.1234/off",
                year=2026,
                date="2026-08-03",
            ),
            SourceRecord(
                source="openalex",
                title="Underwater Image Enhancement Old",
                doi="10.1234/old",
                year=2000,
                date="2000-01-01",
            ),
        ]
        config = {"keywords": ["underwater image enhancement"], "discover_since_days": 90}
        result = discover(
            [paper],
            config,
            sources=[FakeSource(records)],
            today=date(2026, 9, 27),
        )
        summary = result.summary()
        self.assertEqual(summary["existing"], 1)
        self.assertEqual(summary["new"], 1)
        self.assertEqual(summary["similar"], 1)
        self.assertEqual(summary["found"], 3)
        self.assertEqual(result.new[0].record.doi, "10.1234/new")

    def test_empty_keywords(self):
        result = discover(
            [],
            {"keywords": [], "discover_since_days": 90},
            sources=[FakeSource([])],
            today=date(2026, 9, 27),
        )
        self.assertEqual(result.summary()["found"], 0)


class ApplyDiscoverTest(unittest.TestCase):
    def test_candidates_to_papers(self):
        from uie.discover import DiscoverResult

        result = DiscoverResult(since="2026-06-29", queries=["q"], new=[make_candidate()])
        added = candidates_to_papers([], result, today=date(2026, 9, 27))
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].status, Status.CANDIDATE)
        self.assertEqual(added[0].venue, "arXiv")
        self.assertEqual(added[0].tags, ["CNN"])

    def test_duplicate_id_gets_suffix(self):
        from uie.discover import DiscoverResult

        existing = make_paper(id="2026-demo")
        result = DiscoverResult(since="2026-06-29", queries=["q"], new=[make_candidate(candidate_id="2026-demo")])
        added = candidates_to_papers([existing], result, today=date(2026, 9, 27))
        self.assertEqual(added[0].id, "2026-demo-2")

    def test_skips_without_url(self):
        from uie.discover import DiscoverResult

        result = DiscoverResult(since="2026-06-29", queries=["q"], new=[make_candidate(url=None)])
        self.assertEqual(candidates_to_papers([], result), [])

    def test_uses_doi_url(self):
        from uie.discover import DiscoverResult

        candidate = make_candidate(url="https://openalex.org/W1", doi="10.1/x")
        result = DiscoverResult(since="2026-06-29", queries=["q"], new=[candidate])
        added = candidates_to_papers([], result)
        self.assertEqual(str(added[0].url), "https://doi.org/10.1/x")

    def test_skips_out_of_range_year(self):
        from uie.discover import DiscoverResult

        result = DiscoverResult(since="2026-06-29", queries=["q"], new=[make_candidate(year=1990)])
        self.assertEqual(candidates_to_papers([], result), [])

    def test_published_filters_candidates(self):
        verified = make_paper(id="verified")
        candidate = make_paper(id="candidate", status=Status.CANDIDATE, url="https://example.org/c")
        self.assertEqual([paper.id for paper in published([verified, candidate])], ["verified"])


if __name__ == "__main__":
    unittest.main()