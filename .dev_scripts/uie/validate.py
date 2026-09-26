"""Deterministic validation for ``papers.yaml``.

Only deterministic checks live here: schema, duplicates, controlled-vocabulary
tags and basic sanity. Network checks are in :mod:`uie.links`; semantic checks
by an LLM are intentionally out of scope.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import yaml
from pydantic import ValidationError

from .schema import KNOWN_TAGS, Paper


@dataclass
class Issue:
    level: str  # "error" | "warning"
    code: str
    message: str
    where: str = ""

    def as_dict(self) -> Dict[str, str]:
        return {"level": self.level, "code": self.code, "message": self.message, "where": self.where}


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def load_raw(path: Path | str) -> List[Any]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{path}: top level must be a list of papers")
    return raw


def load_and_collect(path: Path | str) -> Tuple[List[Paper], List[Issue]]:
    """Load papers, collecting schema errors as issues instead of raising."""
    issues: List[Issue] = []
    papers: List[Paper] = []
    for index, item in enumerate(load_raw(path)):
        try:
            papers.append(Paper.model_validate(item))
        except ValidationError as exc:
            rid = item.get("id") if isinstance(item, dict) else None
            where = f"index {index}" + (f" (id={rid})" if rid else "")
            for error in exc.errors():
                location = ".".join(str(part) for part in error["loc"])
                issues.append(Issue("error", "schema", f"{location}: {error['msg']}", where))
    return papers, issues


def validate_papers(
    papers: Sequence[Paper],
    allowlist: Optional[Dict[str, Any]] = None,
    registry: Optional[object] = None,
) -> List[Issue]:
    allowlist = allowlist or {}
    allowed_urls = set(allowlist.get("duplicate_urls") or [])
    issues: List[Issue] = []

    # duplicate ids
    seen: Dict[str, Paper] = {}
    for paper in papers:
        if paper.id in seen:
            issues.append(Issue("error", "duplicate-id", f"id also used by {seen[paper.id].title!r}", paper.id))
        else:
            seen[paper.id] = paper

    # duplicate URLs
    by_url: Dict[str, List[Paper]] = {}
    for paper in papers:
        by_url.setdefault(str(paper.url), []).append(paper)
    for url, group in by_url.items():
        if len(group) > 1 and url not in allowed_urls:
            ids = ", ".join(paper.id for paper in group)
            issues.append(Issue("error", "duplicate-url", f"URL shared by: {ids}", url))

    # similar titles (any year) -> warning
    by_title: Dict[str, List[Paper]] = {}
    for paper in papers:
        by_title.setdefault(_normalize(paper.title), []).append(paper)
    for group in by_title.values():
        if len(group) > 1:
            ids = ", ".join(paper.id for paper in group)
            issues.append(Issue("warning", "duplicate-title", f"similar title across entries: {ids}", ids))

    # same normalized title and year -> error
    by_title_year: Dict[Tuple[str, int], List[Paper]] = {}
    for paper in papers:
        by_title_year.setdefault((_normalize(paper.title), paper.year), []).append(paper)
    for group in by_title_year.values():
        if len(group) > 1:
            ids = ", ".join(paper.id for paper in group)
            issues.append(Issue("error", "duplicate-title-year", f"same title and year: {ids}", ids))

    # tags
    for paper in papers:
        for tag in sorted({tag for tag in paper.tags if paper.tags.count(tag) > 1}):
            issues.append(Issue("warning", "duplicate-tag", f"tag appears more than once: '{tag}'", paper.id))
        for tag in paper.tags:
            if tag not in KNOWN_TAGS:
                issues.append(Issue("warning", "unknown-tag", f"tag '{tag}' is not in the controlled vocabulary", paper.id))

    # duplicate code / project links
    for attribute in ("code", "project"):
        by_link: Dict[str, List[Paper]] = {}
        for paper in papers:
            value = getattr(paper, attribute)
            if value:
                by_link.setdefault(str(value), []).append(paper)
        for link, group in by_link.items():
            if len(group) > 1:
                ids = ", ".join(paper.id for paper in group)
                issues.append(Issue("warning", f"duplicate-{attribute}", f"{attribute} link shared by: {ids}", link))

    # venues not present in the registry (curation signal)
    if registry is not None:
        for paper in papers:
            if not registry.known(paper.venue):
                issues.append(
                    Issue("warning", "unknown-venue", f"venue '{paper.venue}' is not in venues.yaml", paper.id)
                )

    # basic title sanity
    for paper in papers:
        if len(paper.title) < 15:
            issues.append(Issue("warning", "short-title", f"title is short ({len(paper.title)} chars)", paper.id))
        elif len(paper.title) > 250:
            issues.append(Issue("warning", "long-title", f"title is very long ({len(paper.title)} chars)", paper.id))

    return issues


def changed_ids(rel_path: str, base_ref: str, repo_root: Path | str) -> Set[str]:
    """Return ids that are new or modified relative to ``base_ref``.

    Falls back to returning every id in the current file when the base revision
    is unavailable (e.g. shallow clone), so a full validation still runs.
    """
    current = load_raw(Path(repo_root) / rel_path)
    current_by_id = {item.get("id"): item for item in current if isinstance(item, dict)}

    result = subprocess.run(
        ["git", "show", f"{base_ref}:{rel_path}"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return set(current_by_id)
    try:
        base = yaml.safe_load(result.stdout) or []
    except yaml.YAMLError:
        return set(current_by_id)
    base_by_id = {item.get("id"): item for item in base if isinstance(item, dict)}

    changed: Set[str] = set()
    for identifier, item in current_by_id.items():
        if identifier not in base_by_id or base_by_id[identifier] != item:
            changed.add(identifier)
    return changed
