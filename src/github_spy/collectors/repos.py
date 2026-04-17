"""Collect public repositories and detect creation/deletion over time."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from github_spy.models import CollectionResult, RepoChange, RepoChangeKind, RepoSnapshot
from github_spy.storage import Storage, utc_now_iso

if TYPE_CHECKING:
    from github_spy.client import GitHubClient


def normalize_repo(raw: dict[str, Any]) -> RepoSnapshot | None:
    """Convert a raw GitHub repo dict into a typed RepoSnapshot."""
    full_name = raw.get("full_name")
    if not full_name:
        return None

    return RepoSnapshot(
        full_name=full_name,
        repo_id=raw.get("id"),
        html_url=raw.get("html_url"),
        language=raw.get("language"),
        description=raw.get("description"),
        is_fork=bool(raw.get("fork")),
        stargazers_count=raw.get("stargazers_count", 0) or 0,
        raw_json=json.dumps(raw, sort_keys=True),
    )


def diff_repos(
    previous: dict[str, RepoSnapshot],
    current: dict[str, RepoSnapshot],
    username: str,
) -> list[RepoChange]:
    """Compare two repo snapshots and return a list of changes."""
    now = utc_now_iso()
    changes: list[RepoChange] = []

    for name, snap in current.items():
        if name not in previous:
            changes.append(
                RepoChange(
                    username=username,
                    repo_name=name,
                    kind=RepoChangeKind.CREATED,
                    detected_at=now,
                    language=snap.language,
                    repo_id=snap.repo_id,
                )
            )

    for name, old in previous.items():
        if name not in current:
            changes.append(
                RepoChange(
                    username=username,
                    repo_name=name,
                    kind=RepoChangeKind.DELETED,
                    detected_at=now,
                    language=old.language,
                    repo_id=old.repo_id,
                )
            )

    return changes


PER_PAGE = 100


def fetch_repos(
    client: GitHubClient,
    storage: Storage,
    username: str,
    max_pages: int | None = None,
) -> CollectionResult:
    """Fetch public repos and detect creation/deletion changes.

    max_pages=None (default) paginates to exhaustion. A hit cap with a full
    last page means truncation and the diff is skipped to avoid phantom
    `deleted` rows for repos that fell off the bottom of our window.
    """
    all_repos: list[RepoSnapshot] = []
    changed = False
    pages_fetched = 0
    last_page_size = 0

    # No conditional caching: see collectors/stars.py for rationale.
    for page in client.paginate(
        f"/users/{username}/repos",
        params={"type": "owner", "sort": "updated", "direction": "desc"},
        max_pages=max_pages,
        per_page=PER_PAGE,
    ):
        items = page[1]
        changed = True
        pages_fetched += 1
        last_page_size = len(items)
        for raw in items:
            snap = normalize_repo(raw)
            if snap:
                all_repos.append(snap)

    truncated = max_pages is not None and pages_fetched >= max_pages and last_page_size == PER_PAGE

    changes: list[RepoChange] = []
    if changed and not truncated:
        previous = storage.get_current_repos(username)
        current_map = {r.full_name: r for r in all_repos}
        changes = diff_repos(previous, current_map, username)
        storage.replace_current_repos(username, all_repos)
        storage.insert_repo_changes(changes)

    return CollectionResult(
        username=username,
        collector_name="repos",
        changed=changed,
        new_count=len(all_repos),
        change_count=len(changes),
        details={
            "total_repos": len(all_repos),
            "truncated": truncated,
            "changes": [
                {"repo_name": c.repo_name, "kind": c.kind.value, "language": c.language}
                for c in changes[:20]
            ],
        },
    )
