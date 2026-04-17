"""Tests for collector diff logic (pure functions, no network calls)."""

from __future__ import annotations

import inspect
from typing import Any, cast

import pytest

from github_spy.client import GitHubClient
from github_spy.collectors import followers as followers_module
from github_spy.collectors import repos as repos_module
from github_spy.collectors import stars as stars_module
from github_spy.collectors.followers import (
    _diff_user_lists,
    fetch_followers,
    fetch_following,
)
from github_spy.collectors.profile import diff_profiles
from github_spy.collectors.repos import diff_repos, fetch_repos
from github_spy.collectors.stars import diff_stars, fetch_stars
from github_spy.models import (
    FollowerChangeKind,
    FollowerSnapshot,
    ProfileSnapshot,
    RepoChangeKind,
    RepoSnapshot,
    StarChangeKind,
    StarSnapshot,
)
from github_spy.storage import Storage


class TestDiffStars:
    def test_detect_added_star(self) -> None:
        previous: dict[str, StarSnapshot] = {}
        current = {"new/repo": StarSnapshot(full_name="new/repo", language="Rust")}
        changes = diff_stars(previous, current, "octocat")
        assert len(changes) == 1
        assert changes[0].kind == StarChangeKind.ADDED
        assert changes[0].full_name == "new/repo"

    def test_detect_removed_star(self) -> None:
        previous = {"old/repo": StarSnapshot(full_name="old/repo", language="Go")}
        current: dict[str, StarSnapshot] = {}
        changes = diff_stars(previous, current, "octocat")
        assert len(changes) == 1
        assert changes[0].kind == StarChangeKind.REMOVED
        assert changes[0].full_name == "old/repo"

    def test_no_changes(self) -> None:
        both = {"same/repo": StarSnapshot(full_name="same/repo")}
        changes = diff_stars(both, both, "octocat")
        assert len(changes) == 0

    def test_mixed_changes(self) -> None:
        previous = {
            "kept/repo": StarSnapshot(full_name="kept/repo"),
            "removed/repo": StarSnapshot(full_name="removed/repo"),
        }
        current = {
            "kept/repo": StarSnapshot(full_name="kept/repo"),
            "added/repo": StarSnapshot(full_name="added/repo"),
        }
        changes = diff_stars(previous, current, "octocat")
        assert len(changes) == 2
        kinds = {c.full_name: c.kind for c in changes}
        assert kinds["added/repo"] == StarChangeKind.ADDED
        assert kinds["removed/repo"] == StarChangeKind.REMOVED

    def test_language_preserved_in_change(self) -> None:
        current = {"new/repo": StarSnapshot(full_name="new/repo", language="Python")}
        changes = diff_stars({}, current, "octocat")
        assert changes[0].language == "Python"


class TestDiffFollowers:
    def test_detect_new_follower(self) -> None:
        previous: dict[str, FollowerSnapshot] = {}
        current = {"newfan": FollowerSnapshot(login="newfan", user_id=1)}
        changes = _diff_user_lists(previous, current, "octocat")
        assert len(changes) == 1
        assert changes[0].kind == FollowerChangeKind.FOLLOWED

    def test_detect_unfollower(self) -> None:
        previous = {"oldfan": FollowerSnapshot(login="oldfan", user_id=2)}
        current: dict[str, FollowerSnapshot] = {}
        changes = _diff_user_lists(previous, current, "octocat")
        assert len(changes) == 1
        assert changes[0].kind == FollowerChangeKind.UNFOLLOWED

    def test_no_changes(self) -> None:
        both = {"stable": FollowerSnapshot(login="stable")}
        changes = _diff_user_lists(both, both, "octocat")
        assert len(changes) == 0


class TestDiffRepos:
    def test_detect_new_repo(self) -> None:
        previous: dict[str, RepoSnapshot] = {}
        current = {"me/newproj": RepoSnapshot(full_name="me/newproj", language="Rust")}
        changes = diff_repos(previous, current, "octocat")
        assert len(changes) == 1
        assert changes[0].kind == RepoChangeKind.CREATED

    def test_detect_deleted_repo(self) -> None:
        previous = {"me/oldproj": RepoSnapshot(full_name="me/oldproj")}
        current: dict[str, RepoSnapshot] = {}
        changes = diff_repos(previous, current, "octocat")
        assert len(changes) == 1
        assert changes[0].kind == RepoChangeKind.DELETED

    def test_no_changes(self) -> None:
        both = {"me/proj": RepoSnapshot(full_name="me/proj")}
        changes = diff_repos(both, both, "octocat")
        assert len(changes) == 0


