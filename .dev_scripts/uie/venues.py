"""Venue registry: map full journal/conference names to the collection's short codes.

The APIs only expose full names (and ISO abbreviations), never the community
short name used here (``TIP``, ``CVPR``, ...). The registry in
``config/venues.yaml`` is therefore the single source of truth: it maps a
canonical ``code`` to a full ``name`` plus aliases and, where available, an ISSN
for stable matching.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import yaml


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
    aliases: List[str] = field(default_factory=list)


class VenueRegistry:
    def __init__(self, venues: Sequence[Venue]) -> None:
        self.venues = list(venues)
        self._by_issn: Dict[str, str] = {}
        self._by_name: Dict[str, str] = {}
        self._codes = set()
        for venue in self.venues:
            self._codes.add(venue.code)
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
        return bool(code) and code in self._codes

    @property
    def codes(self) -> set:
        return set(self._codes)


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
            aliases=list(item.get("aliases") or []),
        )
        for item in raw
    ]
    return VenueRegistry(venues)
