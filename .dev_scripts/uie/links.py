"""Network checks for paper links and code repositories.

Results are cached on disk (``.dev_scripts/.link-cache.json``) so that the full
list is not re-fetched on every run. Bot-protection / rate-limit responses are
downgraded to warnings; only definite 404/410 count as errors.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import httpx

from .net import make_async_client
from .progress import Progress

DEFAULT_TTL = 7 * 24 * 3600
USER_AGENT = "Awesome-UIE-link-checker/1.0 (+https://github.com/fansuregrin1/Awesome-UIE)"
_GITHUB_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/#?]+)")


BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
CROSSREF_WORKS = "https://api.crossref.org/works/"


def _host_blocked(url: str, hosts: Sequence[str]) -> bool:
    host = (httpx.URL(url).host or "").lower()
    return any(host == entry.lower() or host.endswith("." + entry.lower()) for entry in hosts)


def _is_antibot(status: Optional[int], headers) -> bool:
    if status not in (403, 429, 503):
        return False
    server = (headers.get("server") or "").lower()
    return "cloudflare" in server or "cf-mitigated" in headers


async def _probe(client: httpx.AsyncClient, url: str, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Return ``{"status", "error", "cloudflare"}`` without downloading the body."""
    try:
        response = await client.head(url, follow_redirects=True, headers=headers)
        if response.status_code in (405, 501):  # HEAD not allowed -> streamed GET
            async with client.stream("GET", url, follow_redirects=True, headers=headers) as streamed:
                return {
                    "status": streamed.status_code,
                    "error": None,
                    "cloudflare": _is_antibot(streamed.status_code, streamed.headers),
                }
        return {
            "status": response.status_code,
            "error": None,
            "cloudflare": _is_antibot(response.status_code, response.headers),
        }
    except Exception:
        try:
            async with client.stream("GET", url, follow_redirects=True, headers=headers) as streamed:
                return {
                    "status": streamed.status_code,
                    "error": None,
                    "cloudflare": _is_antibot(streamed.status_code, streamed.headers),
                }
        except Exception as exc:  # noqa: BLE001 - report any transport failure
            return {"status": None, "error": f"{type(exc).__name__}: {str(exc)[:140]}", "cloudflare": False}


def _result_kind(url: str, probe: Dict[str, Any], blocked_hosts: Sequence[str]) -> str:
    status = probe.get("status")
    if status is None:
        return "error"
    if status in (404, 410):
        return "dead"
    if 200 <= status < 400:
        return "ok"
    # 403/429/503 from publishers are almost always bot protection; also honour
    # the configured host list.
    if status in (403, 429, 503) or _host_blocked(url, blocked_hosts):
        return "blocked"
    if status >= 400:
        return "http_error"
    return "unknown"


async def _fetch(
    client: httpx.AsyncClient,
    url: str,
    blocked_hosts: Sequence[str] = (),
    browser_ua_retry: bool = False,
) -> Dict[str, Any]:
    """Check a URL without downloading its body.

    Uses ``HEAD`` (falling back to a streamed GET) and classifies the outcome as
    ``ok`` / ``dead`` / ``blocked`` (anti-bot) / ``http_error`` / ``error``. Bot
    blocks may be retried once with a browser-like User-Agent.
    """
    probe = await _probe(client, url)
    kind = _result_kind(url, probe, blocked_hosts)
    if kind == "blocked" and browser_ua_retry:
        retry = await _probe(client, url, headers=BROWSER_HEADERS)
        if retry.get("status") is not None and retry["status"] < 400:
            return {"status": retry["status"], "error": None, "kind": "ok", "note": "via browser UA"}
    return {"status": probe.get("status"), "error": probe.get("error"), "kind": kind}


def verify_doi(doi: str, client) -> bool:
    """Confirm a DOI is registered via Crossref (bypasses publisher anti-bot)."""
    try:
        client.get_text(f"{CROSSREF_WORKS}{doi}", min_interval=0.2)
        return True
    except Exception:  # noqa: BLE001
        return False


_DOI_IN_URL = re.compile(r"10\.\d{4,9}/[^\s?#]+", re.IGNORECASE)


def extract_doi(url: str) -> Optional[str]:
    """Pull a DOI out of a URL like ``https://doi.org/10.3390/jmse13081546``."""
    match = _DOI_IN_URL.search(url or "")
    return match.group(0).rstrip(".") if match else None


