"""Opt-in LLM relevance scoring for discovery candidates.

Token-frugal by design: candidates are scored in **batches**, each item only sends
``title + abstract[:abstract_chars] + year``, and the model returns a compact JSON
array of scores. Responses are cached by the shared :class:`~uie.llm.LlmClient`.
"""

from __future__ import annotations

from typing import Any, List, Optional, Sequence

from .llm import parse_json
from .sources.base import SourceRecord

SYSTEM = (
    "You judge whether a paper is about underwater image enhancement, restoration, "
    "color correction or dehazing (UIE) — i.e. improving underwater images. "
    "Reply with strict JSON only."
)


def build_batch_prompt(records: Sequence[SourceRecord], abstract_chars: int = 300) -> tuple:
    items = []
    for index, record in enumerate(records):
        abstract = (record.abstract or "")[:abstract_chars]
        items.append(f"{index} | {record.year or '?'} | {record.title} | {abstract}")
    user = (
        "Score each item 0-1 for how clearly it is an underwater image enhancement paper "
        "(1 = clearly in scope, 0 = unrelated). Items are 'index | year | title | abstract'.\n\n"
        + "\n".join(items)
        + '\n\nReturn JSON: {"results": [{"i": 0, "s": 0.9}, ...]}. One entry per item (s = score 0-1).'
    )
    return SYSTEM, user


def parse_batch(text: str, count: int) -> List[Optional[float]]:
    scores: List[Optional[float]] = [None] * count
    try:
        data: Any = parse_json(text)
    except ValueError:
        return scores
    results = data.get("results") if isinstance(data, dict) else data
    if not isinstance(results, list):
        return scores
    for item in results:
        if not isinstance(item, dict):
            continue
        index = item.get("i")
        value = item.get("s", item.get("score"))
        if isinstance(index, int) and 0 <= index < count and isinstance(value, (int, float)):
            scores[index] = max(0.0, min(1.0, float(value)))
    return scores


def score_records(
    records: Sequence[SourceRecord],
    llm: object,
    batch: int = 10,
    abstract_chars: int = 300,
    progress=None,
) -> List[Optional[float]]:
    """Return a score (0-1) or ``None`` (on failure) for each record."""
    scores: List[Optional[float]] = [None] * len(records)
    for start in range(0, len(records), max(1, batch)):
        chunk = list(records[start:start + max(1, batch)])
        system, user = build_batch_prompt(chunk, abstract_chars=abstract_chars)
        try:
            parsed = parse_batch(llm.complete(system, user), len(chunk))
        except Exception:  # noqa: BLE001 - fall back to None on any failure
            parsed = [None] * len(chunk)
        for offset, value in enumerate(parsed):
            scores[start + offset] = value
            if progress:
                progress.update(chunk[offset].title[:48])
    return scores
