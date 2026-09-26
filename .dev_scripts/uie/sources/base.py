"""Normalized record returned by every metadata source."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class SourceRecord:
    """A single paper as reported by one external source."""

    source: str
    title: Optional[str] = None
    year: Optional[int] = None
    date: Optional[str] = None  # ISO publication date (YYYY-MM-DD)
    venue: Optional[str] = None
    venue_short: Optional[str] = None
    issn: Optional[str] = None
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    authors: List[str] = field(default_factory=list)
    abstract: Optional[str] = None
    url: Optional[str] = None
    score: Optional[float] = None