async def check_urls(
    urls: Sequence[str],
    cache_path: Optional[Path | str] = None,
    ttl: int = DEFAULT_TTL,
    timeout: float = 20.0,
    concurrency: int = 12,
    progress: Optional[Progress] = None,
    blocked_hosts: Sequence[str] = (),
    browser_ua_retry: bool = False,
) -> Dict[str, Dict[str, Any]]:
    urls = sorted(set(urls))
    cache: Dict[str, Dict[str, Any]] = {}
    cache_file = Path(cache_path) if cache_path else None
    if cache_file and cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cache = {}

    now = time.time()
    results: Dict[str, Dict[str, Any]] = {}
    pending: List[str] = []
    for url in urls:
        hit = cache.get(url)
        if hit and now - hit.get("checked_at", 0) < ttl:
            results[url] = hit
            if progress:
                progress.update(url)
        else:
            pending.append(url)

    if pending:
        limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
        headers = {"User-Agent": USER_AGENT}
        async with make_async_client(timeout=timeout, headers=headers, limits=limits) as client:
            semaphore = asyncio.Semaphore(concurrency)

            async def worker(url: str):
                async with semaphore:
                    result = await _fetch(client, url, blocked_hosts, browser_ua_retry)
                    result["checked_at"] = now
                    if progress:
                        progress.update(url)
                    return url, result

            for url, result in await asyncio.gather(*(worker(url) for url in pending)):
                results[url] = result
                # Only cache definitive responses; don't persist transient transport errors.
                if result.get("status") is not None:
                    cache[url] = result
        if cache_file:
            cache_file.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")

    return results


def classify(url: str, result: Dict[str, Any], allowlist: Optional[Dict[str, Any]] = None) -> str:
    """Return ``"ok"``, ``"warning"`` or ``"error"`` for one link result."""
    allowed = set((allowlist or {}).get("link_warnings") or [])
    kind = result.get("kind")
    if kind is None:  # backward-compatible derivation for old cache entries
        status = result.get("status")
        if status is None:
            kind = "error"
        elif status in (404, 410):
            kind = "dead"
        elif 200 <= status < 400:
            kind = "ok"
        else:
            kind = "http_error"
    if kind == "ok":
        return "ok"
    if kind in ("dead", "error"):
        return "warning" if url in allowed else "error"
    return "warning"  # blocked / http_error / unknown


def github_repo(url: str) -> Optional[str]:
    match = _GITHUB_RE.match(str(url))
    if not match:
        return None
    repo = match.group(2)
    if repo.endswith(".git"):
        repo = repo[:-4]
    return f"{match.group(1)}/{repo}"


def _classify_repo(repo: str, response: httpx.Response) -> Dict[str, Any]:
    """Turn a GitHub repo API response into a result dict (never raises)."""
    status = response.status_code
    if status == 200:
        data = response.json()
        return {
            "ok": True,
            "archived": bool(data.get("archived")),
            "stars": data.get("stargazers_count"),
            "pushed_at": data.get("pushed_at"),
        }
    # 301/302 mean the repository was renamed or moved, not deleted.
    if status in (301, 302, 307, 308):
        return {"ok": True, "moved": True, "status": status}
    if status == 404:
        return {"ok": False, "status": status, "reason": "missing"}
    # 403/429 (or an exhausted rate-limit budget) are transient, not "missing".
    if status in (403, 429) or response.headers.get("x-ratelimit-remaining") == "0":
        return {"ok": False, "status": status, "reason": "rate_limited"}
    return {"ok": False, "status": status, "reason": "http_error"}


async def check_github_repos(
    repos: Sequence[str],
    token: Optional[str] = None,
    timeout: float = 20.0,
    concurrency: int = 8,
) -> Dict[str, Dict[str, Any]]:
    repos = sorted(set(repos))
    if not repos:
        return {}
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    out: Dict[str, Dict[str, Any]] = {}
    async with make_async_client(
        timeout=timeout, headers=headers, limits=limits, follow_redirects=True
    ) as client:
        semaphore = asyncio.Semaphore(concurrency)

        async def worker(repo: str):
            async with semaphore:
                try:
                    response = await client.get(f"https://api.github.com/repos/{repo}")
                except Exception as exc:  # noqa: BLE001
                    return repo, {"ok": None, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
                return repo, _classify_repo(repo, response)

        for repo, result in await asyncio.gather(*(worker(repo) for repo in repos)):
            out[repo] = result
    return out
