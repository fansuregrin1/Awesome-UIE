"""Command line entry point.

Run from the ``.dev_scripts`` directory::

    python -m uie.cli build
    python -m uie.cli validate [--changed-only --base origin/main] [--format json]
    python -m uie.cli check-links [--scope all|code|project|url]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

import yaml

from . import code as code_module
from . import discover as discover_module
from . import enrich as enrich_module
from . import links, proposals, render, validate
from . import llm as llm_module
from . import venues as venues_module
from .progress import Progress
from .schema import Paper, dump_papers, load_papers
from .sources.http import DEFAULT_TTL, HttpClient

ROOT = Path(__file__).resolve().parents[2]          # repository root
PAPERS_REL = ".dev_scripts/papers.yaml"
PAPERS_YAML = ROOT / PAPERS_REL
ALLOWLIST_YAML = ROOT / ".dev_scripts" / "validation-allowlist.yaml"
LINK_CACHE = ROOT / ".dev_scripts" / ".link-cache.json"
CONFIG_YAML = ROOT / ".dev_scripts" / "config" / "discovery.yaml"
VENUES_YAML = ROOT / ".dev_scripts" / "config" / "venues.yaml"
LLM_CONFIG_YAML = ROOT / ".dev_scripts" / "config" / "llm.yaml"
CACHE_DIR = ROOT / ".dev_scripts" / ".cache"


def _load_allowlist() -> Dict[str, Any]:
    if not ALLOWLIST_YAML.exists():
        return {}
    return yaml.safe_load(ALLOWLIST_YAML.read_text(encoding="utf-8")) or {}


def _print_issues(issues: List[validate.Issue], fmt: str) -> None:
    if fmt == "json":
        print(json.dumps([issue.as_dict() for issue in issues], ensure_ascii=False, indent=2))
        return
    errors = [issue for issue in issues if issue.level == "error"]
    warnings = [issue for issue in issues if issue.level == "warning"]
    print(f"errors: {len(errors)}  warnings: {len(warnings)}")
    for issue in issues:
        location = f" [{issue.where}]" if issue.where else ""
        print(f"  {issue.level.upper():7} {issue.code}{location}: {issue.message}")


def cmd_build(args: argparse.Namespace) -> int:
    papers = load_papers(PAPERS_YAML)
    written = render.write_all(ROOT, papers)
    for path in written:
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    papers, issues = validate.load_and_collect(PAPERS_YAML)

    if args.changed_only:
        changed = validate.changed_ids(PAPERS_REL, args.base, ROOT)
        papers = [paper for paper in papers if paper.id in changed]

    issues += validate.validate_papers(papers, _load_allowlist(), registry=venues_module.load_registry(VENUES_YAML))

    if args.format == "text":
        print(f"validated {len(papers)} papers")
    _print_issues(issues, args.format)

    has_error = any(issue.level == "error" for issue in issues)
    has_warning = any(issue.level == "warning" for issue in issues)
    if has_error or (args.strict and has_warning):
        return 1
    return 0


def _collect_urls(papers: List[Paper], scope: str) -> List[str]:
    urls: List[str] = []
    for paper in papers:
        if scope in ("all", "url"):
            urls.append(str(paper.url))
        if scope in ("all", "code") and paper.code:
            urls.append(str(paper.code))
        if scope in ("all", "project") and paper.project:
            urls.append(str(paper.project))
    return urls


def cmd_check_links(args: argparse.Namespace) -> int:
    papers = load_papers(PAPERS_YAML)
    allowlist = _load_allowlist()
    urls = _collect_urls(papers, args.scope)

    progress = _progress(args, len(urls), "links")
    try:
        results = asyncio.run(links.check_urls(urls, cache_path=LINK_CACHE, ttl=args.ttl, progress=progress))
    finally:
        progress.close()

    issues: List[validate.Issue] = []
    for url, result in sorted(results.items()):
        level = links.classify(url, result, allowlist)
        if level == "ok":
            continue
        detail = result.get("error") or f"HTTP {result.get('status')}"
        issues.append(validate.Issue("warning" if level == "warning" else "error", "link-dead", detail, url))

    if args.format == "text":
        print(f"checked {len(urls)} links")
    _print_issues(issues, args.format)

    if args.skip_repos:
        if args.no_fail:
            return 0
        return 1 if any(issue.level == "error" for issue in issues) else 0

    repos = sorted({repo for paper in papers if paper.code for repo in [links.github_repo(str(paper.code))] if repo})
    repo_results = asyncio.run(links.check_github_repos(repos, token=os.environ.get("GITHUB_TOKEN")))
    repo_issues: List[validate.Issue] = []
    for repo, info in sorted(repo_results.items()):
        if info.get("ok") is True:
            if info.get("archived"):
                repo_issues.append(validate.Issue("warning", "repo-archived", "repository is archived", repo))
            continue
        reason = info.get("reason")
        if reason == "missing":
            repo_issues.append(
                validate.Issue("error", "repo-missing", "GitHub API 404 (repository not found)", repo)
            )
        elif reason == "rate_limited":
            repo_issues.append(
                validate.Issue(
                    "warning",
                    "github-rate-limit",
                    f"GitHub API {info.get('status')} (rate limited) — set GITHUB_TOKEN or retry later",
                    repo,
                )
            )
        else:
            detail = info.get("error") or f"GitHub API status {info.get('status')}"
            repo_issues.append(validate.Issue("warning", "repo-check", detail, repo))

    if args.format == "text":
        print(f"checked {len(repos)} code repositories")
    _print_issues(repo_issues, args.format)

    all_issues = issues + repo_issues
    if args.no_fail:
        return 0
    return 1 if any(issue.level == "error" for issue in all_issues) else 0


def _add_progress_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--no-progress", action="store_true", help="disable progress output")
    parser.add_argument("--verbose", action="store_true", help="print one line per item")


def _progress(args: argparse.Namespace, total: int, desc: str) -> Progress:
    return Progress(
        total,
        desc,
        enabled=not getattr(args, "no_progress", False),
        verbose=getattr(args, "verbose", False),
    )


def _load_config() -> Dict[str, Any]:
    if not CONFIG_YAML.exists():
        return {}
    return yaml.safe_load(CONFIG_YAML.read_text(encoding="utf-8")) or {}


def _load_llm_config() -> Dict[str, Any]:
    if not LLM_CONFIG_YAML.exists():
        return {}
    return yaml.safe_load(LLM_CONFIG_YAML.read_text(encoding="utf-8")) or {}


def cmd_enrich(args: argparse.Namespace) -> int:
    config = _load_config()
    papers = load_papers(PAPERS_YAML)

    if args.ids:
        wanted = {value.strip() for value in args.ids.split(",") if value.strip()}
        papers = [paper for paper in papers if paper.id in wanted]
    if args.limit:
        papers = papers[: args.limit]

    mailto = args.mailto or config.get("mailto")
    threshold = args.threshold if args.threshold is not None else float(config.get("match_threshold", 0.9))
    ambiguous_margin = float(config.get("ambiguous_margin", 0.15))
    ttl = 0 if args.refresh else (args.ttl or int(config.get("cache_ttl", DEFAULT_TTL)))

    client = HttpClient(cache_dir=CACHE_DIR, ttl=ttl, mailto=mailto)
    progress = _progress(args, len(papers), "enrich")
    try:
        result = enrich_module.enrich_papers(
            papers, client, threshold=threshold, ambiguous_margin=ambiguous_margin, progress=progress
        )
    finally:
        progress.close()

    titles = {paper.id: paper.title for paper in papers}
    md_path, json_path = proposals.write(result, titles, args.report, args.json)

    summary = result.summary()
    print(
        f"enriched {summary['total']} papers: matched {summary['matched']}, "
        f"ambiguous {summary['ambiguous']}, unmatched {summary['unmatched']}, "
        f"suggestions {summary['suggestions']}"
    )
    print(f"wrote {md_path}")
    print(f"wrote {json_path}")

    if args.apply:
        fields = [field.strip() for field in args.fields.split(",") if field.strip()]
        min_score = args.min_score if args.min_score is not None else threshold
        applied = enrich_module.apply_suggestions(papers, result, fields=fields, min_score=min_score)
        if applied:
            dump_papers(PAPERS_YAML, papers)
            render.write_all(ROOT, papers)
            by_field: Dict[str, int] = {}
            for _, field, _ in applied:
                by_field[field] = by_field.get(field, 0) + 1
            detail = ", ".join(f"{field} {count}" for field, count in sorted(by_field.items()))
            print(f"applied {len(applied)} fields ({detail}) at min_score {min_score}")
            print("updated papers.yaml and regenerated artifacts")
        else:
            print("nothing to apply")

    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    config = _load_config()
    papers = load_papers(PAPERS_YAML)

    mailto = args.mailto or config.get("mailto")
    ttl = 0 if args.refresh else (args.ttl or int(config.get("cache_ttl", DEFAULT_TTL)))
    client = HttpClient(cache_dir=CACHE_DIR, ttl=ttl, mailto=mailto)

    years = None
    if args.year:
        years = [int(value) for value in str(args.year).replace(" ", ",").split(",") if value]

    progress = _progress(args, len(config.get("keywords") or []) * 3, "discover")
    try:
        result = discover_module.discover(
            papers,
            config,
            client=client,
            since_days=args.since,
            limit_per_source=args.limit,
            registry=venues_module.load_registry(VENUES_YAML),
            min_tier=args.min_tier or config.get("min_tier"),
            years=years,
            progress=progress,
        )
    finally:
        progress.close()

    md_path, json_path = proposals.write_discover(result, args.report, args.json)

    summary = result.summary()
    print(
        f"discovered: found {summary['found']}, new {summary['new']}, "
        f"pending {summary['pending']}, similar {summary['similar']}, "
        f"already in collection {summary['existing']}, source errors {summary['source_errors']}"
    )
    print(f"wrote {md_path}")
    print(f"wrote {json_path}")

    if args.apply:
        added = discover_module.candidates_to_papers(papers, result)
        if added:
            dump_papers(PAPERS_YAML, papers + added)
            render.write_all(ROOT, papers + added)
            print(f"added {len(added)} candidate papers (status: candidate, hidden from README/site)")
        else:
            print("nothing to add")
    return 0


def cmd_venues(args: argparse.Namespace) -> int:
    registry = venues_module.load_registry(VENUES_YAML)
    if registry is None:
        print("no config/venues.yaml found")
        return 1

    if args.refresh_metrics:
        client = HttpClient(cache_dir=CACHE_DIR, ttl=args.ttl or DEFAULT_TTL)
        updated = 0
        progress = _progress(args, len(registry.venues), "venues")
        for venue in registry.venues:
            if not venue.issn:
                progress.update(venue.code)
                continue
            try:
                data = client.get_json(
                    "https://api.openalex.org/sources", params={"filter": f"issn:{venue.issn}"}
                )
            except RuntimeError:
                progress.update(venue.code)
                continue
            results = data.get("results") or []
            if results:
                source = results[0]
                stats = source.get("summary_stats") or {}
                venue.metrics = {
                    "two_year_mean_citedness": round(stats.get("2yr_mean_citedness") or 0.0, 2),
                    "h_index": stats.get("h_index"),
                    "works_count": source.get("works_count"),
                    "as_of": date.today().year,
                }
                updated += 1
            progress.update(venue.code)
        progress.close()
        venues_module.save_registry(VENUES_YAML, registry)
        print(f"refreshed OpenAlex metrics for {updated} venues")

    papers = load_papers(PAPERS_YAML)
    registered = sum(1 for paper in papers if registry.known(paper.venue))
    unknown = sorted({paper.venue for paper in papers if not registry.known(paper.venue)})
    tiers = Counter(registry.tier(paper.venue) for paper in papers)
    print(f"{registered}/{len(papers)} papers use a registered venue ({len(registry.codes)} codes known)")
    print("papers by venue tier:", dict(tiers))
    if unknown:
        print("unregistered venues:")
        for venue in unknown:
            print(f"  - {venue}")
    return 0


def cmd_llm(args: argparse.Namespace) -> int:
    config = _load_llm_config()
    key_env = args.api_key_env or config.get("api_key_env", "OPENAI_API_KEY")
    api_key = os.environ.get(key_env)
    if not api_key:
        print(f"error: environment variable {key_env} is not set (see config/llm.yaml)")
        return 1

    papers = load_papers(PAPERS_YAML)
    if args.ids:
        wanted = {value.strip() for value in args.ids.split(",") if value.strip()}
        papers = [paper for paper in papers if paper.id in wanted]
    if args.limit:
        papers = papers[: args.limit]

    http = HttpClient(
        cache_dir=CACHE_DIR,
        ttl=0 if args.refresh_abstracts else int(config.get("cache_ttl", DEFAULT_TTL)),
    )
    to_process = papers if args.all else [paper for paper in papers if not paper.tldr]
    abstracts_progress = _progress(args, len(to_process), "abstracts")
    try:
        abstracts = enrich_module.collect_abstracts(to_process, http, progress=abstracts_progress)
    finally:
        abstracts_progress.close()

    llm = llm_module.LlmClient(
        model=args.model or config.get("model", "gpt-4o-mini"),
        api_key=api_key,
        base_url=args.base_url or config.get("base_url", "https://api.openai.com/v1"),
        cache_dir=CACHE_DIR,
        ttl=int(config.get("cache_ttl", DEFAULT_TTL)),
    )
    llm_progress = _progress(args, len(to_process), "llm")
    try:
        result = llm_module.summarize_papers(
            papers,
            llm,
            abstracts,
            only_missing=not args.all,
            max_tags=int(config.get("max_tags", 5)),
            progress=llm_progress,
        )
    finally:
        llm_progress.close()

    titles = {paper.id: paper.title for paper in papers}
    md_path, json_path = proposals.write_llm(result, titles, args.report, args.json)

    summary = result.summary()
    print(
        f"llm ({llm.model}): papers {summary['total']}, suggestions {summary['suggestions']}, "
        f"skipped {summary['skipped_no_abstract']}, errors {summary['errors']}"
    )
    print(f"wrote {md_path}")
    print(f"wrote {json_path}")

    if args.apply:
        fields = [field.strip() for field in args.fields.split(",") if field.strip()]
        applied = llm_module.apply_llm_suggestions(
            papers, result.suggestions, fields=fields, tag_mode=args.tag_mode
        )
        if applied:
            dump_papers(PAPERS_YAML, papers)
            render.write_all(ROOT, papers)
            by_field: Dict[str, int] = {}
            for _, field, _ in applied:
                by_field[field] = by_field.get(field, 0) + 1
            detail = ", ".join(f"{field} {count}" for field, count in sorted(by_field.items()))
            print(f"applied {len(applied)} fields ({detail})")
        else:
            print("nothing to apply (fill-only)")
    return 0


def cmd_code(args: argparse.Namespace) -> int:
    all_papers = load_papers(PAPERS_YAML)

    selected = all_papers
    if args.ids:
        wanted = {value.strip() for value in args.ids.split(",") if value.strip()}
        selected = [paper for paper in selected if paper.id in wanted]
    if not args.include_existing:
        selected = [paper for paper in selected if not paper.code]
    if args.limit:
        selected = selected[: args.limit]

    token = os.environ.get(args.token_env) if args.token_env else None
    client = HttpClient(cache_dir=CACHE_DIR, ttl=args.ttl or DEFAULT_TTL)
    progress = _progress(args, len(selected), "code")
    try:
        matches = code_module.find_matches(
            selected, client, token=token, threshold=args.threshold, progress=progress
        )
    finally:
        progress.close()

    titles = {paper.id: paper.title for paper in selected}
    md_path, json_path = proposals.write_code(matches, titles, args.report, args.json)
    print(f"code: checked {len(selected)} papers, found {len(matches)} matches (token={'yes' if token else 'no'})")
    print(f"wrote {md_path}")
    print(f"wrote {json_path}")

    if args.apply:
        applied = code_module.apply_code_matches(all_papers, matches)
        if applied:
            dump_papers(PAPERS_YAML, all_papers)
            render.write_all(ROOT, all_papers)
            by_field: Dict[str, int] = {}
            for _, field, _ in applied:
                by_field[field] = by_field.get(field, 0) + 1
            detail = ", ".join(f"{field} {count}" for field, count in sorted(by_field.items()))
            print(f"applied {len(applied)} fields ({detail})")
        else:
            print("nothing to apply (fill-only)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="uie.cli", description="Awesome-UIE tooling")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="regenerate all artifacts from papers.yaml")
    build.set_defaults(func=cmd_build)

    check = sub.add_parser("validate", help="run deterministic validation")
    check.add_argument("--changed-only", action="store_true", help="only validate entries changed vs --base")
    check.add_argument("--base", default="origin/main", help="git revision to diff against (default: origin/main)")
    check.add_argument("--format", choices=["text", "json"], default="text")
    check.add_argument("--strict", action="store_true", help="treat warnings as failures")
    check.set_defaults(func=cmd_validate)

    link = sub.add_parser("check-links", help="check link and code repository liveness")
    link.add_argument("--scope", choices=["all", "url", "code", "project"], default="all")
    link.add_argument("--format", choices=["text", "json"], default="text")
    link.add_argument("--ttl", type=int, default=links.DEFAULT_TTL, help="cache TTL in seconds")
    link.add_argument("--skip-repos", action="store_true", help="skip GitHub repository checks")
    link.add_argument("--no-fail", action="store_true", help="report issues but always exit 0")
    link.set_defaults(func=cmd_check_links)
    _add_progress_args(link)

    enrich = sub.add_parser("enrich", help="suggest metadata updates from arXiv/OpenAlex/Crossref")
    enrich.add_argument("--report", default=str(ROOT / "proposals" / "enrich.md"), help="Markdown report path")
    enrich.add_argument("--json", default=str(ROOT / "proposals" / "enrich.json"), help="JSON report path")
    enrich.add_argument("--ids", help="comma-separated paper ids to limit the run")
    enrich.add_argument("--limit", type=int, help="only process the first N papers")
    enrich.add_argument("--threshold", type=float, help="override match_threshold from config")
    enrich.add_argument("--mailto", help="contact e-mail for the API polite pools")
    enrich.add_argument("--ttl", type=int, help="cache TTL in seconds (0 = refetch)")
    enrich.add_argument("--refresh", action="store_true", help="bypass the response cache")
    enrich.add_argument(
        "--apply", action="store_true", help="write high-confidence fill-only suggestions to papers.yaml"
    )
    enrich.add_argument(
        "--fields", default="doi,authors", help="comma-separated fields to apply (default: doi,authors)"
    )
    enrich.add_argument("--min-score", type=float, help="minimum match score required to apply (default: threshold)")
    enrich.set_defaults(func=cmd_enrich)
    _add_progress_args(enrich)

    disc = sub.add_parser("discover", help="find new papers from arXiv/OpenAlex/Crossref")
    disc.add_argument("--report", default=str(ROOT / "proposals" / "discover.md"), help="Markdown report path")
    disc.add_argument("--json", default=str(ROOT / "proposals" / "discover.json"), help="JSON report path")
    disc.add_argument("--since", type=int, help="look back N days (default from config)")
    disc.add_argument("--limit", type=int, default=25, help="max results per source query")
    disc.add_argument("--mailto", help="contact e-mail for the API polite pools")
    disc.add_argument("--ttl", type=int, help="cache TTL in seconds")
    disc.add_argument("--refresh", action="store_true", help="bypass the response cache")
    disc.add_argument("--min-tier", help="only auto-ingest new candidates at/above this venue tier (A/B/preprint/C)")
    disc.add_argument("--year", help="only keep candidates from these years (comma-separated, e.g. 2025)")
    disc.add_argument("--apply", action="store_true", help="append new candidates to papers.yaml as status: candidate")
    disc.set_defaults(func=cmd_discover)
    _add_progress_args(disc)

    venues = sub.add_parser("venues", help="audit venues against config/venues.yaml")
    venues.add_argument("--refresh-metrics", action="store_true", help="refresh OpenAlex metrics into venues.yaml")
    venues.add_argument("--ttl", type=int, help="cache TTL in seconds for the metric refresh")
    venues.set_defaults(func=cmd_venues)
    _add_progress_args(venues)

    llm = sub.add_parser("llm", help="LLM-assisted tldr/tags (needs an API key)")
    llm.add_argument("--report", default=str(ROOT / "proposals" / "llm.md"), help="Markdown report path")
    llm.add_argument("--json", default=str(ROOT / "proposals" / "llm.json"), help="JSON report path")
    llm.add_argument("--ids", help="comma-separated paper ids to limit the run")
    llm.add_argument("--limit", type=int, help="only process the first N papers")
    llm.add_argument("--model", help="override the model from config/llm.yaml")
    llm.add_argument("--base-url", help="override the OpenAI-compatible base URL")
    llm.add_argument("--api-key-env", help="override the API-key environment variable name")
    llm.add_argument("--all", action="store_true", help="include papers that already have a tldr")
    llm.add_argument("--apply", action="store_true", help="write tldr/tags to papers.yaml")
    llm.add_argument("--fields", default="tldr", help="comma-separated fields to apply (default: tldr)")
    llm.add_argument(
        "--tag-mode",
        choices=["fill", "merge", "replace"],
        default="fill",
        help="how to apply tags: fill (only when empty), merge (union), replace (reviewed set)",
    )
    llm.add_argument("--refresh-abstracts", action="store_true", help="bypass the source response cache")
    llm.set_defaults(func=cmd_llm)
    _add_progress_args(llm)

    code = sub.add_parser("code", help="find code repositories and project pages (GitHub)")
    code.add_argument("--report", default=str(ROOT / "proposals" / "code.md"))
    code.add_argument("--json", default=str(ROOT / "proposals" / "code.json"))
    code.add_argument("--ids", help="comma-separated paper ids to limit the run")
    code.add_argument("--limit", type=int, help="only process the first N papers")
    code.add_argument("--threshold", type=float, default=0.55, help="minimum repo match score")
    code.add_argument("--token-env", default="GITHUB_TOKEN", help="env var holding a GitHub token (optional)")
    code.add_argument("--include-existing", action="store_true", help="also check papers that already have code")
    code.add_argument("--ttl", type=int, help="cache TTL in seconds")
    code.add_argument("--apply", action="store_true", help="fill code/project links (fill-only)")
    code.set_defaults(func=cmd_code)
    _add_progress_args(code)

    return parser


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
