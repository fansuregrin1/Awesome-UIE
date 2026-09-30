"""Find code repositories and project pages for papers.

Primary source: the GitHub search API (by title), validated by title/acronym
similarity. Secondary: GitHub/GitLab/Gitee URLs and ``*.github.io`` project pages
mentioned in the abstract. Report-first; ``--apply`` is fill-only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import HttpUrl, TypeAdapter

from .enrich import collect_abstracts, similarity
from .progress import Progress
from .schema import Paper
from .sources.http import HttpClient

GITHUB_SEARCH = "https://api.github.com/search/repositories"
MIN_INTERVAL = 2.0  # GitHub search API is 10 req/min unauthenticated, 30 with a token
USER_AGENT = "Awesome-UIE-code-finder/1.0 (+https://github.com/fansuregrin1/Awesome-UIE)"

_CODE_RE = re.compile(r"(?:https?://)?(?:www\.)?(?:github|gitlab|gitee)\.com/[A-Za-z0-9_.\-/]+", re.IGNORECASE)
_PROJECT_RE = re.compile(r"https?://[^\s)\]}>,'\"]*\.github\.io/[^\s)\]}>,'\"]*", re.IGNORECASE)
_URL_ADAPTER = TypeAdapter(HttpUrl)

_STOPWORDS = {"underwater", "image", "deep", "a", "the", "an", "for", "based", "via"}


@dataclass
class CodeMatch:
    paper_id: str
    code: Optional[str]
    project: Optional[str]
    repo: Optional[str]
    score: float
    reason: str
    source: str  # "abstract" | "github"


def _as_url(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        return str(_URL_ADAPTER.validate_python(value.strip()))
    except Exception:
        return None


def _clean_url(url: str) -> Optional[str]:
    return _as_url(url.rstrip(".,;:)"))


def is_repo_url(url: Optional[str]) -> bool:
    """Require ``host/owner/repo`` (reject bare ``github.com/user/``)."""
    match = re.match(r"https?://(?:www\.)?(?:github|gitlab|gitee)\.com/([^/?#]+)/([^/?#]+)", url or "")
    return bool(match and match.group(1) and match.group(2))


def _slug_words(text: Optional[str]) -> str:
    return re.sub(r"[-_]+", " ", text or "").strip()


def method_token(title: str) -> Optional[str]:
    """The leading method acronym, e.g. 'WaterMamba' in 'WaterMamba: ...'."""
    head = re.split(r"[:\-–—(]", title or "", maxsplit=1)[0].strip()
    token = head.split(" ")[0]
    if len(token) >= 4 and token.lower() not in _STOPWORDS:
        return token
    return None


def score_repo(paper: Paper, repo: Dict[str, Any]) -> Tuple[float, str]:
    name = _slug_words(repo.get("name"))
    description = repo.get("description") or ""
    topics = " ".join(repo.get("topics") or [])
    haystack = f"{name} {description} {topics}".lower()

    score = max(similarity(paper.title, name), similarity(paper.title, description))
    reasons: List[str] = []
    token = method_token(paper.title)
    if token and token.lower() in haystack:
        score = max(score, 0.6)
        reasons.append(f"method '{token}'")
    if paper.arxiv_id and paper.arxiv_id in haystack:
        score = max(score, 0.95)
        reasons.append("arXiv id")
    if score >= 0.5:
        reasons.append(f"title~{score:.2f}")
    return round(score, 3), ", ".join(reasons)


def _headers(token: Optional[str]) -> Dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _search_repos(
    paper: Paper, client: HttpClient, headers: Dict[str, str], min_interval: float
) -> List[Dict[str, Any]]:
    query = paper.title if "underwater" in paper.title.lower() else f"{paper.title} underwater"
    try:
        data = client.get_json(
            GITHUB_SEARCH, params={"q": query, "per_page": 5}, headers=headers, min_interval=min_interval
        )
    except RuntimeError:
        return []
    return data.get("items", []) or []


def find_matches(
    papers: Sequence[Paper],
    client: HttpClient,
    token: Optional[str] = None,
    threshold: float = 0.55,
    progress: Optional[Progress] = None,
) -> List[CodeMatch]:
    headers = _headers(token)
    # GitHub search: 10 req/min unauthenticated, 30 with a token.
    min_interval = 2.0 if token else 6.5
    abstracts = collect_abstracts(papers, client)
    matches: List[CodeMatch] = []

    for paper in papers:
        best: Optional[CodeMatch] = None

        abstract = abstracts.get(paper.id, "")
        code_match = _CODE_RE.search(abstract)
        project_match = _PROJECT_RE.search(abstract)
        code_url = _clean_url(code_match.group(0)) if code_match else None
        if code_url and not is_repo_url(code_url):
            code_url = None
        project_url = _clean_url(project_match.group(0)) if project_match else None
        if code_url:
            best = CodeMatch(paper.id, code_url, project_url, None, 1.0, "abstract link", "abstract")
        else:
            for repo in _search_repos(paper, client, headers, min_interval):
                score, reason = score_repo(paper, repo)
                if best is None or score > best.score:
                    homepage = repo.get("homepage")
                    best = CodeMatch(
                        paper.id,
                        repo.get("html_url"),
                        _as_url(homepage) if homepage else None,
                        repo.get("full_name"),
                        score,
                        reason,
                        "github",
                    )
            if best is not None and best.score < threshold:
                best = None

        if best is not None and (best.code or best.project):
            matches.append(best)
        if progress:
            progress.update(paper.id)

    return matches


def apply_code_matches(papers: Sequence[Paper], matches: Sequence[CodeMatch]) -> List[tuple]:
    """Fill-only: set ``code``/``project`` only when currently empty."""
    by_id = {paper.id: paper for paper in papers}
    applied: List[tuple] = []
    for match in matches:
        paper = by_id.get(match.paper_id)
        if paper is None:
            continue
        if match.code and not paper.code:
            paper.code = _URL_ADAPTER.validate_python(match.code)
            applied.append((paper.id, "code", match.code))
        if match.project and not paper.project:
            paper.project = _URL_ADAPTER.validate_python(match.project)
            applied.append((paper.id, "project", match.project))
    return applied
