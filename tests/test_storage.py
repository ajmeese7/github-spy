"""Tests for the SQLite storage layer."""

from __future__ import annotations

import json

from github_spy.models import (
    FollowerChange,
    FollowerChangeKind,
    FollowerSnapshot,
    NormalizedEvent,
    ProfileFieldChange,
    RepoChange,
    RepoChangeKind,
    RepoSnapshot,
    StarChange,
    StarChangeKind,
    StarSnapshot,
)
from github_spy.storage import Storage


class TestMetadata:
    def test_set_and_get(self, storage: Storage) -> None:
        storage.set_metadata("key1", "value1")
        assert storage.get_metadata("key1") == "value1"

    def test_get_missing_returns_none(self, storage: Storage) -> None:
        assert storage.get_metadata("nonexistent") is None

    def test_upsert_overwrites(self, storage: Storage) -> None:
        storage.set_metadata("key1", "v1")
        storage.set_metadata("key1", "v2")
        assert storage.get_metadata("key1") == "v2"

    def test_schema_version_set_on_init(self, storage: Storage) -> None:
        assert storage.get_metadata("schema_version") == "2"


class TestEvents:
    def _make_event(self, event_id: str = "ev1", **kwargs) -> NormalizedEvent:
        defaults = {
            "event_id": event_id,
            "username": "octocat",
            "event_type": "PushEvent",
            "created_at": "2024-01-15T10:00:00Z",
            "repo_name": "octocat/hello-world",
            "actor_login": "octocat",
            "action": None,
            "is_public": True,
            "raw_json": "{}",
            "source_page": 1,
            "source_rank": 1,
        }
        defaults.update(kwargs)
        return NormalizedEvent(**defaults)

    def test_insert_returns_new_events(self, storage: Storage) -> None:
        events = [self._make_event("ev1"), self._make_event("ev2")]
        inserted = storage.insert_events(events)
        assert len(inserted) == 2

    def test_deduplication_by_event_id(self, storage: Storage) -> None:
        ev = self._make_event("ev1")
        storage.insert_events([ev])
        inserted = storage.insert_events([ev])
        assert len(inserted) == 0

    def test_recent_events_ordered_by_date(self, storage: Storage) -> None:
        storage.insert_events(
            [
                self._make_event("ev1", created_at="2024-01-10T00:00:00Z"),
                self._make_event("ev2", created_at="2024-01-15T00:00:00Z"),
                self._make_event("ev3", created_at="2024-01-12T00:00:00Z"),
            ]
        )
        rows = storage.recent_events("octocat", limit=3)
        dates = [r["created_at"] for r in rows]
        assert dates == sorted(dates, reverse=True)

    def test_event_count(self, storage: Storage) -> None:
        storage.insert_events([self._make_event("ev1"), self._make_event("ev2")])
        assert storage.event_count("octocat") == 2
        assert storage.event_count("nobody") == 0

    def test_multi_user_isolation(self, storage: Storage) -> None:
        storage.insert_events([self._make_event("ev1", username="alice")])
        storage.insert_events([self._make_event("ev2", username="bob")])
        assert storage.event_count("alice") == 1
        assert storage.event_count("bob") == 1


class TestStars:
    def _make_star(self, name: str = "owner/repo", **kwargs) -> StarSnapshot:
        defaults = {
            "full_name": name,
            "repo_id": 1,
            "html_url": f"https://github.com/{name}",
            "is_private": False,
            "is_fork": False,
            "language": "Python",
            "stargazers_count": 100,
            "starred_at": "2024-01-15T10:00:00Z",
            "raw_json": "{}",
        }
        defaults.update(kwargs)
        return StarSnapshot(**defaults)

    def test_replace_and_get_current_stars(self, storage: Storage) -> None:
        stars = [self._make_star("a/one"), self._make_star("b/two")]
        storage.replace_current_stars("octocat", stars)
        current = storage.get_current_stars("octocat")
        assert set(current.keys()) == {"a/one", "b/two"}

    def test_replace_clears_previous(self, storage: Storage) -> None:
        storage.replace_current_stars("octocat", [self._make_star("a/old")])
        storage.replace_current_stars("octocat", [self._make_star("b/new")])
        current = storage.get_current_stars("octocat")
        assert "a/old" not in current
        assert "b/new" in current

    def test_star_count(self, storage: Storage) -> None:
        storage.replace_current_stars(
            "octocat",
            [
                self._make_star("a/one"),
                self._make_star("b/two"),
            ],
        )
        assert storage.star_count("octocat") == 2

    def test_insert_and_query_star_changes(self, storage: Storage) -> None:
        changes = [
            StarChange(
                username="octocat",
                full_name="a/repo",
                kind=StarChangeKind.ADDED,
                detected_at="2024-01-15T10:00:00Z",
                language="Python",
            ),
            StarChange(
                username="octocat",
                full_name="b/repo",
                kind=StarChangeKind.REMOVED,
                detected_at="2024-01-15T10:00:00Z",
            ),
        ]
        storage.insert_star_changes(changes)
        rows = storage.recent_star_changes("octocat")
        assert len(rows) == 2
        kinds = {r["event_kind"] for r in rows}
        assert kinds == {"star_added", "star_removed"}


