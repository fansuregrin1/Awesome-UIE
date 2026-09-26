# AGENTS.md

Guidance for AI coding agents working in this repository.

## What this repository is

A curated list of Underwater Image Enhancement (UIE) papers. It is **data-driven**:
a single YAML database is the source of truth and every other artifact is generated
from it.

## Single source of truth

- **Edit only `.dev_scripts/papers.yaml`.**
- Never edit generated files by hand:
  - `README.md`
  - `.dev_scripts/collection.csv`
  - `papers.json`
  - `papers.bib`
  - `llms.txt`
  - `docs/data/papers.json`

After changing `papers.yaml`, regenerate everything:

```bash
cd .dev_scripts
python -m uie.cli build
```

CI verifies that the generated files match the source (it runs `build` and then
`git diff --exit-code`). A PR that changes `papers.yaml` without rebuilding fails.

## Commands

Run all commands from `.dev_scripts`:

```bash
python -m uie.cli build                    # regenerate all artifacts
python -m uie.cli validate                 # deterministic checks
python -m uie.cli validate --format json   # machine-readable report
python -m uie.cli check-links              # link / repo liveness (network)
python -m uie.cli enrich                   # suggest missing metadata (network, read-only)
```

Run the offline tests from the repository root:

```bash
python -m unittest discover -s tests -v
```

`enrich` queries arXiv / OpenAlex / Crossref and writes reports under
`proposals/` (gitignored). It never edits `papers.yaml`; a human applies the
suggestions.

`validate --changed-only --base origin/main` checks only entries changed versus
the base revision.

## Data schema (`papers.yaml`)

Each entry is a list item with these fields:

| field       | required | notes                                                        |
|-------------|----------|--------------------------------------------------------------|
| `id`        | yes      | lowercase hyphenated slug, `{year}-{title-slug}`            |
| `title`     | yes      |                                                              |
| `year`      | yes      | 2000-2100                                                     |
| `venue`     | yes      | short name, e.g. `TIP`, `CVPR`, `arXiv`                     |
| `type`      | yes      | **exactly one of** `Traditional`, `DeepLearning`, `Hybrid`  |
| `tags`      | no       | list, drawn from the controlled vocabulary in `uie/schema.py`|
| `url`       | yes      | link to the paper                                            |
| `doi`       | no       |                                                              |
| `arxiv_id`  | no       | e.g. `2405.08419`                                            |
| `code`      | no       | code repository                                              |
| `project`   | no       | project page                                                 |
| `authors`   | no       | list                                                         |
| `tldr`      | no       | one-sentence summary (filled by enrichment)                  |
| `status`    | no       | `verified` (default) or `candidate`                          |
| `added`     | no       | ISO date the entry was added                                 |
| `notes`     | no       | free text                                                    |

## Classification policy

- `type` is the **coarse methodological paradigm** and is deliberately limited to
  three values so it stays stable as new methods appear.
- `tags` carry finer-grained information (architecture, paper kind, theme). The
  controlled vocabulary lives in `.dev_scripts/uie/schema.py` (`KNOWN_TAGS`).
- New tags may be proposed (e.g. by an LLM) but must be added to `KNOWN_TAGS`
  after human review. Unknown tags produce validation warnings.
- Paper kinds: a survey gets `tags: [Survey]`; a benchmark/dataset paper gets
  `tags: [Benchmark]`. Their `type` is still the paradigm they belong to.

## Validation rules

- **error** (blocks merge): schema violations, duplicate `id`/URL, same
  normalized title *and* year, missing required fields, out-of-range year.
- **warning** (does not block): similar title across years, unknown/duplicate
  tags, duplicate code/project links, suspicious title length, dead links.

Exceptions go in `.dev_scripts/validation-allowlist.yaml` with a reason.
