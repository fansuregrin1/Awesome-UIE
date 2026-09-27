"""Tests for the venue registry and its use in discovery (offline)."""

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".dev_scripts"))

from uie.discover import discover  # noqa: E402
from uie.sources.base import SourceRecord  # noqa: E402
from uie.venues import Venue, VenueRegistry, normalize_issn, normalize_venue  # noqa: E402


class FakeSource:
    name = "fake"

    def __init__(self, records):
        self.records = records

    def search(self, query, since=None, limit=25):
        return list(self.records)


def registry():
    return VenueRegistry(
        [
            Venue(
                code="TIP",
                name="IEEE Transactions on Image Processing",
                type="journal",
                issn="1057-7149",
                aliases=["IEEE Trans. on Image Process."],
            ),
            Venue(
                code="CVPR",
                name="IEEE/CVF Conference on Computer Vision and Pattern Recognition",
                type="conference",
            ),
        ]
    )


class NormalizeTest(unittest.TestCase):
    def test_normalize_venue(self):
        self.assertEqual(normalize_venue("IEEE Trans. on Image Process."), "ieeetransonimageprocess")

    def test_normalize_issn(self):
        self.assertEqual(normalize_issn("1057-7149"), "10577149")
        self.assertIsNone(normalize_issn(None))


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.reg = registry()

    def test_resolve_by_issn(self):
        self.assertEqual(self.reg.resolve(None, "1057-7149"), ("TIP", "issn"))

    def test_resolve_by_name(self):
        self.assertEqual(self.reg.resolve("IEEE Transactions on Image Processing")[0], "TIP")

    def test_resolve_by_alias(self):
        self.assertEqual(self.reg.resolve("IEEE Trans. on Image Process.")[0], "TIP")

    def test_resolve_by_code(self):
        self.assertEqual(self.reg.resolve("CVPR")[0], "CVPR")

    def test_unknown(self):
        self.assertEqual(self.reg.resolve("Optics & Laser Technology"), (None, ""))
        self.assertFalse(self.reg.known("Optics & Laser Technology"))

    def test_resolve_empty(self):
        self.assertEqual(self.reg.resolve(None, None), (None, ""))
        self.assertEqual(self.reg.resolve("", ""), (None, ""))


class EmptyRegistryTest(unittest.TestCase):
    def test_empty_registry(self):
        registry = VenueRegistry([])
        self.assertEqual(registry.resolve("TIP"), (None, ""))
        self.assertFalse(registry.known("TIP"))


class TierTest(unittest.TestCase):
    def setUp(self):
        self.reg = VenueRegistry(
            [
                Venue(code="TIP", name="IEEE Transactions on Image Processing", issn="1057-7149", tier="A"),
                Venue(code="Technium", name="Technium", tier="C"),
                Venue(code="arXiv", name="arXiv", type="preprint", tier="preprint"),
            ]
        )

    def test_tier_lookup(self):
        self.assertEqual(self.reg.tier("TIP"), "A")
        self.assertEqual(self.reg.tier("missing"), "unknown")

    def test_meets_min_tier(self):
        self.assertTrue(self.reg.meets_min_tier("TIP", "A"))
        self.assertTrue(self.reg.meets_min_tier("TIP", "preprint"))
        self.assertTrue(self.reg.meets_min_tier("arXiv", "preprint"))
        self.assertFalse(self.reg.meets_min_tier("Technium", "preprint"))
        self.assertFalse(self.reg.meets_min_tier("Technium", "B"))
        self.assertFalse(self.reg.meets_min_tier("unknown-venue", "C"))

    def test_no_min_tier(self):
        self.assertTrue(self.reg.meets_min_tier("Technium", None))


class DiscoverVenueTest(unittest.TestCase):
    CONFIG = {"keywords": ["underwater image enhancement"], "discover_since_days": 90}

    def test_maps_known_venue(self):
        record = SourceRecord(
            source="crossref",
            title="Underwater Image Enhancement New Method",
            venue="IEEE Transactions on Image Processing",
            issn="1057-7149",
            doi="10.1/x",
            year=2026,
            date="2026-08-01",
        )
        result = discover(
            [], self.CONFIG, sources=[FakeSource([record])], registry=registry(), today=date(2026, 9, 27)
        )
        self.assertEqual(result.new[0].venue, "TIP")
        self.assertFalse(result.new[0].venue_unknown)
        self.assertEqual(result.unknown_venues, [])

    def test_flags_unknown_venue(self):
        record = SourceRecord(
            source="openalex",
            title="Underwater Image Enhancement New Method",
            venue="Optics & Laser Technology",
            doi="10.1/y",
            year=2026,
            date="2026-08-01",
        )
        result = discover(
            [], self.CONFIG, sources=[FakeSource([record])], registry=registry(), today=date(2026, 9, 27)
        )
        self.assertTrue(result.new[0].venue_unknown)
        self.assertIn("Optics & Laser Technology", result.unknown_venues)

    def test_min_tier_gate(self):
        reg = VenueRegistry(
            [
                Venue(code="TIP", name="IEEE Transactions on Image Processing", issn="1057-7149", tier="A"),
                Venue(code="Technium", name="Technium", tier="C"),
            ]
        )
        records = [
            SourceRecord(
                source="crossref",
                title="Underwater Image Enhancement Alpha",
                venue="IEEE Transactions on Image Processing",
                issn="1057-7149",
                doi="10.1/a",
                year=2026,
                date="2026-08-01",
            ),
            SourceRecord(
                source="crossref",
                title="Underwater Image Enhancement Beta",
                venue="Technium",
                doi="10.1/b",
                year=2026,
                date="2026-08-02",
            ),
        ]
        result = discover(
            [],
            self.CONFIG,
            sources=[FakeSource(records)],
            registry=reg,
            min_tier="preprint",
            today=date(2026, 9, 27),
        )
        self.assertEqual([candidate.venue for candidate in result.new], ["TIP"])
        self.assertEqual([candidate.venue for candidate in result.pending], ["Technium"])
        self.assertEqual(result.summary()["pending"], 1)


if __name__ == "__main__":
    unittest.main()
