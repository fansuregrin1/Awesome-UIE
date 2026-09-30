"""HTTP client factories.

``httpx`` supports HTTP(S) proxies natively but needs the optional ``socksio``
package for SOCKS proxies. These helpers keep ``trust_env=True`` (so proxies are
honoured) while turning a missing-``socksio`` failure into an actionable error,
and allow opting out of the environment with ``UIE_TRUST_ENV=0``.
"""

from __future__ import annotations

import importlib.util
import os
from typing import Any

import httpx

_SOCKS_SCHEMES = ("socks4://", "socks4a://", "socks5://", "socks5h://")
_PROXY_ENV = ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")
_GUIDANCE = (
    "A SOCKS proxy is configured but the 'socksio' package is missing. "
    'Install it with:  pip install "httpx[socks]"  (or `pip install socksio`); '
    "or unset ALL_PROXY; or set NO_PROXY=* to bypass the proxy."
)


def trust_env() -> bool:
    """Whether to honour proxy/CA environment variables (default: yes)."""
    value = os.environ.get("UIE_TRUST_ENV", "1").strip().lower()
    return value not in ("0", "false", "no", "off")


def _socks_proxy_configured() -> bool:
    for name in _PROXY_ENV:
        value = os.environ.get(name)
        if value and value.strip().lower().startswith(_SOCKS_SCHEMES):
            return True
    return False


def _ensure_socks_support() -> None:
    if _socks_proxy_configured() and importlib.util.find_spec("socksio") is None:
        raise RuntimeError(_GUIDANCE)


def _with_defaults(kwargs: dict) -> dict:
    kwargs.setdefault("trust_env", trust_env())
    return kwargs


def make_client(**kwargs: Any) -> httpx.Client:
    _ensure_socks_support()
    try:
        return httpx.Client(**_with_defaults(dict(kwargs)))
    except ImportError as exc:  # pragma: no cover - defensive (httpx lazy transport)
        if "socks" in str(exc).lower():
            raise RuntimeError(_GUIDANCE) from exc
        raise


def make_async_client(**kwargs: Any) -> httpx.AsyncClient:
    _ensure_socks_support()
    try:
        return httpx.AsyncClient(**_with_defaults(dict(kwargs)))
    except ImportError as exc:  # pragma: no cover - defensive (httpx lazy transport)
        if "socks" in str(exc).lower():
            raise RuntimeError(_GUIDANCE) from exc
        raise
