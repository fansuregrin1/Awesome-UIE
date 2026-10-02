"""Tests for the discovery pipeline (offline)."""

import argparse
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".dev_scripts"))
from uie.discover import (  # noqa: E402
    Candidate,
    _merge_records,
    candidates_to_papers,
    discover,
    is_preprint,
    is_relevant,
    keyword_tokens,
    match_status,
    relevance,
    suggest_classification,
)
from uie.cli import _resolve_window, build_parser  # noqa: E402
from uie.render import published  # noqa: E402
from uie.schema import Paper, PaperType, Status  # noqa: E402
from uie.sources.base import SourceRecord  # noqa: E402
from uie.venues import Venue, VenueRegistry  # noqa: E402


class FakeSource:
    name = "fake"

    def __init__(self, records):
        self.records = records

    def search(self, query, since=None, until=None, limit=25):
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


class FakeNamedSource:
    def __init__(self, name, records):
        self.name = name
        self.records = records

    def search(self, query, since=None, until=None, limit=25):
        return list(self.records)


class MergeTest(unittest.TestCase):
    def test_prefers_published_and_carries_arxiv(self):
        arxiv = SourceRecord(source="arxiv", title="Underwater Image Enhancement Alpha", arxiv_id="2601.00001")
        published = SourceRecord(
            source="crossref",
            title="Underwater Image Enhancement Alpha",
            doi="10.1/x",
            venue="Pattern Recognition",
        )
        merged = _merge_records([arxiv, published])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].venue, "Pattern Recognition")
        self.assertEqual(merged[0].arxiv_id, "2601.00001")
        self.assertFalse(is_preprint(merged[0]))

    def test_is_preprint(self):
        self.assertTrue(is_preprint(SourceRecord(source="arxiv", title="x")))
        self.assertTrue(
            is_preprint(SourceRecord(source="openalex", title="x", venue="arXiv (Cornell University)"))
        )
        self.assertFalse(is_preprint(SourceRecord(source="crossref", title="x", venue="Pattern Recognition")))

    def test_discover_prefers_published_source(self):
        records_arxiv = [
            SourceRecord(
                source="arxiv",
                title="Underwater Image Enhancement Alpha",
                arxiv_id="2601.00001",
                year=2026,
                date="2026-05-01",
            )
        ]
        records_pub = [
            SourceRecord(
                source="crossref",
                title="Underwater Image Enhancement Alpha",
                doi="10.1/x",
                venue="Pattern Recognition",
                year=2026,
                date="2026-05-01",
            )
        ]
        result = discover(
            [],
            {"keywords": ["underwater image enhancement"], "discover_since_days": 1400},
            sources=[FakeNamedSource("arxiv", records_arxiv), FakeNamedSource("crossref", records_pub)],
            today=date(2026, 9, 27),
        )
        self.assertEqual(len(result.new), 1)
        self.assertEqual(result.new[0].venue, "Pattern Recognition")
        self.assertFalse(result.new[0].is_preprint)


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
    def test_diffusion_is_deep_learning(self):
        paper_type, tags, _ = suggest_classification(
            SourceRecord(source="x", title="Underwater Image Enhancement Diffusion Network")
        )
        self.assertEqual(paper_type, "DeepLearning")
        self.assertIn("Diffusion", tags)

    def test_hybrid(self):
        paper_type, tags, _ = suggest_classification(
            SourceRecord(
                source="x",
                title="Underwater Image Enhancement",
                abstract="We combine a physical model with a deep network.",
            )
        )
        self.assertEqual(paper_type, "Hybrid")
        self.assertIn("Physical-Model", tags)

    def test_traditional_from_keywords(self):
        paper_type, _, _ = suggest_classification(
            SourceRecord(source="x", title="Underwater image enhancement via histogram and retinex fusion")
        )
        self.assertEqual(paper_type, "Traditional")

    def test_physical_only_is_traditional(self):
        paper_type, _, basis = suggest_classification(
            SourceRecord(
                source="x",
                title="Underwater Image Restoration",
                abstract="A revised underwater image formation model with attenuation.",
            )
        )
        self.assertEqual(paper_type, "Traditional")
        self.assertIn("physical", basis)

    def test_year_prior_when_no_signal(self):
        # 'Self-Tuning Underwater Image Restoration' (2006): no abstract, no keywords.
        paper_type, _, basis = suggest_classification(
            SourceRecord(source="x", title="Self-Tuning Underwater Image Restoration", year=2006)
        )
        self.assertEqual(paper_type, "Traditional")
        self.assertIn("no signals", basis)

    def test_recent_no_signal_defaults_to_deep_learning(self):
        paper_type, _, _ = suggest_classification(
            SourceRecord(source="x", title="An Underwater Method", year=2025)
        )
        self.assertEqual(paper_type, "DeepLearning")


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

    def test_year_filter(self):
        records = [
            SourceRecord(
                source="crossref",
                title="Underwater Image Enhancement Alpha",
                doi="10.1/a",
                year=2025,
                date="2025-05-01",
            ),
            SourceRecord(
                source="crossref",
                title="Underwater Image Enhancement Beta",
                doi="10.1/b",
                year=2026,
                date="2026-05-01",
            ),
        ]
        result = discover(
            [],
            {"keywords": ["underwater image enhancement"], "discover_since_days": 1400},
            sources=[FakeSource(records)],
            years=[2025],
            today=date(2026, 9, 27),
        )
        self.assertEqual([candidate.record.year for candidate in result.new], [2025])


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


