"""Tests for the link checker's body-less fetch (offline, via MockTransport)."""

import asyncio
import sys
import unittest
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".dev_scripts"))

from uie import links  # noqa: E402


class FetchTest(unittest.TestCase):
    def _fetch(self, handler, url="https://example.com/code.zip"):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return asyncio.run(links._fetch(client, url))

    def test_uses_head_and_does_not_read_body(self):
        seen = {}

        def handler(request):
            seen["method"] = request.method
            return httpx.Response(200, headers={"content-length": "694700"})

        result = self._fetch(handler)
        self.assertEqual(result["status"], 200)
        self.assertEqual(seen["method"], "HEAD")

    def test_falls_back_to_streamed_get_when_head_disallowed(self):
        seen = []

        def handler(request):
            seen.append(request.method)
            if request.method == "HEAD":
                return httpx.Response(405)
            return httpx.Response(200)

        result = self._fetch(handler)
        self.assertEqual(result["status"], 200)
        self.assertEqual(seen, ["HEAD", "GET"])

    def test_network_error_is_reported(self):
        def handler(request):
            raise httpx.ConnectError("boom")

        result = self._fetch(handler)
        self.assertIsNone(result["status"])
        self.assertIn("ConnectError", result["error"])


class RepoClassifyTest(unittest.TestCase):
    def test_ok_repo(self):
        result = links._classify_repo(
            "a/b", httpx.Response(200, json={"archived": True, "stargazers_count": 5})
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["archived"])
        self.assertEqual(result["stars"], 5)

    def test_moved_repo_is_ok(self):
        result = links._classify_repo("a/b", httpx.Response(301))
        self.assertTrue(result["ok"])
        self.assertTrue(result["moved"])

    def test_404_is_missing(self):
        result = links._classify_repo("a/b", httpx.Response(404))
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "missing")

    def test_403_with_exhausted_budget_is_rate_limited(self):
        result = links._classify_repo("a/b", httpx.Response(403, headers={"x-ratelimit-remaining": "0"}))
        self.assertEqual(result["reason"], "rate_limited")

    def test_429_is_rate_limited(self):
        result = links._classify_repo("a/b", httpx.Response(429))
        self.assertEqual(result["reason"], "rate_limited")

    def test_500_is_generic_error(self):
        result = links._classify_repo("a/b", httpx.Response(500))
        self.assertEqual(result["reason"], "http_error")


if __name__ == "__main__":
    unittest.main()
