"""Crossref API client."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import SourceRecord
from .http import HttpClient

API = "https://api.crossref.org/works"
MIN_INTERVAL = 0.15
SELECT = "DOI,title,author,container-title,issued,published,published-print,published-online,type"


def _text(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _extract_year(message: Dict[str, Any]) -> Optional[int]:
    for key in ("published", "published-print", "published-online", "issued"):
        parts = (message.get(key) or {}).get("date-parts")
        if parts and parts[0] and parts[0][0]:
            return parts[0][0]
    return None


def _extract_date(message: Dict[str, Any]) -> Optional[str]:
    for key in ("published", "published-print", "published-online", "issued", "created"):
        parts = (message.get(key) or {}).get("date-parts")
        if parts and parts[0] and parts[0][0]:
            values = list(parts[0]) + [1, 1]
            return "%04d-%02d-%02d" % (values[0], values[1], values[2])
    return None


def parse_work(message: Dict[str, Any]) -> SourceRecord:
    authors = []
    for author in message.get("author", []) or []:
        name = " ".join(part for part in [author.get("given"), author.get("family")] if part)
        name = name or author.get("name")
        if name:
            authors.append(name)
    doi = _text(message.get("DOI"))
    titles = message.get("title") or []
    container = message.get("container-title") or []
    issns = message.get("ISSN") or []
    short_titles = message.get("short-container-title") or []
    return SourceRecord(
        source="crossref",
        title=_text(titles[0]) if titles else None,
        year=_extract_year(message),
        date=_extract_date(message),
        venue=_text(container[0]) if container else None,
        venue_short=_text(short_titles[0]) if short_titles else None,
        issn=_text(issns[0]) if issns else None,
        doi=doi,
        authors=authors,
        url=f"https://doi.org/{doi}" if doi else None,
    )


class CrossrefSource:
    name = "crossref"

    def __init__(self, client: HttpClient) -> None:
        self.client = client

    def lookup(
        self,
        doi: Optional[str] = None,
        arxiv_id: Optional[str] = None,
        title: Optional[str] = None,
        year: Optional[int] = None,
    ) -> List[SourceRecord]:
        if doi:
            try:
                data = self.client.get_json(f"{API}/{doi}", min_interval=MIN_INTERVAL)
            except RuntimeError:
                return []
            message = data.get("message")
            return [parse_work(message)] if message else []

        if not title:
            return []
        params: Dict[str, Any] = {"query.bibliographic": title, "rows": 5, "select": SELECT}
        if self.client.mailto:
            params["mailto"] = self.client.mailto
        data = self.client.get_json(API, params=params, min_interval=MIN_INTERVAL)
        items = (data.get("message") or {}).get("items", []) or []
        return [parse_work(item) for item in items]

    def search(self, query: str, since: Optional[str] = None, limit: int = 25) -> List[SourceRecord]:
        params: Dict[str, Any] = {
            "query.bibliographic": query,
            "rows": limit,
            "select": SELECT,
        }
        if since:
            params["filter"] = f"from-pub-date:{since}"
        if self.client.mailto:
            params["mailto"] = self.client.mailto
        data = self.client.get_json(API, params=params, min_interval=MIN_INTERVAL)
        items = (data.get("message") or {}).get("items", []) or []
        return [parse_work(item) for item in items]
