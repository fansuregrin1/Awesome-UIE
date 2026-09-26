"""One-off migration: ``.dev_scripts/collection.csv`` -> ``.dev_scripts/papers.yaml``.

Run from the ``.dev_scripts`` directory::

    python migrate_csv_to_yaml.py

The script also reports duplicate URLs / titles it finds. Two known data
problems were resolved during the initial migration (see ``FIXES`` below); any
new problem is reported for manual review.
"""

from __future__ import annotations

import csv
import re
from datetime import date
from pathlib import Path
from typing import Dict, List

from uie.schema import Paper, PaperType, Status, dump_papers, make_id

HERE = Path(__file__).resolve().parent
CSV_PATH = HERE / "collection.csv"
YAML_PATH = HERE / "papers.yaml"

# Date the entries were (re)ingested into the YAML database. Used as `added`.
ADDED_DATE = date(2026, 9, 26)

# --- Old `Type` -> (Paper.type, tags) -----------------------------------------
TYPE_MAP: Dict[str, tuple[PaperType, List[str]]] = {
    "Traditional": (PaperType.TRADITIONAL, []),
    "Hybrid": (PaperType.HYBRID, []),
    "CNN": (PaperType.DEEP_LEARNING, ["CNN"]),
    "GAN": (PaperType.DEEP_LEARNING, ["GAN"]),
    "Diffusion": (PaperType.DEEP_LEARNING, ["Diffusion"]),
    "Transformer": (PaperType.DEEP_LEARNING, ["Transformer"]),
    "Mamba": (PaperType.DEEP_LEARNING, ["Mamba"]),
    "Large-Model": (PaperType.DEEP_LEARNING, ["Large-Model"]),
    "Task-Driven": (PaperType.DEEP_LEARNING, ["Task-Driven"]),
    "CNN;Transformer": (PaperType.DEEP_LEARNING, ["CNN", "Transformer"]),
    # Paper kinds stay DeepLearning (paradigm) and carry a kind tag.
    "Review": (PaperType.DEEP_LEARNING, ["Survey"]),
    "Benchmark": (PaperType.DEEP_LEARNING, ["Benchmark"]),
}

# --- Manual fixes for known data problems -------------------------------------
# DCGF was listed twice (2024 and 2025); it is a single TGARS 2025 paper.
#   https://doi.org/10.1109/TGRS.2024.3522685
DROP: set[tuple[str, str]] = {
    ("2024", "DCGF: Diffusion-Color-Guided Framework for Underwater Image Enhancement"),
}
# ICAD-UIE and "Multicolor Attribute Information Fusion" shared document/11296899.
# The latter owns 11296899; ICAD-UIE is document/11313551.
URL_FIX: Dict[str, str] = {
    (
        "ICAD-UIE: Naturalness-Ensuring Underwater Image Enhancement With "
        "Interchannel Attenuation Difference-Based Dewatering Model"
    ): "https://ieeexplore.ieee.org/document/11313551/",
}
# Mojibake in the original CSV.
TITLE_FIX: Dict[str, str] = {
    "UIEC\u85db2-Net: CNN-based Underwater Image Enhancement Using Two Color Space":
        "UIEC^2-Net: CNN-based Underwater Image Enhancement Using Two Color Space",
}

_ARXIV_RE = re.compile(r"arxiv\.org/abs/([0-9.]+)")


def _arxiv_id(url: str, venue: str) -> str | None:
    match = _ARXIV_RE.search(url or "")
    if match:
        return match.group(1)
    return None


def main() -> int:
    with CSV_PATH.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    papers: List[Paper] = []
    used_ids: Dict[str, int] = {}
    seen_urls: Dict[str, str] = {}
    seen_titles: Dict[str, str] = {}
    problems: List[str] = []

    for row in rows:
        year = row["Year"].strip()
        title = TITLE_FIX.get(row["Title"].strip(), row["Title"].strip())
        url = URL_FIX.get(title, row["URL"].strip())
        pub = row["Pub"].strip()
        old_type = row["Type"].strip()

        if (year, title) in DROP:
            problem = f"dropped duplicate entry: {year} {title!r}"
            problems.append(problem)
            print(f"[fix] {problem}")
            continue

        if old_type not in TYPE_MAP:
            raise SystemExit(f"unmapped Type {old_type!r} for {title!r}")
        paper_type, tags = TYPE_MAP[old_type]

        paper_id = make_id(int(year), title)
        if paper_id in used_ids:
            used_ids[paper_id] += 1
            paper_id = f"{paper_id}-{used_ids[paper_id]}"

        if url in seen_urls:
            problems.append(f"duplicate URL {url} shared by {seen_urls[url]!r} and {title!r}")
        else:
            seen_urls[url] = title

        normalized = re.sub(r"[^a-z0-9]+", "", title.lower())
        if normalized in seen_titles:
            problems.append(f"duplicate title: {seen_titles[normalized]!r} and {title!r}")
        else:
            seen_titles[normalized] = title

        papers.append(
            Paper(
                id=paper_id,
                title=title,
                year=int(year),
                venue=pub,
                type=paper_type,
                tags=tags,
                url=url,
                arxiv_id=_arxiv_id(url, pub),
                code=row["Code"].strip() or None,
                project=row["Project"].strip() or None,
                status=Status.VERIFIED,
                added=ADDED_DATE,
            )
        )

    papers.sort(key=lambda paper: (paper.year, paper.title.lower()))
    dump_papers(YAML_PATH, papers)

    print(f"wrote {YAML_PATH.relative_to(HERE.parent)} ({len(papers)} papers)")
    if problems:
        print(f"\n{len(problems)} issue(s) to review:")
        for problem in problems:
            print(f"  - {problem}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
