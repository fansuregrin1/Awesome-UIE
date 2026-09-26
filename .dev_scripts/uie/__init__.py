"""Tooling for the Awesome-UIE collection.

The single source of truth is ``.dev_scripts/papers.yaml``. All other artifacts
(``README.md``, ``collection.csv``, ``papers.json``, ``papers.bib`` and
``llms.txt``) are generated from it.
"""

__all__ = ["schema", "render", "validate", "links", "cli"]