class ResolveWindowTest(unittest.TestCase):
    def test_year_derives_window(self):
        args = argparse.Namespace(year="2004,2005,2006", date_from=None, date_to=None)
        self.assertEqual(_resolve_window(args), ("2004-01-01", "2006-12-31", [2004, 2005, 2006]))

    def test_explicit_dates_win(self):
        args = argparse.Namespace(year="2004", date_from="2020-01-01", date_to="2020-12-31")
        self.assertEqual(_resolve_window(args), ("2020-01-01", "2020-12-31", [2004]))

    def test_default_none(self):
        args = argparse.Namespace(year=None, date_from=None, date_to=None)
        self.assertEqual(_resolve_window(args), (None, None, None))

    def test_years_alias(self):
        args = build_parser().parse_args(["discover", "--years", "2004,2005"])
        self.assertEqual(args.year, "2004,2005")

    def test_min_relevance_arg(self):
        args = build_parser().parse_args(["discover", "--min-relevance", "0.5"])
        self.assertEqual(args.min_relevance, 0.5)


class WindowFilterTest(unittest.TestCase):
    def test_records_outside_window_are_excluded(self):
        records = [
            SourceRecord(
                source="crossref",
                title="Underwater Image Enhancement In Window",
                doi="10.1/a",
                year=2005,
                date="2005-05-01",
            ),
            SourceRecord(
                source="crossref",
                title="Underwater Image Enhancement Out Of Window",
                doi="10.1/b",
                year=2010,
                date="2010-05-01",
            ),
        ]
        result = discover(
            [],
            {"keywords": ["underwater image enhancement"]},
            sources=[FakeSource(records)],
            since="2004-01-01",
            until="2006-12-31",
            today=date(2026, 9, 27),
        )
        self.assertEqual([candidate.record.year for candidate in result.new], [2005])
        self.assertEqual(result.until, "2006-12-31")


class FakeLlm:
    def __init__(self, response):
        self.response = response
        self.prompts = []

    def complete(self, system, user):
        self.prompts.append(user)
        return self.response


