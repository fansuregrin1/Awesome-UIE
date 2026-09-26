"""Render enrichment reports as Markdown and JSON.

Reports are written under ``proposals/`` (gitignored). Nothing here touches
``papers.yaml`` — a human applies the suggestions.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Dict

from .enrich import EnrichResult


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
