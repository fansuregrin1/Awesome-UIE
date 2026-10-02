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

from . import relevance as relevance_module
from .enrich import normalize_arxiv, normalize_doi, similarity
from .progress import Progress
from .schema import KNOWN_TAGS, Paper, Status, make_id
from .sources.arxiv import ArxivSource
from .sources.base import SourceRecord
from .sources.crossref import CrossrefSource
from .sources.http import HttpClient
from .sources.openalex import OpenAlexSource
from .venues import DEFAULT_TIER, TIER_ORDER

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

# Needles that are word prefixes (e.g. "dehaz" -> dehazing/dehazed). Everything
# else is matched as a whole word so short needles like "gan" don't hit "organic".
_PREFIX_NEEDLES = {"dehaz"}


def _tag_pattern(needle: str):
    escaped = re.escape(needle)
    return re.compile(r"\b" + escaped) if needle in _PREFIX_NEEDLES else re.compile(r"\b" + escaped + r"\b")


TAG_PATTERNS: List[Tuple[Tuple, str]] = [
    (tuple(_tag_pattern(needle) for needle in needles), tag)
    for needles, tag in ARCH_TAG_RULES + THEME_TAG_RULES
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
    venue_tier: str = DEFAULT_TIER
    is_preprint: bool = False
    type_basis: str = ""
    llm_relevance: Optional[float] = None
    llm_type: Optional[str] = None


@dataclass
class DiscoverResult:
    since: str
    queries: List[str]
    until: str = ""
    years: List[int] = field(default_factory=list)
    found: int = 0
    new: List[Candidate] = field(default_factory=list)
    pending: List[Candidate] = field(default_factory=list)
    similar: List[Candidate] = field(default_factory=list)
    existing: List[Candidate] = field(default_factory=list)
    unknown_venues: List[str] = field(default_factory=list)
    venue_tiers: Dict[str, str] = field(default_factory=dict)
    source_errors: List[str] = field(default_factory=list)

    def summary(self) -> Dict[str, int]:
        return {
            "found": self.found,
            "new": len(self.new),
            "pending": len(self.pending),
            "similar": len(self.similar),
            "existing": len(self.existing),
            "source_errors": len(self.source_errors),
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


# Deep-learning UIE essentially begins around 2017; earlier papers without a
# deep-learning signal are almost always classical/physical methods.
DEEP_LEARNING_YEAR = 2016
_LEARNING_WORDS = (
    "network", "learning", "neural", "cnn", "gan",
    "transformer", "diffusion", "mamba", "deep",
)
_PHYSICAL_WORDS = ("physical model", "formation model", "physics", "attenuation", "scattering")
_CLASSICAL_WORDS = (
    "retinex", "histogram", "fusion", "dark channel", "prior",
    "polarization", "dehazing", "filter", "restoration model",
)
_LEARNING_RE = re.compile(r"\b(?:" + "|".join(re.escape(word) for word in _LEARNING_WORDS) + r")\b")
_PHYSICAL_RE = re.compile(r"\b(?:" + "|".join(re.escape(word) for word in _PHYSICAL_WORDS) + r")\b")
_CLASSICAL_RE = re.compile(r"\b(?:" + "|".join(re.escape(word) for word in _CLASSICAL_WORDS) + r")\b")


def suggest_classification(record: SourceRecord, year: Optional[int] = None) -> Tuple[str, List[str], str]:
    """Suggest ``(type, tags, basis)`` for a source record.

    ``basis`` is a short human-readable explanation of the decision. With no
    textual signal, the year decides (older papers → Traditional).
    """
    text = ((record.title or "") + " " + (record.abstract or "")).lower()
    tags: List[str] = []
    for patterns, tag in TAG_PATTERNS:
        if tag not in KNOWN_TAGS:
            continue
        if tag not in tags and any(pattern.search(text) for pattern in patterns):
            tags.append(tag)

    learning = bool(_LEARNING_RE.search(text))
    physical = bool(_PHYSICAL_RE.search(text))
    classical = bool(_CLASSICAL_RE.search(text))
    year = year if year is not None else record.year

    if learning and physical:
        return "Hybrid", tags, "physical + deep-learning signals"
    if learning:
        return "DeepLearning", tags, "deep-learning keywords"
    if physical or classical:
        return "Traditional", tags, "classical / physical-model keywords"
    if year and year <= DEEP_LEARNING_YEAR:
        return "Traditional", tags, f"no signals, year <= {DEEP_LEARNING_YEAR}"
    return "DeepLearning", tags, "default (recent paper, no signals)"


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


_SOURCE_RANK = {"crossref": 0, "openalex": 1, "arxiv": 2}


def is_preprint(record: SourceRecord) -> bool:
    """True when the record has no published venue (arXiv / unknown)."""
    venue = (record.venue or "").lower()
    return record.source == "arxiv" or "arxiv" in venue or not record.venue


def _merge_records(records: Sequence[SourceRecord]) -> List[SourceRecord]:
    """Merge the same paper across sources, preferring the published record.

    Records are grouped by normalized title; within a group the best record is a
    non-preprint one (Crossref > OpenAlex > arXiv), and missing ``doi``/``arxiv_id``/
    ``abstract``/``issn`` are copied over from the others.
    """
    groups: Dict[str, List[SourceRecord]] = {}
    order: List[str] = []
    for record in records:
        if not record.title:
            continue
        key = re.sub(r"[^a-z0-9]+", "", record.title.lower()) or _internal_key(record)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(record)

    merged: List[SourceRecord] = []
    for key in order:
        group = groups[key]
        best = min(group, key=lambda item: (is_preprint(item), _SOURCE_RANK.get(item.source, 9)))
        for field_name in ("doi", "arxiv_id", "abstract", "issn"):
            if not getattr(best, field_name):
                value = next((getattr(item, field_name) for item in group if getattr(item, field_name)), None)
                if value:
                    setattr(best, field_name, value)
        merged.append(best)
    return merged


def _resolve_published(record: SourceRecord, sources: Sequence[object]) -> Optional[SourceRecord]:
    """Look a record up by DOI on published sources; return a non-arXiv match."""
    if not record.doi:
        return None
    for source in sources:
        if getattr(source, "name", "") == "arxiv" or not hasattr(source, "lookup"):
            continue
        try:
            records = source.lookup(doi=record.doi)
        except RuntimeError:
            continue
        for candidate in records or []:
            if candidate.venue and "arxiv" not in candidate.venue.lower():
                return candidate
    return None


def discover(
    papers: Sequence,
    config: Dict,
    client: Optional[HttpClient] = None,
    sources: Optional[Sequence[object]] = None,
    registry: Optional[object] = None,
    min_tier: Optional[str] = None,
    years: Optional[Sequence[int]] = None,
    since_days: Optional[int] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit_per_source: int = 25,
    min_relevance: float = 0.25,
    prefer_published: bool = True,
    llm_relevance: Optional[object] = None,
    llm_min_relevance: float = 0.5,
    llm_auto_accept: float = 0.6,
    llm_band_low: float = 0.1,
    llm_batch: int = 10,
    llm_abstract_chars: int = 300,
    llm_venue_tier: bool = False,
    progress: Optional[Progress] = None,
    today: Optional[date] = None,
) -> DiscoverResult:
    if sources is None:
        if client is None:
            raise ValueError("provide either client or sources")
        sources = [ArxivSource(client), OpenAlexSource(client), CrossrefSource(client)]

    keywords = list(config.get("keywords") or [])
    tokens = keyword_tokens(keywords)
    today = today or date.today()
    if since:
        since_date = date.fromisoformat(since)
    else:
        if since_days is None:
            since_days = int(config.get("discover_since_days", 90))
        since_date = today - timedelta(days=since_days)
    until_date = date.fromisoformat(until) if until else today
    since_iso, until_iso = since_date.isoformat(), until_date.isoformat()

    dois, arxiv_ids, titles = build_index(papers)
    result = DiscoverResult(since=since_iso, until=until_iso, queries=keywords, years=list(years or []))
    seen = set()
    unknown_venues = set()

    gathered: List[SourceRecord] = []
    for source in sources:
        for keyword in keywords:
            try:
                records = source.search(keyword, since=since_iso, until=until_iso, limit=limit_per_source)
            except RuntimeError as exc:
                result.source_errors.append(f"{getattr(source, 'name', 'source')} '{keyword}': {exc}")
                records = []
            gathered.extend(records or [])
            if progress:
                progress.update(f"{getattr(source, 'name', 'source')}: {keyword}")

    in_window: List[SourceRecord] = []
    for record in gathered:
        if not record.title:
            continue
        if record.date:
            if record.date < since_iso or record.date > until_iso:
                continue
        elif record.year:
            if record.year < since_date.year or record.year > until_date.year:
                continue
        in_window.append(record)

    gated: List[tuple] = []
    for record in _merge_records(in_window):
        relevant, score = is_relevant(record, tokens, min_relevance=0.0)
        if not relevant:
            continue
        if years and record.year not in years:
            continue
        gated.append((record, score))

    # Optional LLM assessment: judge only the borderline band (token-frugal).
    llm_scores: List[Optional[float]] = [None] * len(gated)
    llm_types: List[Optional[str]] = [None] * len(gated)
    if llm_relevance is not None:
        band = [
            index
            for index, (_, rule_score) in enumerate(gated)
            if llm_band_low <= rule_score < llm_auto_accept
        ]
        if band:
            band_records = [gated[index][0] for index in band]
            assessments = relevance_module.assess_records(
                band_records, llm_relevance, batch=llm_batch, abstract_chars=llm_abstract_chars
            )
            for index, assessment in zip(band, assessments):
                if assessment is not None:
                    llm_scores[index] = assessment.score
                    llm_types[index] = assessment.type

    # Venue tiers (LLM) for venues missing from the registry, rated once per unique venue.
    venue_tiers: Dict[str, str] = {}
    if llm_relevance is not None and llm_venue_tier and registry is not None:
        def _venue_name(record: SourceRecord) -> str:
            name, _ = registry.resolve(record.venue, record.issn)
            if name:
                return name
            if is_preprint(record) and not record.venue:
                return "arXiv"
            return record.venue or record.source

        unregistered = {
            _venue_name(record)
            for record, _ in gated
            if _venue_name(record) != "arXiv" and not registry.known(_venue_name(record))
        }
        if unregistered:
            venue_tiers = relevance_module.score_venues(sorted(unregistered), llm_relevance)

    for index, (record, score) in enumerate(gated):
        if llm_relevance is not None:
            if score >= llm_auto_accept:
                pass
            elif score < llm_band_low:
                continue
            else:
                llm_score = llm_scores[index]
                if llm_score is not None:
                    if llm_score < llm_min_relevance:
                        continue
                elif score < min_relevance:
                    continue
        elif score < min_relevance:
            continue

        # A preprint that carries a DOI may have a published version: adopt its venue.
        if prefer_published and is_preprint(record) and record.doi:
            published = _resolve_published(record, sources)
            if published is not None:
                for field_name in ("venue", "issn", "doi"):
                    value = getattr(published, field_name, None)
                    if value:
                        setattr(record, field_name, value)

        key = _internal_key(record)
        if key in seen:
            continue
        seen.add(key)

        status, matched = match_status(record, dois, arxiv_ids, titles)
        paper_type, tags, type_basis = suggest_classification(record)
        if llm_types[index]:
            paper_type = llm_types[index]
            type_basis = f"llm, rule: {type_basis}" if type_basis else "llm"

        code = None
        if registry is not None:
            code, _ = registry.resolve(record.venue, record.issn)
        if is_preprint(record) and not record.venue:
            venue_value, venue_unknown = "arXiv", False
        elif code:
            venue_value, venue_unknown = code, False
        else:
            venue_value = record.venue or record.source
            venue_unknown = registry is not None
        if venue_unknown:
            unknown_venues.add(venue_value)
        venue_tier = registry.tier(venue_value) if registry is not None else DEFAULT_TIER
        if not (registry is not None and registry.known(venue_value)) and venue_value in venue_tiers:
            venue_tier = venue_tiers[venue_value]

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
            venue_tier=venue_tier,
            is_preprint=is_preprint(record),
            type_basis=type_basis,
            llm_relevance=llm_scores[index],
            llm_type=llm_types[index],
        )
        result.found += 1
        if status == "new":
            if min_tier and TIER_ORDER.get(venue_tier, 99) > TIER_ORDER.get(min_tier, 99):
                result.pending.append(candidate)
            else:
                result.new.append(candidate)
        elif status == "similar":
            result.similar.append(candidate)
        else:
            result.existing.append(candidate)

    # published candidates first, then by date/relevance
    for group in (result.new, result.pending):
        group.sort(key=lambda candidate: (candidate.record.date or "", candidate.relevance), reverse=True)
        group.sort(key=lambda candidate: candidate.is_preprint)
    result.unknown_venues = sorted(unknown_venues)
    result.venue_tiers = venue_tiers
    return result


def candidate_to_paper(
    candidate: Candidate,
    existing_ids: set,
    today: Optional[date] = None,
) -> Optional[Paper]:
    """Convert a new candidate into a ``status: candidate`` Paper, or None if unusable."""
    record = candidate.record
    url = f"https://doi.org/{record.doi}" if record.doi else record.url
    if not url or not record.year or not (2000 <= record.year <= 2100):
        return None

    paper_id = candidate.suggested_id or make_id(record.year, record.title)
    if paper_id in existing_ids:
        suffix = 2
        while f"{paper_id}-{suffix}" in existing_ids:
            suffix += 1
        paper_id = f"{paper_id}-{suffix}"
    existing_ids.add(paper_id)

    return Paper(
        id=paper_id,
        title=record.title,
        year=record.year,
        venue=candidate.venue,
        type=candidate.suggested_type,
        tags=list(candidate.suggested_tags),
        url=url,
        doi=record.doi,
        arxiv_id=record.arxiv_id,
        authors=list(record.authors),
        status=Status.CANDIDATE,
        added=today or date.today(),
    )


def candidates_to_papers(
    papers: Sequence, result: DiscoverResult, today: Optional[date] = None
) -> List[Paper]:
    """Turn the new candidates of a discovery run into candidate papers (not added)."""
    existing_ids = {paper.id for paper in papers}
    added: List[Paper] = []
    for candidate in result.new:
        paper = candidate_to_paper(candidate, existing_ids, today=today)
        if paper is not None:
            added.append(paper)
    return added
