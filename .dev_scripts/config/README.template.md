<!-- AUTO-GENERATED FILE - DO NOT EDIT.
     Edit .dev_scripts/config/README.template.md and run from .dev_scripts:
         python -m uie.cli build
-->

# Awesome Underwater Image Enhancement (UIE)

> A curated, machine-readable collection of **underwater image enhancement** papers — {{COUNT}} entries spanning {{YEAR_MIN}}–{{YEAR_MAX}}, with venues, authors, DOIs, code links, controlled tags and one-sentence summaries.

This list is **data-driven**: a single YAML file ([`.dev_scripts/papers.yaml`](.dev_scripts/papers.yaml)) is the source of truth, and the paper list, exports and website are all generated from it.

## 🔗 Quick links

- 📄 **[Paper list](PAPERS.md)** — the full, year-grouped list
- 🌐 **[Interactive site](https://fansuregrin1.github.io/Awesome-UIE/)** — search, filters (year / type / venue / tag), and statistics
- 🧾 **Data**: [`papers.json`](papers.json) · [`papers.bib`](papers.bib) · [`collection.csv`](.dev_scripts/collection.csv) · [`llms.txt`](llms.txt)

## ✨ Highlights

- **{{COUNT}} papers** ({{YEAR_MIN}}–{{YEAR_MAX}}), curated and deduplicated.
- **Rich metadata**: venue, year, authors, DOI, tags, code/project links and an LLM-written **TL;DR**.
- **Coarse `type` + fine-grained `tags`** — `type` stays stable (`Traditional` / `DeepLearning` / `Hybrid`); `tags` capture architecture (`CNN`, `GAN`, `Diffusion`, `Transformer`, `Mamba`, …) and themes (`Color-Correction`, `Physical-Model`, `Dehazing`, `Image-Fusion`, …).
- **Machine-readable**: JSON, BibTeX and CSV exports plus an `llms.txt` index.
- **Automated pipeline**: metadata enrichment (arXiv / OpenAlex / Crossref), new-paper discovery, and LLM-assisted TL;DRs and tags.
- **Validated in CI**: schema, duplicates, tag vocabulary and link checks on every change.

## 📊 At a glance

| Metric | Value |
| --- | --- |
| Papers | {{COUNT}} |
| Year range | {{YEAR_MIN}}–{{YEAR_MAX}} |
| With code | {{WITH_CODE}} |
| With DOI | {{WITH_DOI}} |
| Traditional | {{T_TRADITIONAL}} |
| Deep learning | {{T_DEEPLEARNING}} |
| Hybrid | {{T_HYBRID}} |

See the [interactive site](https://fansuregrin1.github.io/Awesome-UIE/) for charts and filtering.

## 🤖 How it stays up to date

Everything is generated from `.dev_scripts/papers.yaml` by the tooling in `.dev_scripts/uie/`:

| Command | Purpose |
| --- | --- |
| `python -m uie.cli build` | regenerate README, paper list, exports and site data |
| `python -m uie.cli validate` | schema / duplicate / tag / venue checks |
| `python -m uie.cli enrich` | suggest missing metadata (arXiv / OpenAlex / Crossref) |
| `python -m uie.cli discover` | find new papers |
| `python -m uie.cli llm` | LLM TL;DRs and tag review |
| `python -m uie.cli check-links` | link and repository liveness |

## Contributing

Corrections and additions are welcome. The one rule: **edit `papers.yaml`, not the generated files.**

See [`AGENTS.md`](AGENTS.md) for the schema and workflow.

## License

See [LICENSE](LICENSE).
