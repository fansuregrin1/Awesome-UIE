"""arXiv API client (Atom feed)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import List, Optional

from .base import SourceRecord
from .http import HttpClient

API = "http://export.arxiv.org/api/query"
NS = {
    "a": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}
# arXiv asks for ~3s between requests.
MIN_INTERVAL = 3.0


def _text(element: Optional[ET.Element]) -> Optional[str]:
    if element is None or element.text is None:
        return None
    return re.sub(r"\s+", " ", element.text).strip()


def _strip_version(arxiv_id: str) -> str:
    return re.sub(r"v\d+$", "", arxiv_id)


def parse_feed(xml_text: str) -> List[SourceRecord]:
    root = ET.fromstring(xml_text)
    records: List[SourceRecord] = []
    for entry in root.findall("a:entry", NS):
        title = _text(entry.find("a:title", NS))
        if not title or title.lower() == "error":
            continue
        id_url = _text(entry.find("a:id", NS)) or ""
        arxiv_id = _strip_version(id_url.rsplit("/abs/", 1)[-1]) if "/abs/" in id_url else None
        published = _text(entry.find("a:published", NS)) or ""
        year = int(published[:4]) if published[:4].isdigit() else None
        authors = [
            name
            for name in (_text(author.find("a:name", NS)) for author in entry.findall("a:author", NS))
            if name
        ]
        records.append(
            SourceRecord(
                source="arxiv",
                title=title,
                year=year,
                date=published[:10] or None,
                venue=_text(entry.find("arxiv:journal_ref", NS)),
                doi=_text(entry.find("arxiv:doi", NS)),
                arxiv_id=arxiv_id,
                authors=authors,
                abstract=_text(entry.find("a:summary", NS)),
                url=f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else None,
            )
        )
    return records


class ArxivSource:
    name = "arxiv"

    def __init__(self, client: HttpClient) -> None:
        self.client = client

    def lookup(
        self,
        doi: Optional[str] = None,
        arxiv_id: Optional[str] = None,
        title: Optional[str] = None,
        year: Optional[int] = None,
    ) -> List[SourceRecord]:
        if arxiv_id:
            params = {"id_list": arxiv_id, "max_results": 1}
        elif title:
            params = {"search_query": f'ti:"{title}"', "max_results": 5}
        else:
            return []
        text = self.client.get_text(API, params=params, min_interval=MIN_INTERVAL)
        return parse_feed(text)

    def search(self, query: str, since: Optional[str] = None, limit: int = 25) -> List[SourceRecord]:
        params = {
            "search_query": f'all:"{query}"',
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": limit,
        }
        text = self.client.get_text(API, params=params, min_interval=MIN_INTERVAL)
        return parse_feed(text)