class _FakePaginatedClient:
    """Minimal GitHubClient stub for collector regression tests.

    Captures the paginate call kwargs so we can assert the collectors do
    NOT pass conditional-request cache hooks, which would reintroduce the
    phantom-unfollow bug when upstream returns 304 on some pages.
    """

    def __init__(self, pages: list[list[dict[str, Any]]]) -> None:
        self.pages = pages
        self.last_paginate_kwargs: dict[str, Any] = {}
        self.get_json_calls: list[tuple[str, Any]] = []
        self.last_rate_limit = None

    def paginate(self, path: str, *, max_pages: int = 10, **kwargs: Any):
        # Record whatever the collector passed along.
        self.last_paginate_kwargs = {"path": path, "max_pages": max_pages, **kwargs}
        for i, page_items in enumerate(self.pages, start=1):
            yield 200, page_items, i

    def get_json(self, path: str, *, cache_entry: Any = None):
        self.get_json_calls.append((path, cache_entry))
        return 200, {}, {}


class TestCollectorsDoNotUseCache:
    """Regression: full-state collectors must NOT pass cache hooks to paginate.

    The original bug was: per-page 304 responses left the collector with only
    part of the user list, then the 'replace-current / diff' step would log
    phantom unfollows for everyone on cached pages.
    """

    def test_followers_do_not_forward_cache_hooks(self, db_path) -> None:
        client = _FakePaginatedClient(
            pages=[[{"login": "fan1", "id": 1, "html_url": "https://github.com/fan1"}]]
        )
        with Storage(db_path) as storage:
            result = fetch_followers(cast(GitHubClient, client), storage, "octocat", max_pages=1)
        assert "cache_getter" not in client.last_paginate_kwargs
        assert "cache_setter" not in client.last_paginate_kwargs
        assert result.changed is True

    def test_following_do_not_forward_cache_hooks(self, db_path) -> None:
        client = _FakePaginatedClient(pages=[[{"login": "idol1", "id": 1}]])
        with Storage(db_path) as storage:
            fetch_following(cast(GitHubClient, client), storage, "octocat", max_pages=1)
        assert "cache_getter" not in client.last_paginate_kwargs
        assert "cache_setter" not in client.last_paginate_kwargs

    def test_stars_do_not_forward_cache_hooks(self, db_path) -> None:
        raw_star = {
            "starred_at": "2024-01-15T10:00:00Z",
            "repo": {
                "id": 1,
                "full_name": "lang/python",
                "html_url": "https://github.com/lang/python",
                "private": False,
                "fork": False,
                "language": "Python",
                "stargazers_count": 10,
                "owner": {"login": "lang"},
                "name": "python",
            },
        }
        client = _FakePaginatedClient(pages=[[raw_star]])
        with Storage(db_path) as storage:
            fetch_stars(cast(GitHubClient, client), storage, "octocat", max_pages=1)
        assert "cache_getter" not in client.last_paginate_kwargs
        assert "cache_setter" not in client.last_paginate_kwargs

    def test_repos_do_not_forward_cache_hooks(self, db_path) -> None:
        raw_repo = {
            "id": 1,
            "full_name": "octocat/proj",
            "html_url": "https://github.com/octocat/proj",
            "language": "Python",
            "description": "x",
            "fork": False,
            "stargazers_count": 5,
            "owner": {"login": "octocat"},
            "name": "proj",
        }
        client = _FakePaginatedClient(pages=[[raw_repo]])
        with Storage(db_path) as storage:
            fetch_repos(cast(GitHubClient, client), storage, "octocat", max_pages=1)
        assert "cache_getter" not in client.last_paginate_kwargs
        assert "cache_setter" not in client.last_paginate_kwargs