class TestFollowers:
    def _make_follower(self, login: str = "fan1") -> FollowerSnapshot:
        return FollowerSnapshot(login=login, user_id=1, html_url=f"https://github.com/{login}")

    def test_replace_and_get_followers(self, storage: Storage) -> None:
        followers = [self._make_follower("fan1"), self._make_follower("fan2")]
        storage.replace_current_followers("octocat", followers)
        current = storage.get_current_followers("octocat")
        assert set(current.keys()) == {"fan1", "fan2"}

    def test_follower_count(self, storage: Storage) -> None:
        storage.replace_current_followers("octocat", [self._make_follower("x")])
        assert storage.follower_count("octocat") == 1

    def test_insert_follower_changes(self, storage: Storage) -> None:
        changes = [
            FollowerChange(
                username="octocat",
                login="newfan",
                kind=FollowerChangeKind.FOLLOWED,
                detected_at="2024-01-15T10:00:00Z",
                user_id=42,
            ),
        ]
        storage.insert_follower_changes(changes)
        rows = storage.recent_follower_changes("octocat")
        assert len(rows) == 1
        assert rows[0]["login"] == "newfan"
        assert rows[0]["event_kind"] == "followed"


class TestFollowing:
    def test_replace_and_get_following(self, storage: Storage) -> None:
        following = [
            FollowerSnapshot(login="idol1", user_id=1),
            FollowerSnapshot(login="idol2", user_id=2),
        ]
        storage.replace_current_following("octocat", following)
        current = storage.get_current_following("octocat")
        assert set(current.keys()) == {"idol1", "idol2"}

    def test_following_count(self, storage: Storage) -> None:
        storage.replace_current_following(
            "octocat",
            [
                FollowerSnapshot(login="x", user_id=1),
            ],
        )
        assert storage.following_count("octocat") == 1


class TestRepos:
    def _make_repo(self, name: str = "octocat/proj") -> RepoSnapshot:
        return RepoSnapshot(full_name=name, repo_id=1, language="Python", raw_json="{}")

    def test_replace_and_get_repos(self, storage: Storage) -> None:
        repos = [self._make_repo("a/one"), self._make_repo("b/two")]
        storage.replace_current_repos("octocat", repos)
        current = storage.get_current_repos("octocat")
        assert set(current.keys()) == {"a/one", "b/two"}

    def test_repo_count(self, storage: Storage) -> None:
        storage.replace_current_repos("octocat", [self._make_repo()])
        assert storage.repo_count("octocat") == 1

    def test_insert_repo_changes(self, storage: Storage) -> None:
        changes = [
            RepoChange(
                username="octocat",
                repo_name="octocat/new-proj",
                kind=RepoChangeKind.CREATED,
                detected_at="2024-01-15T10:00:00Z",
                language="Rust",
            ),
        ]
        storage.insert_repo_changes(changes)
        rows = storage.recent_repo_changes("octocat")
        assert len(rows) == 1
        assert rows[0]["event_kind"] == "created"


