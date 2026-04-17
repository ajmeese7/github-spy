"""Collect user profile data and detect field-level changes over time."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from github_spy.models import (
    TRACKED_PROFILE_FIELDS,
    CollectionResult,
    ProfileFieldChange,
    ProfileSnapshot,
)
from github_spy.storage import Storage, utc_now_iso

if TYPE_CHECKING:
    from github_spy.client import GitHubClient


def _build_snapshot(username: str, data: dict[str, Any]) -> ProfileSnapshot:
    return ProfileSnapshot(
        username=username,
        name=data.get("name"),
        bio=data.get("bio"),
        company=data.get("company"),
        location=data.get("location"),
        blog=data.get("blog"),
        public_repos=data.get("public_repos", 0),
        public_gists=data.get("public_gists", 0),
        followers=data.get("followers", 0),
        following=data.get("following", 0),
        html_url=data.get("html_url"),
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
        fetched_at=utc_now_iso(),
        raw_json=json.dumps(data, sort_keys=True),
    )


def diff_profiles(
    old: ProfileSnapshot | None, new: ProfileSnapshot, username: str
) -> list[ProfileFieldChange]:
    """Compare two profile snapshots and return field-level changes."""
    if old is None:
        return []

    now = utc_now_iso()
    changes: list[ProfileFieldChange] = []
    for field_name in TRACKED_PROFILE_FIELDS:
        old_val = str(getattr(old, field_name)) if getattr(old, field_name) is not None else None
        new_val = str(getattr(new, field_name)) if getattr(new, field_name) is not None else None
        if old_val != new_val:
            changes.append(
                ProfileFieldChange(
                    username=username,
                    field_name=field_name,
                    old_value=old_val,
                    new_value=new_val,
                    detected_at=now,
                )
            )
    return changes


def fetch_profile(
    client: GitHubClient,
    storage: Storage,
    username: str,
) -> tuple[ProfileSnapshot, CollectionResult]:
    """Fetch the user's profile, detect changes, and return the snapshot."""
    cache_key = f"profile:{username}"
    status, data, headers = client.get_json(
        f"/users/{username}",
        cache_entry=storage.get_cache_entry(cache_key),
    )

    if status == 304:
        existing = storage.get_profile_snapshot(username)
        if existing is None:
            raise RuntimeError(f"Got 304 for {username} profile but no cached data exists")
        return existing, CollectionResult(
            username=username, collector_name="profile", changed=False
        )

    if data is None:
        raise RuntimeError(f"GitHub returned empty profile for {username}")

    storage.upsert_cache_entry(cache_key, headers)

    old_snapshot = storage.get_profile_snapshot(username)
    new_snapshot = _build_snapshot(username, data)
    changes = diff_profiles(old_snapshot, new_snapshot, username)

    storage.save_profile(username, json.dumps(data, sort_keys=True))
    storage.insert_profile_changes(changes)

    return new_snapshot, CollectionResult(
        username=username,
        collector_name="profile",
        changed=True,
        change_count=len(changes),
        details={
            "field_changes": [
                {"field": c.field_name, "old": c.old_value, "new": c.new_value} for c in changes
            ]
        },
    )
