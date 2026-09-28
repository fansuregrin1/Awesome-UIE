"""Tests for the generated README stats and SVG charts (offline)."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".dev_scripts"))

from uie.render import render_json, render_type_svg, render_year_svg  # noqa: E402
from uie.schema import Paper, PaperType  # noqa: E402


def make_paper(paper_id, year, type_=PaperType.DEEP_LEARNING, code=None):
    return Paper(
        id=paper_id,
        title=f"Underwater Image Enhancement {paper_id}",
        year=year,
        venue="arXiv",
        type=type_,
        url="https://arxiv.org/abs/2405.08419",
        code=code,
    )


class StatsTest(unittest.TestCase):
    def setUp(self):
        self.papers = [
            make_paper("p1", 2023, PaperType.TRADITIONAL, code="https://github.com/a/b"),
            make_paper("p2", 2023, PaperType.DEEP_LEARNING),
            make_paper("p3", 2025, PaperType.HYBRID),
        ]

    def test_render_json_stats(self):
        payload = json.loads(render_json(self.papers))
        self.assertEqual(payload["count"], 3)
        self.assertEqual(payload["stats"]["count"], 3)
        self.assertEqual(payload["stats"]["with_code"], 1)
        self.assertEqual(payload["stats"]["year_range"], "2023-2025")

    def test_year_svg(self):
        svg = render_year_svg(self.papers)
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("Papers per year", svg)
        self.assertIn("2023: 2", svg)

    def test_type_svg(self):
        svg = render_type_svg(self.papers)
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("By type", svg)
        self.assertIn("Deep learning", svg)


if __name__ == "__main__":
    unittest.main()
