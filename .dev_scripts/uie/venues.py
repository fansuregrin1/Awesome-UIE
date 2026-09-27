"""Venue registry: map full journal/conference names to the collection's short codes.

Beyond name/ISSN/alias matching, each venue carries a *quality tier* (``A``/``B``/
``C``/``preprint``) and a snapshot of OpenAlex ``metrics``. Tiers are curated by
hand (conferences) or seeded from metrics; they drive the ``discover`` quality
gate. The registry lives in ``config/venues.yaml`` and is the single source of
truth for venue codes.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import yaml

DEFAULT_TIER = "unknown"
# Best -> worst. ``preprint`` ranks above ``C`` so arXiv papers pass a stricter
# gate than low-impact journals. Used by ``meets_min_tier``.
TIER_ORDER = {"A": 0, "B": 1, "preprint": 2, "C": 3, "unknown": 4}

HEADER = (
    "# Venue registry: full name / ISSN / aliases -> the short code used in the collection,\n"
    "# plus a quality `tier` (A/B/C/preprint) and OpenAlex `metrics`.\n"
    "# Refresh metrics with:  python -m uie.cli venues --refresh-metrics\n"
    "# Maintained via the `unknown-venue` validation warning.\n\n"
)


def normalize_venue(text: Optional[str]) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def normalize_issn(issn: Optional[str]) -> Optional[str]:
    if not issn:
        return None
    key = re.sub(r"[^0-9xX]", "", issn).lower()
    return key or None


@dataclass
class Venue:
    code: str
    name: str
    type: str = "other"
    issn: Optional[str] = None
    tier: str = DEFAULT_TIER
    rank: Dict[str, str] = field(default_factory=dict)
    metrics: Dict[str, object] = field(default_factory=dict)
    aliases: List[str] = field(default_factory=list)


class VenueRegistry:
    def __init__(self, venues: Sequence[Venue]) -> None:
        self.venues = list(venues)
        self._by_code: Dict[str, Venue] = {}
        self._by_issn: Dict[str, str] = {}
        self._by_name: Dict[str, str] = {}
        for venue in self.venues:
            self._by_code[venue.code] = venue
            issn = normalize_issn(venue.issn)
            if issn:
                self._by_issn[issn] = venue.code
            self._by_name[normalize_venue(venue.name)] = venue.code
            self._by_name.setdefault(normalize_venue(venue.code), venue.code)
            for alias in venue.aliases:
                key = normalize_venue(alias)
                if key:
                    self._by_name.setdefault(key, venue.code)

    def resolve(self, name: Optional[str], issn: Optional[str] = None) -> Tuple[Optional[str], str]:
        """Return ``(code, matched_by)``; ``code`` is None when unknown."""
        issn_key = normalize_issn(issn)
        if issn_key and issn_key in self._by_issn:
            return self._by_issn[issn_key], "issn"
        name_key = normalize_venue(name)
        if name_key and name_key in self._by_name:
            return self._by_name[name_key], "name"
        return None, ""

    def known(self, code: Optional[str]) -> bool:
        return bool(code) and code in self._by_code

    def tier(self, code: Optional[str]) -> str:
        venue = self._by_code.get(code or "")
        return venue.tier if venue else DEFAULT_TIER

    def meets_min_tier(self, code: Optional[str], min_tier: Optional[str]) -> bool:
        if not min_tier:
            return True
        return TIER_ORDER.get(self.tier(code), 99) <= TIER_ORDER.get(min_tier, 99)

    @property
    def codes(self) -> set:
        return set(self._by_code)


def load_registry(path: Path | str) -> Optional[VenueRegistry]:
    path = Path(path)
    if not path.exists():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    venues = [
        Venue(
            code=item["code"],
            name=item.get("name") or item["code"],
            type=item.get("type", "other"),
            issn=item.get("issn"),
            tier=item.get("tier") or DEFAULT_TIER,
            rank=dict(item.get("rank") or {}),
            metrics=dict(item.get("metrics") or {}),
            aliases=list(item.get("aliases") or []),
        )
        for item in raw
    ]
    return VenueRegistry(venues)


def save_registry(path: Path | str, registry: VenueRegistry) -> None:
    """Write the registry back (tool-managed file, keeps a stable field order)."""
    entries = [
        {
            "code": venue.code,
            "name": venue.name,
            "type": venue.type,
            "issn": venue.issn,
            "tier": venue.tier,
            "rank": venue.rank,
            "metrics": venue.metrics,
            "aliases": venue.aliases,
        }
        for venue in registry.venues
    ]
    Path(path).write_text(
        HEADER + yaml.safe_dump(entries, sort_keys=False, allow_unicode=True, width=4096),
        encoding="utf-8",
    )
