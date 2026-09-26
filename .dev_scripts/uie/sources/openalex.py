"""OpenAlex API client."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import SourceRecord
from .http import HttpClient

API = "https://api.openalex.org/works"
MIN_INTERVAL = 0.15


def _abstract_from_inverted(inverted: Optional[Dict[str, List[int]]]) -> Optional[str]:
    if not inverted:
        return None
    positions: List[tuple] = []
    for word, indexes in inverted.items():
        for index in indexes:
            positions.append((index, word))
    if not positions:
        return None
    positions.sort()
    return " ".join(word for _, word in positions)


def _strip_doi(doi: Optional[str]) -> Optional[str]:
    if not doi:
        return None
    return doi[len("https://doi.org/"):] if doi.startswith("https://doi.org/") else doi


def parse_work(work: Dict[str, Any]) -> SourceRecord:
    authors = [
        authorship.get("author", {}).get("display_name")
        for authorship in work.get("authorships", []) or []
    ]
    source = (work.get("primary_location") or {}).get("source") or {}
    issns = source.get("issn") or []
    issn = source.get("issn_l") or (issns[0] if issns else None)
    return SourceRecord(
        source="openalex",
        title=work.get("display_name"),
        year=work.get("publication_year"),
        date=work.get("publication_date"),
        venue=source.get("display_name"),
        venue_short=source.get("abbreviated_title"),
        issn=issn,
        doi=_strip_doi(work.get("doi")),
        authors=[name for name in authors if name],
        abstract=_abstract_from_inverted(work.get("abstract_inverted_index")),
        url=work.get("id"),
    )


class OpenAlexSource:
    name = "openalex"

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
                data = self.client.get_json(f"{API}/https://doi.org/{doi}", min_interval=MIN_INTERVAL)
            except RuntimeError:
                return []
            return [parse_work(data)]

        if not title:
            return []
        params: Dict[str, Any] = {"search": title, "per_page": 5}
        if self.client.mailto:
            params["mailto"] = self.client.mailto
        data = self.client.get_json(API, params=params, min_interval=MIN_INTERVAL)
        return [parse_work(work) for work in data.get("results", []) or []]

    def search(self, query: str, since: Optional[str] = None, limit: int = 25) -> List[SourceRecord]:
        params: Dict[str, Any] = {"search": query, "per_page": limit, "sort": "publication_date:desc"}
        if since:
            params["filter"] = f"from_publication_date:{since}"
        if self.client.mailto:
            params["mailto"] = self.client.mailto
        data = self.client.get_json(API, params=params, min_interval=MIN_INTERVAL)
        return [parse_work(work) for work in data.get("results", []) or []]
