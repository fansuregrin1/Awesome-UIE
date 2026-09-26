"""One-off: build ``config/venues.yaml`` from the collection's existing venues.

For each distinct venue code it looks up one representative DOI on Crossref to
fill in the full name, ISSN and ISO short title. Review the result and refine
names/aliases/types by hand; after that the registry is maintained via the
``unknown-venue`` validation warning.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import yaml

from uie.schema import load_papers
from uie.sources.crossref import CrossrefSource
from uie.sources.http import HttpClient

HERE = Path(__file__).resolve().parent
PAPERS = HERE / "papers.yaml"
OUTPUT = HERE / "config" / "venues.yaml"

CONFERENCES = {
    "AAAI", "ACCV", "ACM-MM", "CVPR", "CVPRW", "ECCV", "ICASSP", "ICCV",
    "ICCVW", "ICIP", "ICME", "ICRA", "ICVGIP", "WACV",
}

# Canonical names for venues whose Crossref container title is year-specific
# ("Proceedings of the 2004 ...") or a generic series ("Lecture Notes in CS").
CANONICAL: Dict[str, str] = {
    "AAAI": "Proceedings of the AAAI Conference on Artificial Intelligence",
    "ACCV": "Asian Conference on Computer Vision",
    "ACM-MM": "ACM International Conference on Multimedia",
    "CVPR": "IEEE/CVF Conference on Computer Vision and Pattern Recognition",
    "CVPRW": "IEEE/CVF Conference on Computer Vision and Pattern Recognition Workshops",
    "ECCV": "European Conference on Computer Vision",
    "ICASSP": "IEEE International Conference on Acoustics, Speech and Signal Processing",
    "ICCV": "IEEE/CVF International Conference on Computer Vision",
    "ICCVW": "IEEE/CVF International Conference on Computer Vision Workshops",
    "ICIP": "IEEE International Conference on Image Processing",
    "ICME": "IEEE International Conference on Multimedia and Expo",
    "ICRA": "IEEE International Conference on Robotics and Automation",
    "ICVGIP": "Indian Conference on Computer Vision, Graphics and Image Processing",
    "WACV": "IEEE/CVF Winter Conference on Applications of Computer Vision",
    "arXiv": "arXiv",
}


# Manual name/ISSN for venues Crossref resolves poorly.
OVERRIDES: Dict[str, tuple] = {
    "C&G": ("Computers & Graphics", "0097-8493"),
}


def representative_dois() -> Dict[str, str]:
    """Map each venue code to one DOI from the collection."""
    dois: Dict[str, str] = {}
    for paper in load_papers(PAPERS):
        if paper.doi and paper.venue not in dois:
            dois[paper.venue] = paper.doi
    return dois


def main() -> int:
    client = HttpClient(mailto=None)
    crossref = CrossrefSource(client)
    dois = representative_dois()

    entries: List[dict] = []
    for code, doi in sorted(dois.items()):
        if code in CANONICAL:
            entries.append(
                {
                    "code": code,
                    "name": CANONICAL[code],
                    "type": "preprint" if code == "arXiv" else "conference",
                    "issn": None,
                    "aliases": [],
                }
            )
            continue

        name, issn, aliases = code, None, []
        if code in OVERRIDES:
            name, issn = OVERRIDES[code]
        else:
            records = crossref.lookup(doi=doi)
            if records:
                record = records[0]
                if record.venue:
                    name = record.venue
                issn = record.issn
                if record.venue_short and record.venue_short.lower() != name.lower():
                    aliases.append(record.venue_short)
        entries.append(
            {
                "code": code,
                "name": name,
                "type": "journal",
                "issn": issn,
                "aliases": aliases,
            }
        )

    # venues without a DOI (e.g. arXiv preprints) still get a minimal entry
    for paper in load_papers(PAPERS):
        if paper.venue not in {entry["code"] for entry in entries}:
            entries.append(
                {
                    "code": paper.venue,
                    "name": paper.venue,
                    "type": "conference" if paper.venue in CONFERENCES else ("preprint" if paper.venue == "arXiv" else "journal"),
                    "issn": None,
                    "aliases": [],
                }
            )

    entries.sort(key=lambda entry: entry["code"])
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        "# Venue registry: full name / ISSN / aliases -> the short code used in the collection.\n"
        "# Maintained via the `unknown-venue` validation warning.\n\n"
        + yaml.safe_dump(entries, sort_keys=False, allow_unicode=True, width=4096),
        encoding="utf-8",
    )
    print(f"wrote {OUTPUT.relative_to(HERE.parent)} ({len(entries)} venues)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
