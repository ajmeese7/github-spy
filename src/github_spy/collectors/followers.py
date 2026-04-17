"""Collect followers and following lists, detect changes over time."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from github_spy.models import (
    CollectionResult,
    FollowerChange,
    FollowerChangeKind,
    FollowerSnapshot,
)
from github_spy.storage import Storage, utc_now_iso

if TYPE_CHECKING:
    from github_spy.client import GitHubClient


def _normalize_user(raw: dict[str, Any]) -> FollowerSnapshot:
    return FollowerSnapshot(
        login=raw.get("login", ""),
        user_id=raw.get("id"),
        html_url=raw.get("html_url"),
    )


def _diff_user_lists(
    previous: dict[str, FollowerSnapshot],
    current: dict[str, FollowerSnapshot],
    username: str,
) -> list[FollowerChange]:
    now = utc_now_iso()
    changes: list[FollowerChange] = []

    for login, snap in current.items():
        if login not in previous:
            changes.append(
                FollowerChange(
                    username=username,
                    login=login,
                    kind=FollowerChangeKind.FOLLOWED,
                    detected_at=now,
                    user_id=snap.user_id,
                )
            )

    for login, snap in previous.items():
        if login not in current:
            changes.append(
                FollowerChange(
                    username=username,
                    login=login,
                    kind=FollowerChangeKind.UNFOLLOWED,
                    detected_at=now,
                    user_id=snap.user_id,
                )
            )

    return changes


PER_PAGE = 100


def _fetch_user_list(
    client: GitHubClient,
    path: str,
    max_pages: int | None,
) -> tuple[bool, list[FollowerSnapshot], bool]:
    """Return (changed, users, truncated).

    truncated=True when the caller capped max_pages AND the final page was
    completely full — meaning GitHub has more rows beyond our window. In that
    case callers must skip the replace+diff to avoid fabricating phantom
    unfollow rows for users who fell off the bottom of our window.

    No conditional caching on paginated list endpoints: a 304 on a single
    page would leave the list partial, same fabrication bug. See
    collectors/stars.py for the full explanation.
    """
    all_users: list[FollowerSnapshot] = []
    changed = False
    pages_fetched = 0
    last_page_size = 0

    for page in client.paginate(path, max_pages=max_pages, per_page=PER_PAGE):
        items = page[1]
        changed = True
        pages_fetched += 1
        last_page_size = len(items)
        for raw in items:
            snap = _normalize_user(raw)
            if snap.login:
                all_users.append(snap)

    truncated = max_pages is not None and pages_fetched >= max_pages and last_page_size == PER_PAGE
    return changed, all_users, truncated


def fetch_followers(
    client: GitHubClient,
    storage: Storage,
    username: str,
    max_pages: int | None = None,
) -> CollectionResult:
    """Fetch followers list and detect follow/unfollow changes."""
    changed, all_followers, truncated = _fetch_user_list(
        client, f"/users/{username}/followers", max_pages
    )

    changes: list[FollowerChange] = []
    if changed and not truncated:
        previous = storage.get_current_followers(username)
        current_map = {f.login: f for f in all_followers}
        changes = _diff_user_lists(previous, current_map, username)
        storage.replace_current_followers(username, all_followers)
        storage.insert_follower_changes(changes)

    return CollectionResult(
        username=username,
        collector_name="followers",
        changed=changed,
        new_count=len(all_followers),
        change_count=len(changes),
        details={
            "total_followers": len(all_followers),
            "truncated": truncated,
            "changes": [{"login": c.login, "kind": c.kind.value} for c in changes[:20]],
        },
    )


def fetch_following(
    client: GitHubClient,
    storage: Storage,
    username: str,
    max_pages: int | None = None,
) -> CollectionResult:
    """Fetch following list and detect follow/unfollow changes."""
    changed, all_following, truncated = _fetch_user_list(
        client, f"/users/{username}/following", max_pages
    )

    changes: list[FollowerChange] = []
    if changed and not truncated:
        previous = storage.get_current_following(username)
        current_map = {f.login: f for f in all_following}
        changes = _diff_user_lists(previous, current_map, username)
        storage.replace_current_following(username, all_following)
        storage.insert_following_changes(changes)

    return CollectionResult(
        username=username,
        collector_name="following",
        changed=changed,
        new_count=len(all_following),
        change_count=len(changes),
        details={
            "total_following": len(all_following),
            "truncated": truncated,
            "changes": [{"login": c.login, "kind": c.kind.value} for c in changes[:20]],
        },
    )
