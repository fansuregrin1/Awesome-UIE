"""Render every generated artifact from the paper database.

All renderers are deterministic: no timestamps are emitted, so CI can verify
that the committed artifacts match the source by running ``build`` followed by
``git diff --exit-code``.
"""

from __future__ import annotations

import csv
import io
import json
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

from .schema import Paper, PaperType, Status

README_TEMPLATE = Path(__file__).resolve().parents[1] / "config" / "README.template.md"
VENUES_YAML = Path(__file__).resolve().parents[1] / "config" / "venues.yaml"
SITE_URL = "https://fansuregrin1.github.io/Awesome-UIE/"

# Venues treated as conferences in the BibTeX export; everything else is a journal.
CONFERENCE_VENUES = {
    "AAAI",
    "ACCV",
    "ACM-MM",
    "CVPR",
    "CVPRW",
    "ECCV",
    "ICASSP",
    "ICCV",
    "ICCVW",
    "ICIP",
    "ICME",
    "ICRA",
    "ICVGIP",
    "IROS",
    "WACV",
}

_README_CSV_COLUMNS = ["Year", "Pub", "Type", "Tags", "Title", "URL", "Code", "Project"]


def _sorted(papers: List[Paper]) -> List[Paper]:
    return sorted(papers, key=lambda paper: paper.sort_key())


def published(papers: List[Paper]) -> List[Paper]:
    """Only ``status: verified`` entries are exposed in generated artifacts."""
    return [paper for paper in papers if paper.status == Status.VERIFIED]


def _readme_line(paper: Paper) -> str:
    parts = [f"`{tag}`" for tag in paper.tags]
    if paper.code:
        parts.append(f"[[code]]({paper.code})")
    if paper.project:
        parts.append(f"[[project]]({paper.project})")
    line = f"- **{paper.venue}** ({paper.type.value}) [{paper.title}]({paper.url})."
    if parts:
        line += " " + " ".join(parts)
    return line + "\n"


def _stats(papers: List[Paper]) -> dict:
    years = [paper.year for paper in papers]
    by_type = Counter(paper.type.value for paper in papers)
    return {
        "COUNT": len(papers),
        "YEAR_MIN": min(years) if years else "—",
        "YEAR_MAX": max(years) if years else "—",
        "WITH_CODE": sum(1 for paper in papers if paper.code),
        "WITH_DOI": sum(1 for paper in papers if paper.doi),
        "T_TRADITIONAL": by_type.get("Traditional", 0),
        "T_DEEPLEARNING": by_type.get("DeepLearning", 0),
        "T_HYBRID": by_type.get("Hybrid", 0),
    }


def render_readme(papers: List[Paper]) -> str:
    """Project overview (not the list) with live statistics."""
    papers = _sorted(papers)
    template = README_TEMPLATE.read_text(encoding="utf-8")
    for key, value in _stats(papers).items():
        template = template.replace("{{" + key + "}}", str(value))
    return template


def render_papers(papers: List[Paper]) -> str:
    """The full paper list, grouped by year."""
    papers = _sorted(papers)
    header = (
        "<!-- AUTO-GENERATED FILE - DO NOT EDIT. "
        "Edit .dev_scripts/papers.yaml and run `python -m uie.cli build`. -->\n\n"
        "# Underwater Image Enhancement — Paper List\n\n"
        f"{len(papers)} papers, grouped by year (newest first). "
        "See the [README](README.md) for an overview and the "
        f"[interactive site]({SITE_URL}) for search and filtering.\n\n"
    )
    out = [header]
    for year in sorted({paper.year for paper in papers}, reverse=True):
        out.append(f"## Year {year}\n")
        for paper in papers:
            if paper.year == year:
                out.append(_readme_line(paper))
    return "".join(out)


def render_csv(papers: List[Paper]) -> str:
    papers = _sorted(papers)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(_README_CSV_COLUMNS)
    for paper in papers:
        writer.writerow(
            [
                paper.year,
                paper.venue,
                paper.type.value,
                ";".join(paper.tags),
                paper.title,
                str(paper.url),
                str(paper.code or ""),
                str(paper.project or ""),
            ]
        )
    return buffer.getvalue()


