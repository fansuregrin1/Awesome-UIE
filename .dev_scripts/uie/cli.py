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
from pathlib import Path
from typing import Any, Dict, List

import yaml

from . import links, render, validate
from .schema import Paper, load_papers

ROOT = Path(__file__).resolve().parents[2]          # repository root
PAPERS_REL = ".dev_scripts/papers.yaml"
PAPERS_YAML = ROOT / PAPERS_REL
ALLOWLIST_YAML = ROOT / ".dev_scripts" / "validation-allowlist.yaml"
LINK_CACHE = ROOT / ".dev_scripts" / ".link-cache.json"


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

    issues += validate.validate_papers(papers, _load_allowlist())

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

    results = asyncio.run(links.check_urls(urls, cache_path=LINK_CACHE, ttl=args.ttl))

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
        return 1 if any(issue.level == "error" for issue in issues) else 0

    repos = sorted({repo for paper in papers if paper.code for repo in [links.github_repo(str(paper.code))] if repo})
    repo_results = asyncio.run(links.check_github_repos(repos, token=os.environ.get("GITHUB_TOKEN")))
    repo_issues: List[validate.Issue] = []
    for repo, info in sorted(repo_results.items()):
        if info.get("ok") is False:
            repo_issues.append(validate.Issue("error", "repo-missing", f"GitHub API status {info.get('status')}", repo))
        elif info.get("archived"):
            repo_issues.append(validate.Issue("warning", "repo-archived", "repository is archived", repo))

    if args.format == "text":
        print(f"checked {len(repos)} code repositories")
    _print_issues(repo_issues, args.format)

    all_issues = issues + repo_issues
    return 1 if any(issue.level == "error" for issue in all_issues) else 0


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
    link.set_defaults(func=cmd_check_links)

    return parser


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
