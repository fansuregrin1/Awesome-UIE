"""Shared HTTP client with on-disk caching, retries and per-host throttling.

Every response is cached under ``.dev_scripts/.cache/`` keyed by the full request
URL, so repeated runs (and enrichment across many papers) do not re-hit the APIs.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

DEFAULT_TTL = 30 * 24 * 3600
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache"
USER_AGENT = "Awesome-UIE-enricher/1.0 (+https://github.com/fansuregrin1/Awesome-UIE)"


class HttpClient:
    def __init__(
        self,
        cache_dir: Optional[Path | str] = None,
        ttl: int = DEFAULT_TTL,
        mailto: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl
        self.mailto = mailto
        self._last_request: Dict[str, float] = {}
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            follow_redirects=True,
        )

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
        return self.cache_dir / f"{digest}.json"

    def _throttle(self, host: str, min_interval: float) -> None:
        if min_interval <= 0:
            return
        wait = min_interval - (time.time() - self._last_request.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        self._last_request[host] = time.time()

    def get_text(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        min_interval: float = 0.0,
        retries: int = 3,
        headers: Optional[Dict[str, str]] = None,
    ) -> str:
        request = httpx.Request("GET", url, params=params)
        full_url = str(request.url)
        cache_path = self._cache_path(full_url)

        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if (
                    cached.get("status") == 200
                    and time.time() - cached.get("fetched_at", 0) < self.ttl
                ):
                    return cached["text"]
            except (OSError, json.JSONDecodeError, KeyError):
                pass

        host = httpx.URL(full_url).host or ""
        self._throttle(host, min_interval)

        last_error = "unknown error"
        for attempt in range(retries):
            try:
                response = self._client.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:  # network failure
                last_error = str(exc)
                time.sleep(1.0 * (attempt + 1))
                continue

            if response.status_code == 200:
                cache_path.write_text(
                    json.dumps(
                        {
                            "url": full_url,
                            "fetched_at": time.time(),
                            "status": 200,
                            "text": response.text,
                        }
                    ),
                    encoding="utf-8",
                )
                return response.text
            if response.status_code in (429, 500, 502, 503, 504):
                last_error = f"HTTP {response.status_code}"
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 3.0 * (attempt + 1)
                time.sleep(delay)
                continue
            last_error = f"HTTP {response.status_code}"
            break

        raise RuntimeError(f"GET {full_url} failed: {last_error}")

    def get_json(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        min_interval: float = 0.0,
        retries: int = 3,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        return json.loads(
            self.get_text(url, params=params, min_interval=min_interval, retries=retries, headers=headers)
        )
