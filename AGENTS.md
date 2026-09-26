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
python -m uie.cli discover                 # find new papers (network, read-only)
python -m uie.cli venues                   # audit venues against the registry
python -m uie.cli llm                      # LLM tldr/tags (needs an API key)
```

Run the offline tests from the repository root:

```bash
python -m unittest discover -s tests -v
```

`enrich` queries arXiv / OpenAlex / Crossref and writes reports under
`proposals/` (gitignored). By default it is read-only and a human applies the
suggestions. `enrich --apply` writes high-confidence, fill-only `doi`/`authors`
suggestions directly (never overwriting existing values), regenerates the
artifacts and is meant to be reviewed via git diff / PR:

```bash
python -m uie.cli enrich --apply                 # doi + authors
python -m uie.cli enrich --apply --fields authors --min-score 0.97
```

`discover` searches the same sources for **new** papers using the keywords in
`.dev_scripts/config/discovery.yaml`, deduplicates against the collection, and
writes `proposals/discover.md` + `.json` (a ready-to-paste YAML snippet per
candidate). With `--apply` it also appends the new candidates to `papers.yaml` as
`status: candidate`.

Candidates are **isolated**: generated artifacts (README, site, `papers.json`,
BibTeX, CSV) only include `status: verified` entries, so unreviewed candidates
stay out of the public output until you flip their status to `verified` and
rebuild.

```bash
python -m uie.cli discover --since 90 --limit 25            # report only
python -m uie.cli discover --since 90 --limit 25 --apply    # also ingest candidates
```

## Venues

`venue` is the collection's short code (`TIP`, `CVPR`, `Neurocomputing`, ...). The
APIs only return full names (and ISO abbreviations), so short codes are resolved
through the curated registry `.dev_scripts/config/venues.yaml`:

- Matching order: **ISSN → full name → alias → code**.
- `validate` warns (`unknown-venue`) for any `venue` not in the registry.
- `discover` maps a candidate's source venue to a code, or keeps the raw full
  name and lists it under "Unmapped venues" in the report.
- Add new venues to the registry (code + full name + optional ISSN/aliases) when
  the warning appears. Bootstrap drafted the initial entries from the existing
  DOIs via `bootstrap_venues.py`.

## LLM enrichment

`python -m uie.cli llm` fetches each paper's abstract (arXiv/OpenAlex/Crossref),
asks an OpenAI-compatible chat model for a one-sentence `tldr`, a `type` check
and tags from the controlled vocabulary, and writes a report to `proposals/`.
Every model response is cached under `.dev_scripts/.cache/`.

- Requires the API key named in `config/llm.yaml` (`OPENAI_API_KEY` by default).
- `--model` / `--base-url` override the config (any OpenAI-compatible endpoint).
- `--apply` writes fill-only `tldr` (default) or `--fields tldr,tags`, then
  regenerates artifacts.

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
