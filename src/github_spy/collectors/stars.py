"""Collect starred repositories and detect star/unstar changes."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from github_spy.models import CollectionResult, StarChange, StarChangeKind, StarSnapshot
from github_spy.storage import Storage, utc_now_iso

if TYPE_CHECKING:
    from github_spy.client import GitHubClient


def normalize_star(raw: dict[str, Any]) -> StarSnapshot | None:
    """Convert a raw starred repo response into a typed StarSnapshot."""
    repo = raw.get("repo") or {}
    owner = (repo.get("owner") or {}).get("login")
    full_name = repo.get("full_name") or (
        f"{owner}/{repo.get('name')}" if owner and repo.get("name") else None
    )
    if not full_name:
        return None

    return StarSnapshot(
        full_name=full_name,
        repo_id=repo.get("id"),
        html_url=repo.get("html_url"),
        is_private=bool(repo.get("private")),
        is_fork=bool(repo.get("fork")),
        language=repo.get("language"),
        stargazers_count=repo.get("stargazers_count", 0) or 0,
        starred_at=raw.get("starred_at"),
        raw_json=json.dumps(raw, sort_keys=True),
    )


def diff_stars(
    previous: dict[str, StarSnapshot],
    current: dict[str, StarSnapshot],
    username: str,
) -> list[StarChange]:
    """Compare two star snapshots and return a list of changes."""
    now = utc_now_iso()
    changes: list[StarChange] = []

    for name, snap in current.items():
        if name not in previous:
            changes.append(
                StarChange(
                    username=username,
                    full_name=name,
                    kind=StarChangeKind.ADDED,
                    detected_at=now,
                    repo_id=snap.repo_id,
                    html_url=snap.html_url,
                    starred_at=snap.starred_at,
                    language=snap.language,
                )
            )

    for name, old in previous.items():
        if name not in current:
            changes.append(
                StarChange(
                    username=username,
                    full_name=name,
                    kind=StarChangeKind.REMOVED,
                    detected_at=now,
                    repo_id=old.repo_id,
                    html_url=old.html_url,
                    starred_at=old.starred_at,
                    language=old.language,
                )
            )

    return changes


PER_PAGE = 100


def fetch_stars(
    client: GitHubClient,
    storage: Storage,
    username: str,
    max_pages: int | None = None,
) -> CollectionResult:
    """Fetch starred repos and detect changes. Returns collection summary.

    max_pages=None (default) paginates to exhaustion. Caps only exist for
    tests and edge-case tuning; a cap that's hit with a full last page means
    our view is truncated, and the diff logic WILL fabricate phantom
    `unstarred` rows (same failure mode as 304 caching). Truncation is
    detected and the diff is skipped entirely in that case.
    """
    all_stars: list[StarSnapshot] = []
    changed = False
    pages_fetched = 0
    last_page_size = 0

    # No conditional caching here: a 304 on any single page would leave all_stars
    # incomplete, and the downstream diff-against-full-state would invent spurious
    # "unstarred" events for everything on cached pages. See followers.py and
    # repos.py for the same rationale.
    for page in client.paginate(
        f"/users/{username}/starred",
        params={"sort": "created", "direction": "desc"},
        accept="application/vnd.github.star+json",
        max_pages=max_pages,
        per_page=PER_PAGE,
    ):
        items = page[1]
        changed = True
        pages_fetched += 1
        last_page_size = len(items)
        for raw in items:
            snap = normalize_star(raw)
            if snap:
                all_stars.append(snap)

    truncated = max_pages is not None and pages_fetched >= max_pages and last_page_size == PER_PAGE

    changes: list[StarChange] = []
    if changed and not truncated:
        previous = storage.get_current_stars(username)
        current_map = {s.full_name: s for s in all_stars}
        changes = diff_stars(previous, current_map, username)
        storage.replace_current_stars(username, all_stars)
        storage.insert_star_changes(changes)

    return CollectionResult(
        username=username,
        collector_name="stars",
        changed=changed,
        new_count=len(all_stars),
        change_count=len(changes),
        details={
            "total_stars": len(all_stars),
            "truncated": truncated,
            "changes": [_change_summary(c) for c in changes[:20]],
        },
    )


def _change_summary(change: StarChange) -> dict[str, Any]:
    return {
        "full_name": change.full_name,
        "kind": change.kind.value,
        "language": change.language,
        "starred_at": change.starred_at,
    }
