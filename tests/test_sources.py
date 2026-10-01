"""Offline parser tests for the metadata sources (no network)."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".dev_scripts"))

from uie.sources.arxiv import ArxivSource, parse_feed  # noqa: E402
from uie.sources.crossref import CrossrefSource, parse_work as parse_crossref  # noqa: E402
from uie.sources.openalex import OpenAlexSource, parse_work as parse_openalex  # noqa: E402
from uie.sources.semantic_scholar import parse_paper as parse_s2  # noqa: E402

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


class SemanticScholarParseTest(unittest.TestCase):
    def test_parse_doi_lookup(self):
        data = json.loads((FIXTURES / "semantic_scholar_doi.json").read_text(encoding="utf-8"))
        record = parse_s2(data)
        self.assertIn("Underwater scene prior", record.title)
        self.assertIsNotNone(record.abstract)
        self.assertEqual(record.doi, "10.1016/j.patcog.2019.107038")
        self.assertTrue(record.authors)


class SearchWindowTest(unittest.TestCase):
    class _Client:
        def __init__(self, text="{}", data=None):
            self.text = text
            self.data = data if data is not None else {}
            self.calls = []
            self.mailto = None

        def get_text(self, url, params=None, **kwargs):
            self.calls.append(params)
            return self.text

        def get_json(self, url, params=None, **kwargs):
            self.calls.append(params)
            return self.data

    def test_arxiv_submitted_date_range(self):
        client = self._Client(text="<feed xmlns='http://www.w3.org/2005/Atom'></feed>")
        ArxivSource(client).search("q", since="2004-01-01", until="2006-12-31")
        self.assertIn("submittedDate:[200401010000 TO 200612312359]", client.calls[0]["search_query"])

    def test_openalex_date_range(self):
        client = self._Client(data={"results": []})
        OpenAlexSource(client).search("q", since="2004-01-01", until="2006-12-31")
        self.assertIn("from_publication_date:2004-01-01", client.calls[0]["filter"])
        self.assertIn("to_publication_date:2006-12-31", client.calls[0]["filter"])

    def test_crossref_date_range(self):
        client = self._Client(data={"message": {"items": []}})
        CrossrefSource(client).search("q", since="2004-01-01", until="2006-12-31")
        self.assertIn("from-pub-date:2004-01-01", client.calls[0]["filter"])
        self.assertIn("until-pub-date:2006-12-31", client.calls[0]["filter"])


if __name__ == "__main__":
    unittest.main()
