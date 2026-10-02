"""Render enrichment reports as Markdown and JSON.

Reports are written under ``proposals/`` (gitignored). Nothing here touches
``papers.yaml`` — a human applies the suggestions.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Dict, List

import yaml

from .code import CodeMatch
from .discover import Candidate, DiscoverResult
from .enrich import EnrichResult
from .llm import LlmResult


def _fmt(value: object) -> str:
    if value in (None, "", [], {}):
        return "—"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def render_markdown(result: EnrichResult, titles: Dict[str, str], abstract_chars: int = 700) -> str:
    summary = result.summary()
    lines: list[str] = ["# Enrichment report", ""]
    lines.append(
        f"{summary['total']} papers · matched {summary['matched']} · "
        f"ambiguous {summary['ambiguous']} · unmatched {summary['unmatched']} · "
        f"suggestions {summary['suggestions']}"
    )
    lines.append("")

    by_paper: Dict[str, list] = {}
    for suggestion in result.suggestions:
        by_paper.setdefault(suggestion.paper_id, []).append(suggestion)

    lines.append("## Suggested updates")
    lines.append("")
    if not by_paper:
        lines.append("_none_")
        lines.append("")
    for paper_id in sorted(by_paper):
        match = next((item for item in result.matched if item.paper_id == paper_id), None)
        lines.append(f"### `{paper_id}`")
        lines.append(f"_{titles.get(paper_id, '')}_")
        lines.append("")
        if match:
            lines.append(f"- match: {match.source} (score {match.score:.2f})")
            if match.year_hints:
                lines.append(f"- year hints: {', '.join(str(year) for year in match.year_hints)}")
        lines.append("")
        lines.append("| field | current | suggested | source |")
        lines.append("| --- | --- | --- | --- |")
        for suggestion in by_paper[paper_id]:
            lines.append(
                f"| `{suggestion.field}` | {_fmt(suggestion.current)} | "
                f"{_fmt(suggestion.suggested)} | {suggestion.source} |"
            )
        lines.append("")
        reference = result.references.get(paper_id)
        if reference:
            text = reference[:abstract_chars] + ("…" if len(reference) > abstract_chars else "")
            lines.append("<details><summary>reference abstract (not stored)</summary>")
            lines.append("")
            lines.append(text)
            lines.append("")
            lines.append("</details>")
            lines.append("")

    lines.append("## Ambiguous matches")
    lines.append("")
    if result.ambiguous:
        for item in result.ambiguous:
            lines.append(
                f"- `{item.paper_id}` best {item.score:.2f} via {item.source} — {titles.get(item.paper_id, '')}"
            )
    else:
        lines.append("_none_")
    lines.append("")

    lines.append("## Unmatched")
    lines.append("")
    if result.unmatched:
        for paper_id in result.unmatched:
            lines.append(f"- `{paper_id}` — {titles.get(paper_id, '')}")
    else:
        lines.append("_none_")
    lines.append("")

    return "\n".join(lines)


def render_json(result: EnrichResult) -> str:
    payload = {
        "summary": result.summary(),
        "matched": [asdict(item) for item in result.matched],
        "ambiguous": [asdict(item) for item in result.ambiguous],
        "unmatched": result.unmatched,
        "suggestions": [asdict(item) for item in result.suggestions],
        "references": result.references,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def write(result: EnrichResult, titles: Dict[str, str], md_path, json_path) -> tuple[Path, Path]:
    md_path = Path(md_path)
    json_path = Path(json_path)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(result, titles), encoding="utf-8")
    json_path.write_text(render_json(result), encoding="utf-8")
    return md_path, json_path


def _candidate_dict(candidate: Candidate) -> Dict:
    record = candidate.record
    return {
        "id": candidate.suggested_id,
        "title": record.title,
        "year": record.year,
        "venue": candidate.venue,
        "type": candidate.suggested_type,
        "tags": candidate.suggested_tags,
        "url": record.url,
        "doi": record.doi,
        "arxiv_id": record.arxiv_id,
        "code": None,
        "project": None,
        "authors": record.authors,
        "tldr": None,
        "status": "candidate",
        "added": date.today().isoformat(),
        "notes": None,
    }


def _candidate_yaml(candidate: Candidate) -> str:
    return yaml.safe_dump(
        [_candidate_dict(candidate)], sort_keys=False, allow_unicode=True, width=4096
    ).rstrip()


def render_discover_markdown(result: DiscoverResult) -> str:
    summary = result.summary()
    window = f"{result.since} → {result.until}" if result.until else f"since {result.since}"
    if result.years:
        window += f" (years {','.join(str(year) for year in result.years)})"
    lines = ["# Discovery report", ""]
    lines.append(f"window: {window} · queries {len(result.queries)} · found {summary['found']}")
    lines.append(
        f"new {summary['new']} · pending {summary['pending']} · similar {summary['similar']} · "
        f"already in collection {summary['existing']}"
    )
    lines.append("")

    lines.append("## Queries")
    for query in result.queries:
        lines.append(f"- {query}")
    lines.append("")

    lines.append("## New candidates")
    lines.append("")
    if not result.new:
        lines.append("_none_")
        lines.append("")
    for candidate in result.new:
        record = candidate.record
        venue_note = candidate.venue or "—"
        if candidate.venue_unknown:
            venue_note += " (unmapped)"
        kind = "preprint" if candidate.is_preprint else "published"
        lines.append(f"### {record.title}")
        lines.append(
            f"- year {record.year} · venue {venue_note} [tier {candidate.venue_tier}, {kind}] · "
            f"source {record.source} · relevance {candidate.relevance:.2f}"
        )
        if candidate.type_basis:
            lines.append(
                f"- suggested type: {candidate.suggested_type} "
                f"({candidate.type_basis}); tags: {', '.join(candidate.suggested_tags) or '—'}"
            )
        if candidate.llm_relevance is not None:
            lines.append(
                f"- relevance: llm {candidate.llm_relevance:.2f} (rule {candidate.relevance:.2f})"
            )
        if record.date:
            lines.append(f"- published {record.date}")
        if record.authors:
            shown = ", ".join(record.authors[:8])
            if len(record.authors) > 8:
                shown += " et al."
            lines.append(f"- authors: {shown}")
        if record.doi:
            lines.append(f"- doi: {record.doi}")
        if record.arxiv_id:
            lines.append(f"- arxiv: {record.arxiv_id}")
        if record.url:
            lines.append(f"- url: {record.url}")
        tags = ", ".join(candidate.suggested_tags) if candidate.suggested_tags else "—"
        lines.append(f"- suggested: type={candidate.suggested_type} · tags={tags}")
        lines.append("")
        lines.append("```yaml")
        lines.append(_candidate_yaml(candidate))
        lines.append("```")
        lines.append("")

    lines.append("## Pending (below venue tier)")
    lines.append("")
    if result.pending:
        lines.append("Below the quality threshold; **not** auto-ingested by `--apply`.")
        lines.append("")
        for candidate in result.pending:
            record = candidate.record
            lines.append(
                f"- {record.title} — venue {candidate.venue} [tier {candidate.venue_tier}], "
                f"{record.source}, relevance {candidate.relevance:.2f}"
            )
    else:
        lines.append("_none_")
    lines.append("")

    lines.append("## Similar (possible duplicates or updates)")
    lines.append("")
    if result.similar:
        for candidate in result.similar:
            lines.append(
                f"- `{candidate.matched_id}` ← {candidate.record.title} "
                f"(relevance {candidate.relevance:.2f}, {candidate.record.source})"
            )
    else:
        lines.append("_none_")
    lines.append("")

    lines.append("## Unmapped venues")
    lines.append("")
    if result.unknown_venues:
        lines.append("Add these to `config/venues.yaml` so future runs use a short code:")
        lines.append("")
        for venue in result.unknown_venues:
            lines.append(f"- {venue}")
    else:
        lines.append("_none_")
    lines.append("")

    if result.source_errors:
        lines.append("## Source errors")
        lines.append("")
        for error in result.source_errors:
            lines.append(f"- {error}")
        lines.append("")
    return "\n".join(lines)


def render_discover_json(result: DiscoverResult) -> str:
    payload = {
        "since": result.since,
        "queries": result.queries,
        "summary": result.summary(),
        "new": [asdict(candidate) for candidate in result.new],
        "pending": [asdict(candidate) for candidate in result.pending],
        "similar": [asdict(candidate) for candidate in result.similar],
        "existing": [asdict(candidate) for candidate in result.existing],
        "unknown_venues": result.unknown_venues,
        "source_errors": result.source_errors,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def write_discover(result: DiscoverResult, md_path, json_path) -> tuple[Path, Path]:
    md_path = Path(md_path)
    json_path = Path(json_path)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_discover_markdown(result), encoding="utf-8")
    json_path.write_text(render_discover_json(result), encoding="utf-8")
    return md_path, json_path


def render_llm_markdown(result: LlmResult, titles: Dict[str, str]) -> str:
    summary = result.summary()
    lines = ["# LLM enrichment report", ""]
    lines.append(
        f"papers {summary['total']} · suggestions {summary['suggestions']} · "
        f"skipped (no abstract) {summary['skipped_no_abstract']} · errors {summary['errors']}"
    )
    lines.append("")

    lines.append("## Suggestions")
    lines.append("")
    if not result.suggestions:
        lines.append("_none_")
        lines.append("")
    for suggestion in result.suggestions:
        current = suggestion.current_tags
        added = [tag for tag in suggestion.tags if tag not in current]
        removed = [tag for tag in current if tag not in suggestion.tags]
        lines.append(f"### `{suggestion.paper_id}`")
        lines.append(f"_{titles.get(suggestion.paper_id, '')}_")
        lines.append("")
        lines.append(f"- type: {suggestion.type or '—'}")
        lines.append(f"- current tags: {', '.join(current) if current else '—'}")
        lines.append(f"- suggested tags: {', '.join(suggestion.tags) if suggestion.tags else '—'}")
        if added or removed:
            changes = [f"+{tag}" for tag in added] + [f"-{tag}" for tag in removed]
            lines.append(f"- changes: {' '.join(changes)}")
        if suggestion.new_tags:
            lines.append(f"- new tag candidates: {', '.join(suggestion.new_tags)}")
        lines.append("")
        lines.append(f"> {suggestion.tldr or '—'}")
        lines.append("")

    if result.skipped_no_abstract:
        lines.append("## Skipped (no abstract available)")
        lines.append("")
        for paper_id in result.skipped_no_abstract:
            lines.append(f"- `{paper_id}` — {titles.get(paper_id, '')}")
        lines.append("")

    if result.errors:
        lines.append("## Errors")
        lines.append("")
        for error in result.errors:
            lines.append(f"- {error}")
        lines.append("")

    return "\n".join(lines)


def render_llm_json(result: LlmResult) -> str:
    payload = {
        "summary": result.summary(),
        "suggestions": [asdict(suggestion) for suggestion in result.suggestions],
        "skipped_no_abstract": result.skipped_no_abstract,
        "errors": result.errors,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def write_llm(result: LlmResult, titles: Dict[str, str], md_path, json_path) -> tuple[Path, Path]:
    md_path = Path(md_path)
    json_path = Path(json_path)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_llm_markdown(result, titles), encoding="utf-8")
    json_path.write_text(render_llm_json(result), encoding="utf-8")
    return md_path, json_path


def render_code_markdown(matches: List[CodeMatch], titles: Dict[str, str]) -> str:
    lines = ["# Code / project link report", "", f"matches: {len(matches)}", ""]
    for match in matches:
        lines.append(f"### `{match.paper_id}`")
        lines.append(f"_{titles.get(match.paper_id, '')}_")
        lines.append("")
        lines.append(f"- code: {match.code or '—'}")
        if match.project:
            lines.append(f"- project: {match.project}")
        lines.append(f"- source: {match.source} · score {match.score:.2f} · {match.reason}")
        if match.repo:
            lines.append(f"- repo: {match.repo}")
        lines.append("")
    return "\n".join(lines)


def render_code_json(matches: List[CodeMatch]) -> str:
    return json.dumps([asdict(match) for match in matches], ensure_ascii=False, indent=2) + "\n"


def write_code(matches: List[CodeMatch], titles: Dict[str, str], md_path, json_path) -> tuple[Path, Path]:
    md_path = Path(md_path)
    json_path = Path(json_path)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_code_markdown(matches, titles), encoding="utf-8")
    json_path.write_text(render_code_json(matches), encoding="utf-8")
    return md_path, json_path