class TestNoPartialFetchPhantomChanges:
    """If we were to pass the same full list twice, the second run must not
    produce phantom unfollow/add events. This is the behavioural guarantee
    that matters for the actual bug.
    """

    def test_followers_stable_twice_no_phantom(self, db_path) -> None:
        page = [{"login": "fan1", "id": 1, "html_url": "https://github.com/fan1"}]
        client = _FakePaginatedClient(pages=[page])
        with Storage(db_path) as storage:
            first = fetch_followers(cast(GitHubClient, client), storage, "octocat", max_pages=1)
            # Second identical fetch.
            client2 = _FakePaginatedClient(pages=[page])
            second = fetch_followers(cast(GitHubClient, client2), storage, "octocat", max_pages=1)
        assert first.change_count == 1  # initial add
        assert second.change_count == 0  # stable state, no phantom unfollow/refollow

    def test_stars_stable_twice_no_phantom(self, db_path) -> None:
        raw_star = {
            "starred_at": "2024-01-15T10:00:00Z",
            "repo": {
                "id": 1,
                "full_name": "lang/python",
                "html_url": "https://github.com/lang/python",
                "private": False,
                "fork": False,
                "language": "Python",
                "stargazers_count": 10,
                "owner": {"login": "lang"},
                "name": "python",
            },
        }
        with Storage(db_path) as storage:
            first = fetch_stars(
                cast(GitHubClient, _FakePaginatedClient(pages=[[raw_star]])),
                storage,
                "octocat",
                max_pages=1,
            )
            second = fetch_stars(
                cast(GitHubClient, _FakePaginatedClient(pages=[[raw_star]])),
                storage,
                "octocat",
                max_pages=1,
            )
        assert first.change_count == 1
        assert second.change_count == 0


class TestCollectorModuleSanity:
    """Static check: the collector source must not reintroduce cache hooks."""

    @pytest.mark.parametrize(
        "module",
        [stars_module, repos_module, followers_module],
    )
    def test_no_cache_getter_in_source(self, module: Any) -> None:
        src = inspect.getsource(module)
        assert "cache_getter" not in src, (
            f"{module.__name__} reintroduced cache_getter; this reopens the phantom-change bug"
        )
        assert "cache_setter" not in src, (
            f"{module.__name__} reintroduced cache_setter; this reopens the phantom-change bug"
        )


class TestDiffProfiles:
    def _make_profile(self, **kwargs) -> ProfileSnapshot:
        defaults = {
            "username": "octocat",
            "name": "Octocat",
            "bio": "Hi",
            "company": "@github",
            "location": "SF",
            "blog": "https://github.blog",
            "public_repos": 50,
            "public_gists": 5,
            "followers": 1000,
            "following": 10,
        }
        defaults.update(kwargs)
        return ProfileSnapshot(**defaults)

    def test_no_changes(self) -> None:
        profile = self._make_profile()
        changes = diff_profiles(profile, profile, "octocat")
        assert len(changes) == 0

    def test_detect_bio_change(self) -> None:
        old = self._make_profile(bio="Old bio")
        new = self._make_profile(bio="New bio")
        changes = diff_profiles(old, new, "octocat")
        assert len(changes) == 1
        assert changes[0].field_name == "bio"
        assert changes[0].old_value == "Old bio"
        assert changes[0].new_value == "New bio"

    def test_detect_follower_count_change(self) -> None:
        old = self._make_profile(followers=100)
        new = self._make_profile(followers=200)
        changes = diff_profiles(old, new, "octocat")
        bio_changes = [c for c in changes if c.field_name == "followers"]
        assert len(bio_changes) == 1
        assert bio_changes[0].old_value == "100"
        assert bio_changes[0].new_value == "200"

    def test_multiple_changes(self) -> None:
        old = self._make_profile(bio="A", company="X", location="NY")
        new = self._make_profile(bio="B", company="Y", location="NY")
        changes = diff_profiles(old, new, "octocat")
        changed_fields = {c.field_name for c in changes}
        assert changed_fields == {"bio", "company"}

    def test_none_old_returns_empty(self) -> None:
        new = self._make_profile()
        changes = diff_profiles(None, new, "octocat")
        assert len(changes) == 0