def _stats_dict(papers: List[Paper]) -> Dict:
    years = [paper.year for paper in papers]
    return {
        "count": len(papers),
        "with_code": sum(1 for paper in papers if paper.code),
        "year_min": min(years) if years else None,
        "year_max": max(years) if years else None,
        "year_range": f"{min(years)}-{max(years)}" if years else "",
    }


def render_json(papers: List[Paper], venue_names: Optional[Dict[str, str]] = None) -> str:
    papers = _sorted(papers)
    payload: Dict = {
        "count": len(papers),
        "stats": _stats_dict(papers),
        "papers": [paper.model_dump(mode="json") for paper in papers],
    }
    if venue_names:
        payload["venues"] = venue_names
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _venue_names() -> Dict[str, str]:
    """Map venue code -> full name (from the registry) for the website tooltip."""
    from .venues import load_registry

    registry = load_registry(VENUES_YAML)
    if registry is None:
        return {}
    return {venue.code: venue.name for venue in registry.venues}


TYPE_LABELS = [
    ("Traditional", "Traditional", "#7a6a56"),
    ("DeepLearning", "Deep learning", "#0b6bcb"),
    ("Hybrid", "Hybrid", "#2e8b6f"),
]


def render_year_svg(papers: List[Paper]) -> str:
    counts = Counter(paper.year for paper in papers)
    years = sorted(counts)
    width, height = 680, 210
    pad_left, pad_right, pad_top, pad_bottom = 40, 10, 24, 28
    chart_w = width - pad_left - pad_right
    chart_h = height - pad_top - pad_bottom
    base_y = pad_top + chart_h
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" '
        'font-family="-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{pad_left}" y="16" font-size="13" font-weight="700" fill="#1b2430">Papers per year</text>',
        f'<line x1="{pad_left}" y1="{base_y}" x2="{width - pad_right}" y2="{base_y}" stroke="#dde5ee"/>',
    ]
    if years:
        max_count = max(counts.values()) or 1
        gap = 4.0
        bar_w = max(3.0, (chart_w - gap * (len(years) - 1)) / len(years))
        for index, year in enumerate(years):
            count = counts[year]
            bar_h = count / max_count * chart_h
            x = pad_left + index * (bar_w + gap)
            y = base_y - bar_h
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" '
                f'fill="#0b6bcb" rx="2"><title>{year}: {count}</title></rect>'
            )
            parts.append(
                f'<text x="{x + bar_w / 2:.1f}" y="{y - 3:.1f}" font-size="8" fill="#5b6b7c" '
                f'text-anchor="middle">{count}</text>'
            )
            parts.append(
                f'<text x="{x + bar_w / 2:.1f}" y="{base_y + 14:.1f}" font-size="8" fill="#5b6b7c" '
                f'text-anchor="middle">{str(year)[2:]}</text>'
            )
    parts.append("</svg>")
    return "".join(parts)


