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


async def _fetch(client: httpx.AsyncClient, url: str) -> Dict[str, Any]:
    """Check a URL without downloading the body.

    Uses ``HEAD`` (no body) and falls back to a **streamed** GET that is closed
    before reading the body — so large files (zips, PDFs) can't cause a
    ``ReadTimeout``.
    """
    try:
        response = await client.head(url, follow_redirects=True)
        status = response.status_code
        if status in (405, 501):  # HEAD not allowed -> streamed GET
            async with client.stream("GET", url, follow_redirects=True) as streamed:
                status = streamed.status_code
        return {"status": status, "error": None}
    except Exception:
        try:
            async with client.stream("GET", url, follow_redirects=True) as streamed:
                return {"status": streamed.status_code, "error": None}
        except Exception as exc:  # noqa: BLE001 - report any transport failure
            return {"status": None, "error": f"{type(exc).__name__}: {str(exc)[:140]}"}


async def check_urls(
    urls: Sequence[str],
    cache_path: Optional[Path | str] = None,
    ttl: int = DEFAULT_TTL,
    timeout: float = 20.0,
    concurrency: int = 12,
    progress: Optional[Progress] = None,
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
                    result = await _fetch(client, url)
                    result["checked_at"] = now
                    return url, result

            for url, result in await asyncio.gather(*(worker(url) for url in pending)):
                results[url] = result
                # Only cache definitive responses; don't persist transient transport errors.
                if result.get("status") is not None:
                    cache[url] = result
                if progress:
                    progress.update(url)
        if cache_file:
            cache_file.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")

    return results


def classify(url: str, result: Dict[str, Any], allowlist: Optional[Dict[str, Any]] = None) -> str:
    """Return ``"ok"``, ``"warning"`` or ``"error"`` for one link result."""
    allowed = set((allowlist or {}).get("link_warnings") or [])
    status = result.get("status")
    if result.get("error"):
        return "warning" if url in allowed else "error"
    if status in (404, 410):
        return "warning" if url in allowed else "error"
    if status is not None and status >= 400:
        return "warning"
    if status is not None and 200 <= status < 400:
        return "ok"
    return "warning"


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
