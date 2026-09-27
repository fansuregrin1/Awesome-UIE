"""Tests for the code/project finder (offline)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".dev_scripts"))

from uie.code import (  # noqa: E402
    CodeMatch,
    _clean_url,
    apply_code_matches,
    is_repo_url,
    method_token,
    score_repo,
)
from uie.schema import Paper, PaperType  # noqa: E402


def make_paper(**overrides):
    base = dict(
        id="2024-watermamba",
        title="WaterMamba: Visual State Space Model for Underwater Image Enhancement",
        year=2024,
        venue="arXiv",
        type=PaperType.DEEP_LEARNING,
        url="https://arxiv.org/abs/2405.08419",
    )
    base.update(overrides)
    return Paper(**base)


class MethodTokenTest(unittest.TestCase):
    def test_extracts_leading_acronym(self):
        self.assertEqual(method_token("WaterMamba: Visual State Space Model"), "WaterMamba")
        self.assertEqual(method_token("PUGAN - Physical Model Guided"), "PUGAN")

    def test_none_for_generic_title(self):
        self.assertIsNone(method_token("Underwater Image Enhancement with Global-Local Networks"))


class ScoreRepoTest(unittest.TestCase):
    def test_matches_method_name(self):
        repo = {"name": "WaterMamba", "description": "Official code", "topics": []}
        score, reason = score_repo(make_paper(), repo)
        self.assertGreaterEqual(score, 0.6)
        self.assertIn("WaterMamba", reason)

    def test_arxiv_id_bonus(self):
        repo = {"name": "some-repo", "description": "paper 2405.08419 implementation", "topics": []}
        score, _ = score_repo(make_paper(arxiv_id="2405.08419"), repo)
        self.assertGreaterEqual(score, 0.9)

    def test_unrelated_repo_is_low(self):
        repo = {"name": "cooking-recipes", "description": "nothing to do here", "topics": []}
        score, _ = score_repo(make_paper(), repo)
        self.assertLess(score, 0.55)


class CleanUrlTest(unittest.TestCase):
    def test_strips_trailing_punctuation(self):
        self.assertEqual(_clean_url("https://github.com/a/b)."), "https://github.com/a/b")
        self.assertEqual(_clean_url("https://github.com/a/b."), "https://github.com/a/b")


class RepoUrlTest(unittest.TestCase):
    def test_requires_owner_and_repo(self):
        self.assertTrue(is_repo_url("https://github.com/a/b"))
        self.assertTrue(is_repo_url("https://gitee.com/a/b.git"))
        self.assertFalse(is_repo_url("https://github.com/iN1k1/"))
        self.assertFalse(is_repo_url("https://example.com/a/b"))


class ApplyTest(unittest.TestCase):
    def test_fill_only(self):
        filled = make_paper(id="p1")
        existing = make_paper(id="p2", code="https://github.com/orig/repo")
        matches = [
            CodeMatch("p1", "https://github.com/a/b", "https://a.github.io/b/", "a/b", 0.9, "method", "github"),
            CodeMatch("p2", "https://github.com/x/y", None, "x/y", 0.9, "method", "github"),
        ]
        applied = apply_code_matches([filled, existing], matches)
        self.assertEqual(str(filled.code), "https://github.com/a/b")
        self.assertEqual(str(filled.project), "https://a.github.io/b/")
        self.assertEqual(str(existing.code), "https://github.com/orig/repo")  # not overwritten
        self.assertEqual([entry[1] for entry in applied], ["code", "project"])


if __name__ == "__main__":
    unittest.main()