class LlmRelevanceTest(unittest.TestCase):
    CONFIG = {"keywords": ["underwater image enhancement"]}

    def test_only_borderline_uses_llm(self):
        records = [
            SourceRecord(
                source="crossref", title="Underwater Image Enhancement Method",
                doi="10.1/high", year=2020, date="2020-01-01",
            ),
            SourceRecord(
                source="crossref", title="Underwater Restoration Approach",
                doi="10.1/band", year=2020, date="2020-01-01",
            ),
        ]
        llm = FakeLlm('{"results": [{"i": 0, "s": 0.1}]}')
        result = discover(
            [], self.CONFIG, sources=[FakeSource(records)],
            llm_relevance=llm, since="2019-01-01", until="2021-12-31", today=date(2026, 9, 27),
        )
        # rule score 1.0 -> auto-accepted without LLM; borderline rejected by LLM (0.1 < 0.5)
        self.assertEqual([candidate.record.doi for candidate in result.new], ["10.1/high"])
        self.assertEqual(len(llm.prompts), 1)
        self.assertIn("Underwater Restoration Approach", llm.prompts[0])

    def test_llm_type_overrides_rule(self):
        records = [
            SourceRecord(
                source="crossref", title="Underwater Restoration Approach",
                doi="10.1/band", year=2020, date="2020-01-01",
            ),
        ]
        llm = FakeLlm('{"results": [{"i": 0, "s": 0.9, "t": "Hybrid"}]}')
        result = discover(
            [], self.CONFIG, sources=[FakeSource(records)],
            llm_relevance=llm, since="2019-01-01", until="2021-12-31", today=date(2026, 9, 27),
        )
        self.assertEqual(len(result.new), 1)
        candidate = result.new[0]
        self.assertEqual(candidate.llm_type, "Hybrid")
        self.assertEqual(candidate.suggested_type, "Hybrid")
        self.assertIn("llm", candidate.type_basis)

    def test_llm_venue_tier_gates(self):
        records = [
            SourceRecord(
                source="crossref", title="Underwater Restoration Alpha", venue="OCEANS 2010",
                doi="10.1/a", year=2020, date="2020-01-01",
            ),
            SourceRecord(
                source="crossref", title="Underwater Restoration Beta", venue="Low Journal",
                doi="10.1/b", year=2020, date="2020-01-01",
            ),
        ]
        llm = RoutingLlm()
        registry = VenueRegistry(
            [Venue(code="TIP", name="IEEE Transactions on Image Processing", tier="A")]
        )
        result = discover(
            [], self.CONFIG, sources=[FakeSource(records)], registry=registry, min_tier="B",
            llm_relevance=llm, llm_venue_tier=True,
            since="2019-01-01", until="2021-12-31", today=date(2026, 9, 27),
        )
        # sorted unique venues: "Low Journal" (C), "OCEANS 2010" (A)
        self.assertEqual(result.venue_tiers, {"Low Journal": "C", "OCEANS 2010": "A"})
        self.assertIn("OCEANS 2010", {candidate.venue for candidate in result.new})
        self.assertIn("Low Journal", {candidate.venue for candidate in result.pending})

    def test_llm_venue_b_stays_pending(self):
        class VenueB:
            def complete(self, system, user):
                if "venue's tier" in user:
                    return '{"results": [{"i": 0, "tier": "B"}]}'
                return '{"results": [{"i": 0, "s": 0.9}]}'

        records = [
            SourceRecord(
                source="crossref", title="Underwater Restoration Approach", venue="Mid Conference",
                doi="10.1/m", year=2020, date="2020-01-01",
            ),
        ]
        registry = VenueRegistry(
            [Venue(code="TIP", name="IEEE Transactions on Image Processing", tier="A")]
        )
        result = discover(
            [], self.CONFIG, sources=[FakeSource(records)], registry=registry, min_tier="preprint",
            llm_relevance=VenueB(), llm_venue_tier=True,
            since="2019-01-01", until="2021-12-31", today=date(2026, 9, 27),
        )
        self.assertEqual(result.venue_tiers, {"Mid Conference": "B"})
        self.assertEqual(result.new, [])
        self.assertEqual([candidate.venue for candidate in result.pending], ["Mid Conference"])


class RoutingLlm:
    """Returns a paper-batch or venue-batch response based on the prompt."""

    def __init__(self):
        self.prompts = []

    def complete(self, system, user):
        self.prompts.append(user)
        if "venue's tier" in user:
            return '{"results": [{"i": 0, "tier": "C"}, {"i": 1, "tier": "A"}]}'
        return '{"results": [{"i": 0, "s": 0.9}, {"i": 1, "s": 0.9}]}'


if __name__ == "__main__":
    unittest.main()