class TestProfiles:
    def test_save_and_get_profile(self, storage: Storage) -> None:
        profile_data = {"login": "octocat", "name": "The Octocat", "followers": 1000}
        storage.save_profile("octocat", json.dumps(profile_data))
        result = storage.get_profile("octocat")
        assert result is not None
        assert result["name"] == "The Octocat"

    def test_get_profile_snapshot(self, storage: Storage) -> None:
        profile_data = {"login": "octocat", "name": "Cat", "followers": 500, "following": 10}
        storage.save_profile("octocat", json.dumps(profile_data))
        snap = storage.get_profile_snapshot("octocat")
        assert snap is not None
        assert snap.username == "octocat"
        assert snap.name == "Cat"
        assert snap.followers == 500

    def test_insert_profile_changes(self, storage: Storage) -> None:
        changes = [
            ProfileFieldChange(
                username="octocat",
                field_name="bio",
                old_value="Old bio",
                new_value="New bio",
                detected_at="2024-01-15T10:00:00Z",
            ),
        ]
        storage.insert_profile_changes(changes)
        rows = storage.recent_profile_changes("octocat")
        assert len(rows) == 1
        assert rows[0]["field_name"] == "bio"
        assert rows[0]["old_value"] == "Old bio"
        assert rows[0]["new_value"] == "New bio"


class TestAnalytics:
    def test_event_counts_by_type(self, storage: Storage) -> None:
        events = [
            NormalizedEvent(
                event_id=f"ev{i}",
                username="octocat",
                event_type=t,
                created_at="2024-01-15T10:00:00Z",
                repo_name="x/y",
                actor_login="octocat",
                action=None,
                is_public=True,
                raw_json="{}",
                source_page=1,
                source_rank=i,
            )
            for i, t in enumerate(["PushEvent", "PushEvent", "WatchEvent"])
        ]
        storage.insert_events(events)
        counts = storage.event_counts_by_type("octocat")
        count_dict = dict(counts)
        assert count_dict["PushEvent"] == 2
        assert count_dict["WatchEvent"] == 1

    def test_star_language_distribution(self, storage: Storage) -> None:
        stars = [
            StarSnapshot(full_name="a/1", language="Python", raw_json="{}"),
            StarSnapshot(full_name="a/2", language="Python", raw_json="{}"),
            StarSnapshot(full_name="a/3", language="Rust", raw_json="{}"),
            StarSnapshot(full_name="a/4", language=None, raw_json="{}"),
        ]
        storage.replace_current_stars("octocat", stars)
        dist = dict(storage.star_language_distribution("octocat"))
        assert dist["Python"] == 2
        assert dist["Rust"] == 1
        assert dist["Unknown"] == 1

    def test_top_repos_by_events(self, storage: Storage) -> None:
        events = []
        for i in range(5):
            events.append(
                NormalizedEvent(
                    event_id=f"hot{i}",
                    username="octocat",
                    event_type="PushEvent",
                    created_at="2024-01-15T10:00:00Z",
                    repo_name="hot/repo",
                    actor_login="octocat",
                    action=None,
                    is_public=True,
                    raw_json="{}",
                    source_page=1,
                    source_rank=i,
                )
            )
        events.append(
            NormalizedEvent(
                event_id="cold1",
                username="octocat",
                event_type="PushEvent",
                created_at="2024-01-15T10:00:00Z",
                repo_name="cold/repo",
                actor_login="octocat",
                action=None,
                is_public=True,
                raw_json="{}",
                source_page=1,
                source_rank=6,
            )
        )
        storage.insert_events(events)
        top = storage.top_repos_by_events("octocat", limit=2)
        assert top[0] == ("hot/repo", 5)
        assert top[1] == ("cold/repo", 1)


