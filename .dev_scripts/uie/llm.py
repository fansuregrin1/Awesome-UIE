"""LLM-assisted enrichment: one-sentence TL;DRs and controlled-vocabulary tags.

Opt-in and report-first. The command requires an API key, caches every model
response on disk and never edits ``papers.yaml`` unless ``--apply`` is passed.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .net import make_client
from .progress import Progress
from .schema import KNOWN_TAGS, Paper, PaperType

TYPES = [PaperType.TRADITIONAL.value, PaperType.DEEP_LEARNING.value, PaperType.HYBRID.value]

SYSTEM_PROMPT = (
    "You are a meticulous curator of a collection of underwater image enhancement "
    "(UIE) papers. You reply with strict JSON only, no prose, no code fences."
)


@dataclass
class LlmSuggestion:
    paper_id: str
    tldr: Optional[str]
    type: Optional[str]
    tags: List[str]
    new_tags: List[str]
    model: str
    current_tags: List[str] = field(default_factory=list)


@dataclass
class LlmResult:
    total: int = 0
    suggestions: List[LlmSuggestion] = field(default_factory=list)
    skipped_no_abstract: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def summary(self) -> Dict[str, int]:
        return {
            "total": self.total,
            "suggestions": len(self.suggestions),
            "skipped_no_abstract": len(self.skipped_no_abstract),
            "errors": len(self.errors),
        }


def parse_json(text: str) -> dict:
    """Extract a JSON object from a model response (tolerating code fences)."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            return json.loads(match.group(0))
    raise ValueError("could not parse JSON from model output")


class LlmClient:
    """Minimal OpenAI-compatible chat-completions client with a disk cache."""

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        cache_dir: Optional[Path | str] = None,
        ttl: int = 30 * 24 * 3600,
        temperature: float = 0.0,
        timeout: float = 60.0,
    ) -> None:
        if not api_key:
            raise ValueError("missing API key")
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl
        self.temperature = temperature
        self._client = make_client(timeout=timeout)

    def _cache_path(self, key: str) -> Optional[Path]:
        if not self.cache_dir:
            return None
        return self.cache_dir / f"llm-{key[:40]}.json"

    def complete(self, system: str, user: str) -> str:
        request = json.dumps(
            {"model": self.model, "system": system, "user": user, "temperature": self.temperature},
            sort_keys=True,
        )
        key = hashlib.sha256(request.encode("utf-8")).hexdigest()
        cache_path = self._cache_path(key)
        if cache_path and cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if time.time() - cached.get("fetched_at", 0) < self.ttl:
                    return cached["content"]
            except (OSError, json.JSONDecodeError, KeyError):
                pass

        response = self._client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={
                "model": self.model,
                "temperature": self.temperature,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]

        if cache_path:
            cache_path.write_text(
                json.dumps({"fetched_at": time.time(), "model": self.model, "content": content}),
                encoding="utf-8",
            )
        return content

    def complete_json(self, system: str, user: str) -> dict:
        return parse_json(self.complete(system, user))


def build_prompt(
    paper: Paper,
    abstract: str,
    known_tags: Sequence[str] = KNOWN_TAGS,
    max_tags: int = 5,
    current_tags: Optional[Sequence[str]] = None,
) -> tuple:
    vocabulary = ", ".join(known_tags)
    current = ", ".join(current_tags) if current_tags else "(none)"
    user = (
        f"Title: {paper.title}\n"
        f"Year: {paper.year}\n"
        f"Venue: {paper.venue}\n"
        f"Abstract: {abstract}\n\n"
        f"Current tags: {current}\n\n"
        "Review the current tags and return a corrected set.\n"
        "Return a JSON object with these keys:\n"
        '- "tldr": one sentence (at most 30 words) describing the method and its main contribution. '
        "No marketing language, no first person.\n"
        f'- "type": exactly one of {TYPES}, defined as: '
        "Traditional = a classical / physical-model method with no learned network; "
        "DeepLearning = an end-to-end learned network is the core contribution; "
        "Hybrid = explicitly combines a physical or classical model/prior with a learned component. "
        "Pick the one that best matches the paper's main method.\n"
        f'- "tags": the final recommended tags (at most {max_tags}), chosen only from this '
        f"controlled vocabulary: {vocabulary}. "
        "Keep a current tag only if the title/abstract supports it, drop it otherwise, and add any missing ones.\n"
        '- "new_tags": an array of any additional useful tags not in the vocabulary, each '
        "lowercase words joined with hyphens (may be empty)."
    )
    return SYSTEM_PROMPT, user


