"""Offline parser tests for the metadata sources (no network)."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".dev_scripts"))

from uie.sources.arxiv import parse_feed  # noqa: E402
from uie.sources.crossref import parse_work as parse_crossref  # noqa: E402
from uie.sources.openalex import parse_work as parse_openalex  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


class ArxivParseTest(unittest.TestCase):
    def test_parse_id_list_lookup(self):
        records = parse_feed((FIXTURES / "arxiv_2405.08419.xml").read_text(encoding="utf-8"))
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.source, "arxiv")
        self.assertEqual(record.arxiv_id, "2405.08419")
        self.assertIn("WaterMamba", record.title)
        self.assertEqual(record.year, 2024)
        self.assertTrue(any("Guan" in author for author in record.authors))
        self.assertIsNotNone(record.abstract)
        self.assertIn("Underwater", record.abstract)


class OpenAlexParseTest(unittest.TestCase):
    def test_parse_search_result(self):
        data = json.loads((FIXTURES / "openalex_search.json").read_text(encoding="utf-8"))
        records = [parse_openalex(work) for work in data["results"]]
        self.assertTrue(records)
        record = records[0]
        self.assertIn("WaterMamba", record.title)
        self.assertEqual(record.year, 2024)
        self.assertTrue(record.authors)
        self.assertIsNotNone(record.abstract)
        self.assertNotIn("https://doi.org/", record.doi or "")


class CrossrefParseTest(unittest.TestCase):
    def test_parse_doi_lookup(self):
        data = json.loads((FIXTURES / "crossref_doi.json").read_text(encoding="utf-8"))
        record = parse_crossref(data["message"])
        self.assertEqual(record.doi.lower(), "10.1109/tip.2019.2955241")
        self.assertEqual(record.year, 2020)
        self.assertEqual(record.authors[0], "Chongyi Li")
        self.assertIn("IEEE Transactions on Image Processing", record.venue)


if __name__ == "__main__":
    unittest.main()
