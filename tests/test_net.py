"""Tests for the HTTP client factories / proxy handling (offline)."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".dev_scripts"))

from uie import net  # noqa: E402


class TrustEnvTest(unittest.TestCase):
    def test_default_is_true(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(net.trust_env())

    def test_disabled_values(self):
        for value in ("0", "false", "no", "off"):
            with mock.patch.dict(os.environ, {"UIE_TRUST_ENV": value}, clear=True):
                self.assertFalse(net.trust_env())


class SocksSupportTest(unittest.TestCase):
    def test_no_proxy_needs_nothing(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            net._ensure_socks_support()  # must not raise

    def test_socks_without_socksio_raises_with_guidance(self):
        env = {"ALL_PROXY": "socks5://127.0.0.1:1080"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch(
            "importlib.util.find_spec", return_value=None
        ):
            with self.assertRaises(RuntimeError) as context:
                net._ensure_socks_support()
            self.assertIn("httpx[socks]", str(context.exception))

    def test_socks_with_socksio_is_ok(self):
        env = {"ALL_PROXY": "socks5://127.0.0.1:1080"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch(
            "importlib.util.find_spec", return_value=object()
        ):
            net._ensure_socks_support()  # must not raise

    def test_http_proxy_is_not_socks(self):
        env = {"HTTPS_PROXY": "http://127.0.0.1:7890"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch(
            "importlib.util.find_spec", return_value=None
        ):
            net._ensure_socks_support()  # http proxy needs no socksio


if __name__ == "__main__":
    unittest.main()
