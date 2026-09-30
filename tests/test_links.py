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


class ClassifyKindTest(unittest.TestCase):
    def test_host_blocked_suffix(self):
        self.assertTrue(links._host_blocked("https://www.mdpi.com/2079", ["mdpi.com"]))
        self.assertFalse(links._host_blocked("https://example.com", ["mdpi.com"]))

    def test_result_kind(self):
        self.assertEqual(links._result_kind("https://x/y", {"status": 200, "cloudflare": False}, []), "ok")
        self.assertEqual(links._result_kind("https://x/y", {"status": 404, "cloudflare": False}, []), "dead")
        self.assertEqual(links._result_kind("https://x/y", {"status": None, "cloudflare": False}, []), "error")
        self.assertEqual(links._result_kind("https://x/y", {"status": 500, "cloudflare": False}, []), "http_error")
        # blocked via cloudflare signature / via host list
        self.assertEqual(links._result_kind("https://x/y", {"status": 403, "cloudflare": True}, []), "blocked")
        self.assertEqual(
            links._result_kind("https://www.mdpi.com/a", {"status": 403, "cloudflare": False}, ["mdpi.com"]),
            "blocked",
        )

    def test_classify_severity(self):
        self.assertEqual(links.classify("u", {"kind": "ok", "status": 200}), "ok")
        self.assertEqual(links.classify("u", {"kind": "blocked", "status": 403}), "warning")
        self.assertEqual(links.classify("u", {"kind": "dead", "status": 404}), "error")
        self.assertEqual(links.classify("u", {"status": 404}), "error")  # legacy cache entry

    def test_extract_doi(self):
        self.assertEqual(links.extract_doi("https://doi.org/10.3390/jmse13081546"), "10.3390/jmse13081546")
        self.assertEqual(
            links.extract_doi("https://dl.acm.org/doi/10.1145/3581783.3611727"), "10.1145/3581783.3611727"
        )
        self.assertIsNone(links.extract_doi("https://example.com/x"))


class BrowserUaRetryTest(unittest.TestCase):
    def test_retries_blocked_with_browser_ua(self):
        def handler(request):
            if "Mozilla" in request.headers.get("user-agent", ""):
                return httpx.Response(200)
            return httpx.Response(403, headers={"server": "cloudflare"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        result = asyncio.run(
            links._fetch(client, "https://dl.acm.org/doi/10.1/x", blocked_hosts=[], browser_ua_retry=True)
        )
        self.assertEqual(result["kind"], "ok")
        self.assertEqual(result["note"], "via browser UA")


class VerifyDoiTest(unittest.TestCase):
    class _Client:
        def __init__(self, ok):
            self.ok = ok

        def get_text(self, url, **kwargs):
            if not self.ok:
                raise RuntimeError("HTTP 404")
            return "{}"

    def test_ok(self):
        self.assertTrue(links.verify_doi("10.1/x", self._Client(True)))

    def test_failure(self):
        self.assertFalse(links.verify_doi("10.1/x", self._Client(False)))


if __name__ == "__main__":
    unittest.main()
