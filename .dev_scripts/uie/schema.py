"""Data model and controlled vocabularies for the Awesome-UIE collection.

``type`` is the coarse methodological paradigm (only three values, stable over
time). ``tags`` carries the finer-grained information (architecture, paper kind,
theme, ...) and is intentionally small and extensible so that an LLM can later
propose new tags for human review.
"""

from __future__ import annotations

import re
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class PaperType(str, Enum):
    """Coarse methodological paradigm. Deliberately limited to three values."""

    TRADITIONAL = "Traditional"
    DEEP_LEARNING = "DeepLearning"
    HYBRID = "Hybrid"


# Display / sort order within a year (index == rank). Follows the agreed
# numbering 1-Traditional, 2-DeepLearning, 3-Hybrid.
TYPE_ORDER: List[PaperType] = [
    PaperType.TRADITIONAL,
    PaperType.DEEP_LEARNING,
    PaperType.HYBRID,
]
_TYPE_RANK: Dict[PaperType, int] = {t: i for i, t in enumerate(TYPE_ORDER)}


class Status(str, Enum):
    """Curation state of an entry."""

    VERIFIED = "verified"      # reviewed by a human, part of the main list
    CANDIDATE = "candidate"    # auto/user discovered, not reviewed yet


# Controlled tag vocabulary. Unknown tags are reported as warnings by the
# validator so that LLM-proposed tags can be reviewed before being added here.
KNOWN_TAGS: List[str] = [
    # architectures / paradigms (migrated from the old `Type` column)
    "CNN",
    "GAN",
    "Diffusion",
    "Transformer",
    "Mamba",
    "Large-Model",
    "Task-Driven",
    # paper kinds
    "Survey",
    "Benchmark",
    # suggested future tags (extend after review)
    "Physical-Model",
    "Color-Correction",
    "Dehazing",
    "Contrast-Enhancement",
    "Real-time",
    "Lightweight",
    "Unsupervised",
    "Semi-supervised",
    "Self-supervised",
    "Domain-Adaptation",
    "Polarization",
    "Foundation-Model",
]


class Paper(BaseModel):
    """A single curated paper."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str = Field(min_length=1)
    year: int = Field(ge=2000, le=2100)
    venue: str = Field(min_length=1)
    type: PaperType
    tags: List[str] = Field(default_factory=list)
    url: HttpUrl
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    code: Optional[HttpUrl] = None
    project: Optional[HttpUrl] = None
    authors: List[str] = Field(default_factory=list)
    abstract: Optional[str] = None
    status: Status = Status.VERIFIED
    added: Optional[date] = None
    notes: Optional[str] = None

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
            raise ValueError("id must be a lowercase hyphenated slug, e.g. '2024-watermamba'")
        return value

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, value: List[str]) -> List[str]:
        return [tag.strip() for tag in value if tag and tag.strip()]

    @property
    def type_rank(self) -> int:
        return _TYPE_RANK[self.type]

    def sort_key(self):
        """Deterministic ordering: year desc, type rank, venue, title."""
        return (-self.year, self.type_rank, self.venue.lower(), self.title.lower())


def slugify(text: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].strip("-")


def make_id(year: int, title: str) -> str:
    return f"{year}-{slugify(title)}"


def load_papers(path: Path | str) -> List[Paper]:
    """Load and strictly validate the YAML database."""
    raw: Any = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{path}: top level must be a list of papers")
    return [Paper.model_validate(item) for item in raw]


def dump_papers(path: Path | str, papers: List[Paper]) -> None:
    """Write the YAML database, preserving field order."""
    data = [paper.model_dump(mode="json") for paper in papers]
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=4096)
    Path(path).write_text(text, encoding="utf-8")