def render_type_svg(papers: List[Paper]) -> str:
    counts = Counter(paper.type.value for paper in papers)
    total = sum(counts.values()) or 1
    width, height = 680, 132
    pad_left, pad_right, pad_top = 120, 60, 30
    row_h = 24
    track_w = width - pad_left - pad_right
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" '
        'font-family="-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        '<text x="16" y="18" font-size="13" font-weight="700" fill="#1b2430">By type</text>',
    ]
    for index, (key, label, color) in enumerate(TYPE_LABELS):
        count = counts.get(key, 0)
        y = pad_top + index * row_h
        bar_w = count / total * track_w
        parts.append(f'<text x="16" y="{y + 12:.1f}" font-size="11" fill="#1b2430">{label}</text>')
        parts.append(f'<rect x="{pad_left}" y="{y + 2:.1f}" width="{track_w}" height="12" rx="6" fill="#eef2f7"/>')
        parts.append(f'<rect x="{pad_left}" y="{y + 2:.1f}" width="{bar_w:.1f}" height="12" rx="6" fill="{color}"/>')
        parts.append(
            f'<text x="{width - 10}" y="{y + 12:.1f}" font-size="11" fill="#5b6b7c" text-anchor="end">{count}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _bib_escape(text: str) -> str:
    return text.replace("&", r"\&").replace("%", r"\%")


def render_bib(papers: List[Paper]) -> str:
    blocks = []
    for paper in _sorted(papers):
        fields = []
        if paper.authors:
            fields.append(("author", " and ".join(paper.authors)))
        fields.append(("title", "{" + _bib_escape(paper.title) + "}"))
        if paper.venue == "arXiv":
            entry_type = "misc"
            fields.append(("year", str(paper.year)))
            if paper.arxiv_id:
                fields.append(("eprint", paper.arxiv_id))
                fields.append(("archivePrefix", "arXiv"))
            fields.append(("primaryClass", "cs.CV"))
        elif paper.venue in CONFERENCE_VENUES:
            entry_type = "inproceedings"
            fields.append(("booktitle", _bib_escape(paper.venue)))
            fields.append(("year", str(paper.year)))
        else:
            entry_type = "article"
            fields.append(("journal", _bib_escape(paper.venue)))
            fields.append(("year", str(paper.year)))
        if paper.doi:
            fields.append(("doi", paper.doi))
        fields.append(("url", str(paper.url)))
        body = ",\n".join(f"  {key} = {{{value}}}" for key, value in fields)
        blocks.append(f"@{entry_type}{{{paper.id},\n{body}\n}}")
    return "\n\n".join(blocks) + "\n"


def render_llms(papers: List[Paper]) -> str:
    papers = _sorted(papers)
    years = [paper.year for paper in papers]
    span = f"{min(years)}-{max(years)}" if years else "n/a"
    by_type = Counter(paper.type.value for paper in papers)
    with_code = sum(1 for paper in papers if paper.code)

    lines = [
        "# Awesome Underwater Image Enhancement (UIE)",
        "",
        f"> Auto-generated index of {len(papers)} underwater image enhancement papers ({span}).",
        "> For programmatic access prefer `papers.json` (all fields) or `collection.csv`.",
        "",
        "## Data files",
        "- [papers.json](papers.json): machine-readable records",
        "- [papers.bib](papers.bib): BibTeX",
        "- [collection.csv](.dev_scripts/collection.csv): flat CSV",
        "- [PAPERS.md](PAPERS.md): year-grouped list",
        "- [README.md](README.md): project overview",
        "",
        "## Summary",
        f"- papers: {len(papers)}",
        f"- with code: {with_code}",
    ]
    for paper_type in PaperType:
        lines.append(f"- {paper_type.value}: {by_type.get(paper_type.value, 0)}")
    lines += ["", "## Papers"]
    for paper in papers:
        tag = "; ".join(paper.tags)
        extra = f" ({tag})" if tag else ""
        code = f" [code]({paper.code})" if paper.code else ""
        lines.append(f"- [{paper.year}] {paper.venue} — {paper.title}{extra} — {paper.url}{code}")
    return "\n".join(lines) + "\n"


def write_all(root: Path | str, papers: List[Paper]) -> List[Path]:
    """Generate every artifact. Returns the list of written paths."""
    root = Path(root)
    papers = published(papers)
    venue_names = _venue_names()
    outputs = {
        root / "README.md": render_readme(papers),
        root / "PAPERS.md": render_papers(papers),
        root / ".dev_scripts" / "collection.csv": render_csv(papers),
        root / "papers.json": render_json(papers, venue_names),
        root / "papers.bib": render_bib(papers),
        root / "llms.txt": render_llms(papers),
        # Duplicated into the GitHub Pages site, which can only serve files
        # below the published folder (docs/).
        root / "docs" / "data" / "papers.json": render_json(papers, venue_names),
        # Charts embedded in the README (and usable by the site).
        root / "docs" / "assets" / "papers-per-year.svg": render_year_svg(papers),
        root / "docs" / "assets" / "by-type.svg": render_type_svg(papers),
    }
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return list(outputs)
