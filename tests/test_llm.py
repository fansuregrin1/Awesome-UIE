"""Tests for the LLM enrichment module (offline, no API calls)."""

import argparse
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".dev_scripts"))

from uie import cli  # noqa: E402
from uie.llm import (  # noqa: E402
    LlmSuggestion,
    apply_llm_suggestions,
    build_prompt,
    parse_json,
    parse_suggestion,
    summarize_papers,
)
from uie.schema import Paper, PaperType  # noqa: E402


class FakeLlm:
    model = "fake-model"

    def __init__(self, response):
        self.response = response

    def complete_json(self, system, user):
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


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


class ParseJsonTest(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(parse_json('{"a": 1}'), {"a": 1})

    def test_code_fence(self):
        self.assertEqual(parse_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_embedded(self):
        self.assertEqual(parse_json('Sure! {"a": 1} ok'), {"a": 1})

    def test_invalid(self):
        with self.assertRaises(ValueError):
            parse_json("no json here")

    def test_empty(self):
        with self.assertRaises(ValueError):
            parse_json("")


class ParseSuggestionTest(unittest.TestCase):
    def test_filters_and_normalizes(self):
        parsed = parse_suggestion(
            {
                "tldr": '"A new method."',
                "type": "DeepLearning",
                "tags": ["Diffusion", "NotATag", "Diffusion", "Transformer"],
                "new_tags": ["low light", "my_tag", "Diffusion"],
            }
        )
        self.assertEqual(parsed["tldr"], "A new method.")
        self.assertEqual(parsed["type"], "DeepLearning")
        self.assertEqual(parsed["tags"], ["Diffusion", "Transformer"])
        self.assertEqual(parsed["new_tags"], ["Low-Light", "My-Tag"])

    def test_invalid_type(self):
        self.assertIsNone(parse_suggestion({"type": "Whatever"})["type"])

    def test_missing_keys(self):
        parsed = parse_suggestion({})
        self.assertIsNone(parsed["tldr"])
        self.assertIsNone(parsed["type"])
        self.assertEqual(parsed["tags"], [])
        self.assertEqual(parsed["new_tags"], [])

    def test_new_tags_capped(self):
        parsed = parse_suggestion({"new_tags": ["a", "b", "c", "d", "e", "f", "g"]})
        self.assertLessEqual(len(parsed["new_tags"]), 5)


class PromptTest(unittest.TestCase):
    def test_includes_abstract_and_vocabulary(self):
        system, user = build_prompt(make_paper(), "An abstract about diffusion.")
        self.assertIn("strict JSON", system)
        self.assertIn("An abstract about diffusion.", user)
        self.assertIn("Diffusion", user)
        self.assertIn("Traditional", user)

    def test_includes_current_tags(self):
        paper = make_paper(tags=["CNN"])
        _, user = build_prompt(paper, "abstract", current_tags=paper.tags)
        self.assertIn("Current tags: CNN", user)


class SummarizeTest(unittest.TestCase):
    RESPONSE = {
        "tldr": "A state-space model for efficient underwater image enhancement.",
        "type": "DeepLearning",
        "tags": ["Mamba"],
        "new_tags": [],
    }

    def test_produces_suggestion(self):
        result = summarize_papers([make_paper()], FakeLlm(self.RESPONSE), {"2024-watermamba": "abstract"})
        self.assertEqual(len(result.suggestions), 1)
        self.assertEqual(result.suggestions[0].tags, ["Mamba"])
        self.assertEqual(result.suggestions[0].model, "fake-model")

    def test_skips_without_abstract(self):
        result = summarize_papers([make_paper()], FakeLlm(self.RESPONSE), {})
        self.assertEqual(result.summary()["skipped_no_abstract"], 1)
        self.assertEqual(result.summary()["suggestions"], 0)

    def test_only_missing_skips_existing_tldr(self):
        paper = make_paper(tldr="already there")
        result = summarize_papers([paper], FakeLlm(self.RESPONSE), {paper.id: "abstract"})
        self.assertEqual(result.summary()["suggestions"], 0)

    def test_records_errors(self):
        result = summarize_papers(
            [make_paper()], FakeLlm(RuntimeError("boom")), {"2024-watermamba": "abstract"}
        )
        self.assertEqual(result.summary()["errors"], 1)

    def test_summary_counts(self):
        paper = make_paper()
        result = summarize_papers([paper], FakeLlm(self.RESPONSE), {paper.id: "abstract"})
        self.assertEqual(
            result.summary(),
            {"total": 1, "suggestions": 1, "skipped_no_abstract": 0, "errors": 0},
        )


class ApplyTest(unittest.TestCase):
    def test_apply_empty(self):
        self.assertEqual(apply_llm_suggestions([make_paper()], []), [])

    def test_replace_tags(self):
        paper = make_paper(tags=["CNN"])
        suggestion = LlmSuggestion(
            paper_id=paper.id,
            tldr=None,
            type="DeepLearning",
            tags=["Diffusion", "Transformer"],
            new_tags=[],
            model="m",
            current_tags=["CNN"],
        )
        applied = apply_llm_suggestions([paper], [suggestion], fields=("tags",), tag_mode="replace")
        self.assertEqual(paper.tags, ["Diffusion", "Transformer"])
        self.assertEqual([entry[1] for entry in applied], ["tags"])

    def test_merge_tags_keeps_existing(self):
        paper = make_paper(tags=["GAN"])
        suggestion = LlmSuggestion(
            paper_id=paper.id,
            tldr=None,
            type="DeepLearning",
            tags=["Domain-Adaptation", "Unsupervised"],
            new_tags=[],
            model="m",
            current_tags=["GAN"],
        )
        apply_llm_suggestions([paper], [suggestion], fields=("tags",), tag_mode="merge")
        self.assertEqual(paper.tags, ["GAN", "Domain-Adaptation", "Unsupervised"])

    def test_fill_only_keeps_existing_tags(self):
        paper = make_paper(tags=["CNN"])
        suggestion = LlmSuggestion(
            paper_id=paper.id,
            tldr=None,
            type=None,
            tags=["Diffusion"],
            new_tags=[],
            model="m",
            current_tags=["CNN"],
        )
        self.assertEqual(apply_llm_suggestions([paper], [suggestion], fields=("tags",)), [])
        self.assertEqual(paper.tags, ["CNN"])

    def test_fill_only(self):
        paper = make_paper(tldr="keep")
        suggestion = LlmSuggestion(
            paper_id=paper.id, tldr="new", type="DeepLearning", tags=["CNN"], new_tags=[], model="m"
        )
        applied = apply_llm_suggestions([paper], [suggestion], fields=("tldr", "tags"))
        self.assertEqual(paper.tldr, "keep")           # not overwritten
        self.assertEqual(paper.tags, ["CNN"])           # empty -> filled
        self.assertEqual([entry[1] for entry in applied], ["tags"])


class CmdLlmApplyTest(unittest.TestCase):
    def test_ids_apply_keeps_all_papers(self):
        papers = [make_paper(id="a"), make_paper(id="b"), make_paper(id="c")]

        class DummyProgress:
            def update(self, *args, **kwargs):
                pass

            def close(self):
                pass

        captured = {}
        args = argparse.Namespace(
            api_key_env="FAKE_KEY", ids="a", limit=None, refresh_abstracts=False,
            model=None, base_url=None, all=False, apply=True, fields="tldr",
            tag_mode="fill", report="r.md", json="r.json",
        )
        response = {"tldr": "one sentence", "type": "DeepLearning", "tags": []}

        with mock.patch.dict(os.environ, {"FAKE_KEY": "k"}), \
                mock.patch.object(cli, "_load_llm_config", return_value={}), \
                mock.patch.object(cli, "load_papers", return_value=papers), \
                mock.patch.object(cli, "HttpClient", lambda **kwargs: object()), \
                mock.patch.object(cli.enrich_module, "collect_abstracts", return_value={"a": "abs"}), \
                mock.patch.object(cli.llm_module, "LlmClient", lambda **kwargs: FakeLlm(response)), \
                mock.patch.object(
                    cli, "dump_papers", side_effect=lambda path, value: captured.update(papers=value)
                ), \
                mock.patch.object(cli, "render"), \
                mock.patch.object(cli.proposals, "write_llm", return_value=("m", "j")), \
                mock.patch.object(cli, "_progress", return_value=DummyProgress()):
            rc = cli.cmd_llm(args)

        self.assertEqual(rc, 0)
        # Regression: --ids/--limit must not drop the unselected papers on write-back.
        self.assertEqual([paper.id for paper in captured["papers"]], ["a", "b", "c"])
        self.assertEqual(captured["papers"][0].tldr, "one sentence")
        self.assertIsNone(captured["papers"][1].tldr)


if __name__ == "__main__":
    unittest.main()
