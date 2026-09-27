"""Semantic Scholar Graph API client.

Used mainly as an extra abstract source: publishers such as Elsevier often do not
expose abstracts through OpenAlex/Crossref, but Semantic Scholar frequently has
them. Key-less access is rate-limited (~1 req/s), so calls are throttled and
cached like every other source.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import SourceRecord
from .http import HttpClient

API = "https://api.semanticscholar.org/graph/v1/paper"
FIELDS = "title,abstract,venue,year,authors,externalIds,publicationTypes,paperId"
MIN_INTERVAL = 1.1


def _text(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def parse_paper(item: Dict[str, Any]) -> SourceRecord:
    external = item.get("externalIds") or {}
    authors = [author.get("name") for author in item.get("authors") or [] if author.get("name")]
    paper_id = item.get("paperId")
    return SourceRecord(
        source="semantic_scholar",
        title=_text(item.get("title")),
        year=item.get("year"),
        venue=_text(item.get("venue")),
        doi=_text(external.get("DOI")),
        arxiv_id=_text(external.get("ArXiv")),
        authors=authors,
        abstract=_text(item.get("abstract")),
        url=f"https://www.semanticscholar.org/paper/{paper_id}" if paper_id else None,
    )


class SemanticScholarSource:
    name = "semantic_scholar"

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
                data = self.client.get_json(
                    f"{API}/DOI:{doi}", params={"fields": FIELDS}, min_interval=MIN_INTERVAL
                )
            except RuntimeError:
                return []
            return [parse_paper(data)] if data and data.get("title") else []
        if arxiv_id:
            try:
                data = self.client.get_json(
                    f"{API}/arXiv:{arxiv_id}", params={"fields": FIELDS}, min_interval=MIN_INTERVAL
                )
            except RuntimeError:
                return []
            return [parse_paper(data)] if data and data.get("title") else []
        if not title:
            return []
        try:
            data = self.client.get_json(
                f"{API}/search",
                params={"query": title, "limit": 5, "fields": FIELDS},
                min_interval=MIN_INTERVAL,
            )
        except RuntimeError:
            return []
        return [parse_paper(item) for item in data.get("data", []) or []]

    def search(self, query: str, since: Optional[str] = None, limit: int = 25) -> List[SourceRecord]:
        try:
            data = self.client.get_json(
                f"{API}/search",
                params={"query": query, "limit": limit, "fields": FIELDS},
                min_interval=MIN_INTERVAL,
            )
        except RuntimeError:
            return []
        return [parse_paper(item) for item in data.get("data", []) or []]