class TestQueryEvents:
    """Coverage for the new filterable events query methods."""

    def _seed(self, storage: Storage) -> None:
        storage.insert_events(
            [
                NormalizedEvent(
                    event_id="push1",
                    username="octocat",
                    event_type="PushEvent",
                    created_at="2024-01-10T10:00:00Z",
                    repo_name="owner/repo",
                    actor_login="octocat",
                    action=None,
                    is_public=True,
                    raw_json=json.dumps(
                        {"type": "PushEvent", "payload": {"commits": []}}, sort_keys=True
                    ),
                    source_page=1,
                    source_rank=1,
                    url="https://github.com/owner/repo/compare/a...b",
                    details="pushed 1 commit",
                ),
                NormalizedEvent(
                    event_id="pr1",
                    username="octocat",
                    event_type="PullRequestEvent",
                    created_at="2024-01-12T10:00:00Z",
                    repo_name="other/proj",
                    actor_login="octocat",
                    action="opened",
                    is_public=True,
                    raw_json=json.dumps(
                        {"type": "PullRequestEvent", "payload": {"pull_request": {"number": 1}}},
                        sort_keys=True,
                    ),
                    source_page=1,
                    source_rank=2,
                    url="https://github.com/other/proj/pull/1",
                    details="opened PR #1",
                ),
                NormalizedEvent(
                    event_id="push2",
                    username="octocat",
                    event_type="PushEvent",
                    created_at="2024-02-01T10:00:00Z",
                    repo_name="owner/repo",
                    actor_login="octocat",
                    action=None,
                    is_public=True,
                    raw_json="{}",
                    source_page=1,
                    source_rank=3,
                    url=None,
                    details=None,
                ),
            ]
        )

    def test_query_events_by_type(self, storage: Storage) -> None:
        self._seed(storage)
        rows = storage.query_events("octocat", event_types=["PushEvent"])
        assert {r["event_id"] for r in rows} == {"push1", "push2"}

    def test_query_events_by_multiple_types(self, storage: Storage) -> None:
        self._seed(storage)
        rows = storage.query_events("octocat", event_types=["PushEvent", "PullRequestEvent"])
        assert len(rows) == 3

    def test_query_events_by_repo(self, storage: Storage) -> None:
        self._seed(storage)
        rows = storage.query_events("octocat", repo="other/proj")
        assert len(rows) == 1
        assert rows[0]["event_id"] == "pr1"

    def test_query_events_since_filter(self, storage: Storage) -> None:
        self._seed(storage)
        rows = storage.query_events("octocat", since="2024-01-15")
        assert {r["event_id"] for r in rows} == {"push2"}

    def test_query_events_until_filter(self, storage: Storage) -> None:
        self._seed(storage)
        rows = storage.query_events("octocat", until="2024-01-11")
        assert {r["event_id"] for r in rows} == {"push1"}

    def test_query_events_returns_url_and_details(self, storage: Storage) -> None:
        self._seed(storage)
        rows = storage.query_events("octocat", event_types=["PullRequestEvent"])
        assert rows[0]["url"] == "https://github.com/other/proj/pull/1"
        assert rows[0]["details"] == "opened PR #1"

    def test_events_between(self, storage: Storage) -> None:
        self._seed(storage)
        rows = storage.events_between("octocat", since="2024-01-11", until="2024-01-31")
        assert {r["event_id"] for r in rows} == {"pr1"}

    def test_events_between_open_ended(self, storage: Storage) -> None:
        self._seed(storage)
        rows = storage.events_between("octocat", since="2024-01-11")
        assert {r["event_id"] for r in rows} == {"pr1", "push2"}

    def test_get_event_parses_payload(self, storage: Storage) -> None:
        self._seed(storage)
        event = storage.get_event("pr1")
        assert event is not None
        assert event["url"] == "https://github.com/other/proj/pull/1"
        assert isinstance(event["payload"], dict)
        assert event["payload"].get("payload", {}).get("pull_request", {}).get("number") == 1

    def test_get_event_missing_returns_none(self, storage: Storage) -> None:
        assert storage.get_event("does-not-exist") is None

    def test_all_events_includes_url_and_payload(self, storage: Storage) -> None:
        self._seed(storage)
        rows = storage.all_events("octocat")
        assert any("url" in r for r in rows)
        assert any(r.get("payload_json") for r in rows)


class TestListUsers:
    def test_empty_db(self, storage: Storage) -> None:
        assert storage.list_users() == []

    def test_multiple_users(self, storage: Storage) -> None:
        storage.insert_events(
            [
                NormalizedEvent(
                    event_id="a1",
                    username="alice",
                    event_type="PushEvent",
                    created_at="2024-01-15T10:00:00Z",
                    repo_name="x/y",
                    actor_login="alice",
                    action=None,
                    is_public=True,
                    raw_json="{}",
                    source_page=1,
                    source_rank=1,
                ),
            ]
        )
        storage.replace_current_stars(
            "bob",
            [
                StarSnapshot(full_name="b/repo", raw_json="{}"),
            ],
        )
        users = storage.list_users()
        names = {u["username"] for u in users}
        assert names == {"alice", "bob"}
