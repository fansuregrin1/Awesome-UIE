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
from typing import List

from .schema import Paper, PaperType

HEAD = (
    "<!-- AUTO-GENERATED FILE - DO NOT EDIT.\n"
    "     Edit .dev_scripts/papers.yaml instead, then run from .dev_scripts:\n"
    "         python -m uie.cli build\n"
    "-->\n"
    "\n"
    "# Awesome Underwater Image Enhancement (UIE) Methods\n"
    "\n"
    "A curated list of Underwater Image Enhancement (UIE) papers and resources, "
    "inspired by [Awesome-Inpainting-Tech]"
    "(https://github.com/zengyh1900/Awesome-Image-Inpainting).\n"
    "\n"
    "This `README.md` is automatically generated from "
    "[`.dev_scripts/papers.yaml`](.dev_scripts/papers.yaml).\n"
    "\n"
    "We provide [scripts](.dev_scripts/uie) to generate `README.md` "
    "(plus `collection.csv`, `papers.json`, `papers.bib` and `llms.txt`) "
    "from the YAML data file.\n"
    "\n"
    "🌐 **Browse online:** <https://fansuregrin1.github.io/Awesome-UIE/>\n"
    "\n"
    "Welcome to pull request to update or correct this collection. 🥰\n"
)

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
    "WACV",
}

_README_CSV_COLUMNS = ["Year", "Pub", "Type", "Tags", "Title", "URL", "Code", "Project"]


def _sorted(papers: List[Paper]) -> List[Paper]:
    return sorted(papers, key=lambda paper: paper.sort_key())


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


def render_readme(papers: List[Paper]) -> str:
    papers = _sorted(papers)
    out = [HEAD]
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


def render_json(papers: List[Paper]) -> str:
    papers = _sorted(papers)
    payload = {
        "count": len(papers),
        "papers": [paper.model_dump(mode="json") for paper in papers],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


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
        "- [README.md](README.md): grouped by year",
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
    outputs = {
        root / "README.md": render_readme(papers),
        root / ".dev_scripts" / "collection.csv": render_csv(papers),
        root / "papers.json": render_json(papers),
        root / "papers.bib": render_bib(papers),
        root / "llms.txt": render_llms(papers),
        # Duplicated into the GitHub Pages site, which can only serve files
        # below the published folder (docs/).
        root / "docs" / "data" / "papers.json": render_json(papers),
    }
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return list(outputs)