def parse_suggestion(data: dict) -> dict:
    tldr = data.get("tldr")
    tldr = tldr.strip().strip('"').strip() if isinstance(tldr, str) else None

    paper_type = data.get("type")
    if paper_type not in TYPES:
        paper_type = None

    tags: List[str] = []
    for tag in data.get("tags") or []:
        if isinstance(tag, str) and tag in KNOWN_TAGS and tag not in tags:
            tags.append(tag)

    new_tags: List[str] = []
    for tag in data.get("new_tags") or []:
        if not isinstance(tag, str):
            continue
        slug = re.sub(r"[^A-Za-z0-9]+", "-", tag).strip("-")
        slug = "-".join(part.capitalize() for part in slug.split("-") if part)
        if slug and slug not in KNOWN_TAGS and slug not in new_tags:
            new_tags.append(slug)

    return {"tldr": tldr, "type": paper_type, "tags": tags, "new_tags": new_tags[:5]}


def summarize_papers(
    papers: Sequence[Paper],
    llm: object,
    abstracts: Dict[str, str],
    only_missing: bool = True,
    known_tags: Sequence[str] = KNOWN_TAGS,
    max_tags: int = 5,
    progress: Optional[Progress] = None,
) -> LlmResult:
    result = LlmResult(total=len(papers))
    for paper in papers:
        if only_missing and paper.tldr:
            continue
        abstract = abstracts.get(paper.id)
        if not abstract:
            result.skipped_no_abstract.append(paper.id)
            if progress:
                progress.update(paper.id)
            continue
        system, user = build_prompt(
            paper, abstract, known_tags=known_tags, max_tags=max_tags, current_tags=paper.tags
        )
        try:
            data = llm.complete_json(system, user)
        except Exception as exc:  # noqa: BLE001 - report per-paper failures
            result.errors.append(f"{paper.id}: {exc}")
            if progress:
                progress.update(paper.id)
            continue
        parsed = parse_suggestion(data)
        result.suggestions.append(
            LlmSuggestion(
                paper_id=paper.id,
                model=getattr(llm, "model", ""),
                current_tags=list(paper.tags),
                **parsed,
            )
        )
        if progress:
            progress.update(paper.id)
    return result


def apply_llm_suggestions(
    papers: Sequence[Paper],
    suggestions: Sequence[LlmSuggestion],
    fields: Sequence[str] = ("tldr",),
    tag_mode: str = "fill",
    type_mode: str = "fill",
) -> List[tuple]:
    """Apply ``tldr`` / ``type`` / ``tags`` suggestions in place.

    ``tldr`` is always fill-only. ``type_mode`` is ``fill`` (only when unset) or
    ``replace``. ``tag_mode`` controls tags: ``fill`` (only when empty), ``merge``
    (union with the reviewed set) or ``replace`` (use the reviewed set verbatim).
    """
    by_id = {paper.id: paper for paper in papers}
    wanted = set(fields)
    applied: List[tuple] = []
    for suggestion in suggestions:
        paper = by_id.get(suggestion.paper_id)
        if paper is None:
            continue
        if "tldr" in wanted and suggestion.tldr and not paper.tldr:
            paper.tldr = suggestion.tldr
            applied.append((paper.id, "tldr", suggestion.tldr))
        if "type" in wanted and suggestion.type:
            current = paper.type.value if paper.type else None
            if suggestion.type != current and (type_mode == "replace" or current is None):
                paper.type = PaperType(suggestion.type)
                applied.append((paper.id, "type", suggestion.type))
        if "tags" not in wanted or not suggestion.tags:
            continue
        if tag_mode == "replace":
            new_tags = list(suggestion.tags)
        elif tag_mode == "merge":
            new_tags = list(paper.tags)
            for tag in suggestion.tags:
                if tag not in new_tags:
                    new_tags.append(tag)
        elif paper.tags:
            new_tags = list(paper.tags)
        else:
            new_tags = list(suggestion.tags)
        if list(paper.tags) != new_tags:
            paper.tags = new_tags
            applied.append((paper.id, "tags", new_tags))
    return applied
