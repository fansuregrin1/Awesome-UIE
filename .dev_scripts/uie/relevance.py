"""Opt-in LLM assessment for discovery candidates.

Token-frugal by design:
- candidates are scored in **batches**, sending only ``title + abstract[:N] + year``;
- one batch call returns both a relevance **score** and a **type**;
- venue tiers are rated once per **unique venue** (a property of the venue, not the paper);
- responses are cached by the shared :class:`~uie.llm.LlmClient`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from .llm import parse_json
from .sources.base import SourceRecord

VALID_TYPES = {"Traditional", "DeepLearning", "Hybrid"}
VALID_TIERS = {"A", "B", "preprint", "C", "unknown"}

PAPER_SYSTEM = (
    "You assess papers for a curated list of underwater image enhancement (UIE) work. "
    "Reply with strict JSON only."
)
VENUE_SYSTEM = (
    "You rate the academic standing of publication venues. Reply with strict JSON only."
)


@dataclass
class Assessment:
    score: float
    type: Optional[str] = None


def build_batch_prompt(records: Sequence[SourceRecord], abstract_chars: int = 300) -> tuple:
    items = []
    for index, record in enumerate(records):
        abstract = (record.abstract or "")[:abstract_chars]
        items.append(f"{index} | {record.year or '?'} | {record.title} | {abstract}")
    user = (
        "For each item, score 0-1 how clearly it is an underwater image enhancement paper "
        "(1 = clearly in scope) and give its type (Traditional / DeepLearning / Hybrid). "
        "Items are 'index | year | title | abstract'.\n\n"
        + "\n".join(items)
        + '\n\nReturn JSON: {"results": [{"i": 0, "s": 0.9, "t": "Traditional"}, ...]}. '
        "One entry per item."
    )
    return PAPER_SYSTEM, user


def parse_batch(text: str, count: int) -> List[Optional[Assessment]]:
    results: List[Optional[Assessment]] = [None] * count
    try:
        data = parse_json(text)
    except ValueError:
        return results
    items = data.get("results") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return results
    for item in items:
        if not isinstance(item, dict):
            continue
        index = item.get("i")
        value = item.get("s", item.get("score"))
        if not (isinstance(index, int) and 0 <= index < count and isinstance(value, (int, float))):
            continue
        paper_type = item.get("t") or item.get("type")
        if paper_type not in VALID_TYPES:
            paper_type = None
        results[index] = Assessment(max(0.0, min(1.0, float(value))), paper_type)
    return results


def assess_records(
    records: Sequence[SourceRecord],
    llm: object,
    batch: int = 10,
    abstract_chars: int = 300,
    progress=None,
) -> List[Optional[Assessment]]:
    assessments: List[Optional[Assessment]] = [None] * len(records)
    for start in range(0, len(records), max(1, batch)):
        chunk = list(records[start:start + max(1, batch)])
        system, user = build_batch_prompt(chunk, abstract_chars=abstract_chars)
        try:
            parsed = parse_batch(llm.complete(system, user), len(chunk))
        except Exception:  # noqa: BLE001 - fall back to None on any failure
            parsed = [None] * len(chunk)
        for offset, value in enumerate(parsed):
            assessments[start + offset] = value
            if progress:
                progress.update(chunk[offset].title[:48])
    return assessments


def build_venue_prompt(venues: Sequence[str]) -> tuple:
    items = "\n".join(f"{index} | {venue}" for index, venue in enumerate(venues))
    user = (
        "Rate each venue's tier for a curated underwater-image-enhancement list: "
        "A (top / highly reputable), B (solid), C (low-impact), preprint, unknown. "
        "Base it on the venue's standing and impact in image processing / vision.\n\n"
        + items
        + '\n\nReturn JSON: {"results": [{"i": 0, "tier": "B"}, ...]}. One entry per venue.'
    )
    return VENUE_SYSTEM, user


def parse_venue_batch(text: str, venues: Sequence[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        data = parse_json(text)
    except ValueError:
        return out
    items = data.get("results") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        index = item.get("i")
        tier = item.get("tier", item.get("t"))
        if isinstance(index, int) and 0 <= index < len(venues) and tier in VALID_TIERS:
            out[venues[index]] = tier
    return out


def score_venues(
    venues: Sequence[str],
    llm: object,
    batch: int = 20,
    progress=None,
) -> Dict[str, str]:
    unique = list(dict.fromkeys(venues))
    tiers: Dict[str, str] = {}
    for start in range(0, len(unique), max(1, batch)):
        chunk = unique[start:start + max(1, batch)]
        system, user = build_venue_prompt(chunk)
        try:
            parsed = parse_venue_batch(llm.complete(system, user), chunk)
        except Exception:  # noqa: BLE001
            parsed = {}
        tiers.update(parsed)
        if progress:
            for venue in chunk:
                progress.update(venue[:48])
    return tiers
