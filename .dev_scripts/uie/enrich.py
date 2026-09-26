"""Match existing papers against metadata sources and suggest field updates.

This module is read-only with respect to ``papers.yaml``: it produces suggestions
(and reference material) that a human then applies manually.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Sequence

from .schema import Paper
from .sources.arxiv import ArxivSource
from .sources.base import SourceRecord
from .sources.crossref import CrossrefSource
from .sources.http import HttpClient
from .sources.openalex import OpenAlexSource

# Only these fields are proposed for automatic filling in Phase 1.
FILL_FIELDS = ("doi", "arxiv_id", "authors")

# Source trust per field (lower value = preferred).
SOURCE_PRIORITY: Dict[str, Dict[str, int]] = {
    "doi": {"crossref": 0, "openalex": 1},
    "arxiv_id": {"arxiv": 0, "openalex": 1},
    "authors": {"openalex": 0, "crossref": 1, "arxiv": 2},
}


def normalize_text(text: Optional[str]) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def similarity(a: Optional[str], b: Optional[str]) -> float:
    return SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()


def normalize_doi(doi: Optional[str]) -> Optional[str]:
    if not doi:
        return None
    doi = doi.strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.lower().startswith(prefix):
            doi = doi[len(prefix):]
    return doi.lower() or None


def normalize_arxiv(arxiv_id: Optional[str]) -> Optional[str]:
    if not arxiv_id:
        return None
    return re.sub(r"v\d+$", "", arxiv_id.strip())


@dataclass
class Candidate:
    record: SourceRecord
    score: float


@dataclass
class Suggestion:
    paper_id: str
    field: str
    current: object
    suggested: object
    source: str
    score: float


@dataclass
class MatchInfo:
    paper_id: str
    title: str
    score: float
    source: str
    candidates: int
    year_hints: List[int] = field(default_factory=list)


@dataclass
class EnrichResult:
    total: int = 0
    matched: List[MatchInfo] = field(default_factory=list)
    ambiguous: List[MatchInfo] = field(default_factory=list)
    unmatched: List[str] = field(default_factory=list)
    suggestions: List[Suggestion] = field(default_factory=list)
    references: Dict[str, str] = field(default_factory=dict)

    def summary(self) -> Dict[str, int]:
        return {
            "total": self.total,
            "matched": len(self.matched),
            "ambiguous": len(self.ambiguous),
            "unmatched": len(self.unmatched),
            "suggestions": len(self.suggestions),
        }


def build_sources(client: HttpClient) -> List[object]:
    return [ArxivSource(client), OpenAlexSource(client), CrossrefSource(client)]


def _lookup(source, paper: Paper) -> List[SourceRecord]:
    try:
        if getattr(source, "name", "") == "arxiv" and paper.arxiv_id:
            return source.lookup(arxiv_id=paper.arxiv_id)
        return source.lookup(title=paper.title, year=paper.year)
    except RuntimeError:
        return []


def gather_candidates(paper: Paper, sources: Sequence[object]) -> List[Candidate]:
    candidates: List[Candidate] = []
    for source in sources:
        # arXiv is slow; only query it for papers known to be preprints.
        if getattr(source, "name", "") == "arxiv" and not (paper.arxiv_id or paper.venue == "arXiv"):
            continue
        for record in _lookup(source, paper):
            if not record.title:
                continue
            score = similarity(paper.title, record.title)
            if record.year and paper.year and abs(record.year - paper.year) <= 1:
                score = min(1.0, score + 0.02)
            candidates.append(Candidate(record, round(score, 4)))
    return candidates


def _is_empty(value: object) -> bool:
    return value in (None, "", [], {})


def _normalized(field_name: str, value: object) -> object:
    if field_name == "doi":
        return normalize_doi(value if isinstance(value, str) else None)
    if field_name == "arxiv_id":
        return normalize_arxiv(value if isinstance(value, str) else None)
    return value


def _record_value(record: SourceRecord, field_name: str) -> object:
    if field_name == "authors":
        return list(record.authors) if record.authors else None
    return getattr(record, field_name, None)


def _paper_value(paper: Paper, field_name: str) -> object:
    return getattr(paper, field_name, None)


def enrich_papers(
    papers: Sequence[Paper],
    client: Optional[HttpClient] = None,
    threshold: float = 0.9,
    ambiguous_margin: float = 0.15,
    sources: Optional[Sequence[object]] = None,
) -> EnrichResult:
    if sources is None:
        if client is None:
            raise ValueError("provide either client or sources")
        sources = build_sources(client)
    result = EnrichResult(total=len(papers))

    for paper in papers:
        candidates = gather_candidates(paper, sources)
        if not candidates:
            result.unmatched.append(paper.id)
            continue

        best = max(candidates, key=lambda candidate: candidate.score)
        accepted = [candidate for candidate in candidates if candidate.score >= threshold]
        info = MatchInfo(
            paper_id=paper.id,
            title=paper.title,
            score=best.score,
            source=best.record.source,
            candidates=len(candidates),
            year_hints=sorted(
                {
                    candidate.record.year
                    for candidate in accepted
                    if candidate.record.year and candidate.record.year != paper.year
                }
            ),
        )

        if not accepted:
            if best.score >= threshold - ambiguous_margin:
                result.ambiguous.append(info)
            else:
                result.unmatched.append(paper.id)
            continue

        result.matched.append(info)

        for field_name in FILL_FIELDS:
            options = [candidate for candidate in accepted if _record_value(candidate.record, field_name)]
            if field_name == "doi":
                # Skip DataCite arXiv DOIs (10.48550/...): not publisher DOIs, and
                # redundant when the entry already carries an arxiv_id.
                options = [
                    candidate
                    for candidate in options
                    if not str(candidate.record.doi or "").lower().startswith("10.48550/")
                ]
            if not options:
                continue
            options.sort(
                key=lambda candidate: (
                    SOURCE_PRIORITY[field_name].get(candidate.record.source, 9),
                    -candidate.score,
                )
            )
            chosen = options[0]
            suggested = _record_value(chosen.record, field_name)
            current = _paper_value(paper, field_name)
            if _normalized(field_name, suggested) == _normalized(field_name, current):
                continue
            if _is_empty(current):
                result.suggestions.append(
                    Suggestion(paper.id, field_name, current, suggested, chosen.record.source, chosen.score)
                )

        abstract_options = [candidate for candidate in accepted if candidate.record.abstract]
        if abstract_options:
            best_abstract = max(abstract_options, key=lambda candidate: candidate.score)
            result.references[paper.id] = best_abstract.record.abstract or ""

    return result


def apply_suggestions(
    papers: Sequence[Paper],
    result: EnrichResult,
    fields: Sequence[str] = FILL_FIELDS,
    min_score: float = 0.0,
) -> List[tuple]:
    """Apply high-confidence, fill-only suggestions in place.

    Only fills fields that are currently empty and only for suggestions at or
    above ``min_score``. Returns the list of applied ``(paper_id, field, value)``.
    """
    by_id = {paper.id: paper for paper in papers}
    wanted = set(fields)
    applied: List[tuple] = []
    for suggestion in result.suggestions:
        if suggestion.field not in wanted or suggestion.score < min_score:
            continue
        paper = by_id.get(suggestion.paper_id)
        if paper is None:
            continue
        if not _is_empty(getattr(paper, suggestion.field, None)):
            continue
        setattr(paper, suggestion.field, suggestion.suggested)
        applied.append((suggestion.paper_id, suggestion.field, suggestion.suggested))
    return applied
