"""Discover new papers from the metadata sources (Phase 2).

Keywords come from ``config/discovery.yaml``. Results are deduplicated against the
existing collection and scored for relevance; the output is a report of *new*
candidates (with a ready-to-paste YAML snippet) plus entries already present.
This module is read-only with respect to ``papers.yaml``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from .enrich import normalize_arxiv, normalize_doi, similarity
from .schema import KNOWN_TAGS, make_id
from .sources.arxiv import ArxivSource
from .sources.base import SourceRecord
from .sources.crossref import CrossrefSource
from .sources.http import HttpClient
from .sources.openalex import OpenAlexSource

TOKEN_RE = re.compile(r"[a-z0-9]+")
ANCHOR = "underwater"
STOPWORDS = {"of", "for", "and", "the", "a", "an", "in", "on", "with", "via", "based", "to"}
# A relevant title must mention the anchor AND one of these task words.
ACTION_TOKENS = {
    "image", "imaging", "enhancement", "enhance", "restoration", "restore",
    "dehaz", "dehazing", "color", "colour", "correction", "visibility", "vision", "quality",
}

# (needles, tag) rules. Only tags present in the controlled vocabulary are kept.
ARCH_TAG_RULES: List[Tuple[Tuple[str, ...], str]] = [
    (("cnn", "convolutional"), "CNN"),
    (("gan", "adversarial"), "GAN"),
    (("diffusion",), "Diffusion"),
    (("transformer",), "Transformer"),
    (("mamba", "state space model"), "Mamba"),
]
THEME_TAG_RULES: List[Tuple[Tuple[str, ...], str]] = [
    (("physical model", "formation model", "physics"), "Physical-Model"),
    (("color correction", "colour correction"), "Color-Correction"),
    (("dehaz",), "Dehazing"),
    (("real-time", "real time"), "Real-time"),
    (("lightweight", "light-weight"), "Lightweight"),
    (("semi-supervised",), "Semi-supervised"),
    (("self-supervised",), "Self-supervised"),
    (("unsupervised",), "Unsupervised"),
    (("domain adaptation", "domain-adaptation"), "Domain-Adaptation"),
    (("polarization",), "Polarization"),
]


@dataclass
class Candidate:
    record: SourceRecord
    relevance: float
    status: str  # "new" | "existing" | "similar"
    matched_id: Optional[str]
    suggested_type: str
    suggested_tags: List[str]
    suggested_id: str
    venue: str = ""
    venue_unknown: bool = False


@dataclass
class DiscoverResult:
    since: str
    queries: List[str]
    found: int = 0
    new: List[Candidate] = field(default_factory=list)
    similar: List[Candidate] = field(default_factory=list)
    existing: List[Candidate] = field(default_factory=list)
    unknown_venues: List[str] = field(default_factory=list)

    def summary(self) -> Dict[str, int]:
        return {
            "found": self.found,
            "new": len(self.new),
            "similar": len(self.similar),
            "existing": len(self.existing),
        }


def keyword_tokens(keywords: Sequence[str]) -> set:
    tokens = set()
    for keyword in keywords:
        for token in TOKEN_RE.findall(keyword.lower()):
            if token not in STOPWORDS:
                tokens.add(token)
    return tokens


def relevance(record: SourceRecord, tokens: set, anchor: str = ANCHOR) -> float:
    text = ((record.title or "") + " " + (record.abstract or "")).lower()
    words = set(TOKEN_RE.findall(text))
    if anchor not in words or not tokens:
        return 0.0
    return round(len(words & tokens) / len(tokens), 3)


def is_relevant(
    record: SourceRecord,
    tokens: set,
    anchor: str = ANCHOR,
    min_relevance: float = 0.25,
) -> Tuple[bool, float]:
    """A paper is relevant only if the anchor and a task word appear in its title."""
    title_words = set(TOKEN_RE.findall((record.title or "").lower()))
    if anchor not in title_words or not (title_words & ACTION_TOKENS):
        return False, 0.0
    score = relevance(record, tokens, anchor=anchor)
    return score >= min_relevance, score


def suggest_classification(record: SourceRecord) -> Tuple[str, List[str]]:
    text = ((record.title or "") + " " + (record.abstract or "")).lower()
    tags: List[str] = []
    for needles, tag in ARCH_TAG_RULES + THEME_TAG_RULES:
        if tag not in KNOWN_TAGS:
            continue
        if tag not in tags and any(needle in text for needle in needles):
            tags.append(tag)

    learning = any(word in text for word in ("network", "learning", "neural", "cnn", "gan", "transformer", "diffusion", "mamba", "deep"))
    physical = any(word in text for word in ("physical model", "formation model", "physics", "attenuation", "scattering"))
    classical = any(word in text for word in ("retinex", "histogram", "fusion", "dark channel", "prior"))

    if learning and physical:
        paper_type = "Hybrid"
    elif learning:
        paper_type = "DeepLearning"
    elif classical:
        paper_type = "Traditional"
    else:
        paper_type = "DeepLearning"
    return paper_type, tags


def build_index(papers: Sequence) -> Tuple[set, set, List[Tuple[str, str]]]:
    dois = {normalize_doi(paper.doi) for paper in papers if paper.doi}
    arxiv_ids = {normalize_arxiv(paper.arxiv_id) for paper in papers if paper.arxiv_id}
    titles = [(paper.id, paper.title) for paper in papers]
    return dois, arxiv_ids, titles


def _internal_key(record: SourceRecord) -> str:
    doi = normalize_doi(record.doi)
    if doi:
        return f"doi:{doi}"
    arxiv_id = normalize_arxiv(record.arxiv_id)
    if arxiv_id:
        return f"arxiv:{arxiv_id}"
    return "title:" + re.sub(r"[^a-z0-9]+", "", (record.title or "").lower())


def match_status(
    record: SourceRecord,
    dois: set,
    arxiv_ids: set,
    titles: Sequence[Tuple[str, str]],
    threshold: float = 0.9,
    margin: float = 0.05,
) -> Tuple[str, Optional[str]]:
    doi = normalize_doi(record.doi)
    if doi and doi in dois:
        return "existing", None
    arxiv_id = normalize_arxiv(record.arxiv_id)
    if arxiv_id and arxiv_id in arxiv_ids:
        return "existing", None

    best_id, best_score = None, 0.0
    if record.title:
        for paper_id, title in titles:
            score = similarity(record.title, title)
            if score > best_score:
                best_score, best_id = score, paper_id
    if best_score >= threshold:
        return "existing", best_id
    if best_score >= threshold - margin:
        return "similar", best_id
    return "new", None


def discover(
    papers: Sequence,
    config: Dict,
    client: Optional[HttpClient] = None,
    sources: Optional[Sequence[object]] = None,
    registry: Optional[object] = None,
    since_days: Optional[int] = None,
    limit_per_source: int = 25,
    min_relevance: float = 0.25,
    today: Optional[date] = None,
) -> DiscoverResult:
    if sources is None:
        if client is None:
            raise ValueError("provide either client or sources")
        sources = [ArxivSource(client), OpenAlexSource(client), CrossrefSource(client)]

    keywords = list(config.get("keywords") or [])
    tokens = keyword_tokens(keywords)
    if since_days is None:
        since_days = int(config.get("discover_since_days", 90))
    today = today or date.today()
    since = today - timedelta(days=since_days)
    since_iso = since.isoformat()

    dois, arxiv_ids, titles = build_index(papers)
    result = DiscoverResult(since=since_iso, queries=keywords)
    seen = set()
    unknown_venues = set()

    for source in sources:
        for keyword in keywords:
            try:
                records = source.search(keyword, since=since_iso, limit=limit_per_source)
            except RuntimeError:
                continue
            for record in records:
                if not record.title:
                    continue
                if record.date and record.date < since_iso:
                    continue
                if not record.date and record.year and record.year < since.year:
                    continue

                relevant, score = is_relevant(record, tokens, min_relevance=min_relevance)
                if not relevant:
                    continue

                key = _internal_key(record)
                if key in seen:
                    continue
                seen.add(key)

                status, matched = match_status(record, dois, arxiv_ids, titles)
                paper_type, tags = suggest_classification(record)

                code = None
                if registry is not None:
                    code, _ = registry.resolve(record.venue, record.issn)
                if record.source == "arxiv" and not record.venue:
                    venue_value, venue_unknown = "arXiv", False
                elif code:
                    venue_value, venue_unknown = code, False
                else:
                    venue_value = record.venue or record.source
                    venue_unknown = registry is not None
                if venue_unknown:
                    unknown_venues.add(venue_value)

                candidate = Candidate(
                    record=record,
                    relevance=score,
                    status=status,
                    matched_id=matched,
                    suggested_type=paper_type,
                    suggested_tags=tags,
                    suggested_id=make_id(record.year or today.year, record.title),
                    venue=venue_value,
                    venue_unknown=venue_unknown,
                )
                result.found += 1
                if status == "new":
                    result.new.append(candidate)
                elif status == "similar":
                    result.similar.append(candidate)
                else:
                    result.existing.append(candidate)

    result.new.sort(key=lambda candidate: (candidate.record.date or "", candidate.relevance), reverse=True)
    result.unknown_venues = sorted(unknown_venues)
    return result
