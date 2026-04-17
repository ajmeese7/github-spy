"""Tests for normalization functions and model constructors."""

from __future__ import annotations

from github_spy.collectors.events import normalize_event
from github_spy.collectors.followers import _normalize_user
from github_spy.collectors.repos import normalize_repo
from github_spy.collectors.stars import normalize_star
from tests.conftest import make_raw_event, make_raw_repo, make_raw_star, make_raw_user


class TestNormalizeEvent:
    def test_basic_push_event(self) -> None:
        raw = make_raw_event(event_id="12345", event_type="PushEvent", repo_name="owner/repo")
        result = normalize_event(raw, "testuser", source_page=1, source_rank=1)
        assert result.event_id == "12345"
        assert result.event_type == "PushEvent"
        assert result.repo_name == "owner/repo"
        assert result.username == "testuser"
        assert result.is_public is True
        assert result.source_page == 1

    def test_event_with_action(self) -> None:
        raw = make_raw_event(event_type="IssuesEvent", action="opened")
        result = normalize_event(raw, "testuser", 1, 1)
        assert result.action == "opened"

    def test_missing_repo(self) -> None:
        raw = make_raw_event()
        raw["repo"] = None
        result = normalize_event(raw, "testuser", 1, 1)
        assert result.repo_name is None

    def test_missing_actor(self) -> None:
        raw = make_raw_event()
        raw["actor"] = None
        result = normalize_event(raw, "testuser", 1, 1)
        assert result.actor_login is None

    def test_empty_payload(self) -> None:
        raw = make_raw_event()
        raw["payload"] = None
        result = normalize_event(raw, "testuser", 1, 1)
        assert result.action is None


class TestEventUrlAndDetails:
    """Regression suite for the derived url/details fields on NormalizedEvent.

    These shapes mirror the real GitHub events API, so if GH tweaks a payload
    these tests are the place we find out.
    """

    def test_push_event_compare_url(self) -> None:
        raw = make_raw_event(event_type="PushEvent", repo_name="owner/repo")
        raw["payload"] = {
            "ref": "refs/heads/main",
            "before": "abc123",
            "head": "def456",
            "size": 2,
            "commits": [
                {"sha": "abc999", "message": "first commit"},
                {"sha": "def456", "message": "second commit with a really long message"},
            ],
        }
        result = normalize_event(raw, "testuser", 1, 1)
        assert result.url == "https://github.com/owner/repo/compare/abc123...def456"
        assert "pushed 2 commits" in (result.details or "")
        assert "main" in (result.details or "")
        assert "second commit" in (result.details or "")

    def test_push_event_missing_before_falls_back_to_commit(self) -> None:
        raw = make_raw_event(event_type="PushEvent", repo_name="owner/repo")
        raw["payload"] = {"head": "sha1", "commits": [{"sha": "sha1", "message": "only"}]}
        result = normalize_event(raw, "u", 1, 1)
        assert result.url == "https://github.com/owner/repo/commit/sha1"

    def test_pull_request_event(self) -> None:
        raw = make_raw_event(event_type="PullRequestEvent", action="opened")
        raw["payload"]["pull_request"] = {
            "number": 42,
            "title": "Fix the thing",
            "html_url": "https://github.com/o/r/pull/42",
        }
        result = normalize_event(raw, "u", 1, 1)
        assert result.url == "https://github.com/o/r/pull/42"
        assert "PR #42" in (result.details or "")
        assert "Fix the thing" in (result.details or "")

    def test_issues_event_with_title(self) -> None:
        raw = make_raw_event(event_type="IssuesEvent", action="closed")
        raw["payload"]["issue"] = {
            "number": 7,
            "title": "Bug!",
            "html_url": "https://github.com/o/r/issues/7",
        }
        result = normalize_event(raw, "u", 1, 1)
        assert result.url == "https://github.com/o/r/issues/7"
        assert "closed" in (result.details or "")
        assert "#7" in (result.details or "")

    def test_issue_comment_event_first_line_of_body(self) -> None:
        raw = make_raw_event(event_type="IssueCommentEvent", action="created")
        raw["payload"]["comment"] = {
            "body": "first line\nsecond line ignored",
            "html_url": "https://github.com/o/r/issues/5#issuecomment-1",
        }
        raw["payload"]["issue"] = {"number": 5}
        result = normalize_event(raw, "u", 1, 1)
        assert result.url == "https://github.com/o/r/issues/5#issuecomment-1"
        assert "#5" in (result.details or "")
        assert "first line" in (result.details or "")
        assert "second line" not in (result.details or "")

    def test_watch_event(self) -> None:
        raw = make_raw_event(event_type="WatchEvent", repo_name="owner/repo")
        result = normalize_event(raw, "u", 1, 1)
        assert result.url == "https://github.com/owner/repo"
        assert "starred" in (result.details or "")

    def test_fork_event_uses_forkee_url(self) -> None:
        raw = make_raw_event(event_type="ForkEvent", repo_name="owner/repo")
        raw["payload"]["forkee"] = {
            "full_name": "u/owner-repo",
            "html_url": "https://github.com/u/owner-repo",
        }
        result = normalize_event(raw, "u", 1, 1)
        assert result.url == "https://github.com/u/owner-repo"
        assert "u/owner-repo" in (result.details or "")

    def test_create_branch_event(self) -> None:
        raw = make_raw_event(event_type="CreateEvent", repo_name="owner/repo")
        raw["payload"] = {"ref_type": "branch", "ref": "feature-x"}
        result = normalize_event(raw, "u", 1, 1)
        assert result.url == "https://github.com/owner/repo/tree/feature-x"
        assert "feature-x" in (result.details or "")

    def test_create_repository_event(self) -> None:
        raw = make_raw_event(event_type="CreateEvent", repo_name="owner/repo")
        raw["payload"] = {"ref_type": "repository", "ref": None}
        result = normalize_event(raw, "u", 1, 1)
        assert result.url == "https://github.com/owner/repo"
        assert "repository" in (result.details or "")

    def test_delete_event(self) -> None:
        raw = make_raw_event(event_type="DeleteEvent", repo_name="owner/repo")
        raw["payload"] = {"ref_type": "branch", "ref": "stale"}
        result = normalize_event(raw, "u", 1, 1)
        assert "deleted" in (result.details or "")
        assert "stale" in (result.details or "")

    def test_release_event(self) -> None:
        raw = make_raw_event(event_type="ReleaseEvent", action="published")
        raw["payload"]["release"] = {
            "tag_name": "v1.2.3",
            "html_url": "https://github.com/o/r/releases/tag/v1.2.3",
        }
        result = normalize_event(raw, "u", 1, 1)
        assert result.url == "https://github.com/o/r/releases/tag/v1.2.3"
        assert "v1.2.3" in (result.details or "")

    def test_unknown_event_type_falls_back_to_repo_url(self) -> None:
        raw = make_raw_event(event_type="SomeFutureEvent", repo_name="owner/repo")
        result = normalize_event(raw, "u", 1, 1)
        assert result.url == "https://github.com/owner/repo"
        assert result.details == "SomeFutureEvent"

    def test_missing_repo_no_crash(self) -> None:
        raw = make_raw_event(event_type="PushEvent")
        raw["repo"] = None
        raw["payload"] = {"commits": [], "size": 0}
        result = normalize_event(raw, "u", 1, 1)
        assert result.url is None
        assert result.details is not None

    def test_push_event_public_api_shape_no_commits_array(self) -> None:
        """The /users/:user/events/public endpoint trims the commits array.
        Real payload has only before/head/push_id/ref/repository_id — no commits
        or message. The derivation must still produce a useful compare URL and
        not emit "pushed 0 commits"."""
        raw = make_raw_event(event_type="PushEvent", repo_name="owner/repo")
        raw["payload"] = {
            "before": "abc123",
            "head": "def456",
            "push_id": 1,
            "ref": "refs/heads/master",
            "repository_id": 42,
        }
        result = normalize_event(raw, "u", 1, 1)
        assert result.url == "https://github.com/owner/repo/compare/abc123...def456"
        details = result.details or ""
        assert "pushed" in details
        assert "master" in details
        assert "0 commits" not in details

    def test_pull_request_event_public_api_shape_no_html_url(self) -> None:
        """The /users/:user/events/public endpoint returns only api `url`,
        `number`, `base`, `head` on pull_request — no html_url, no title.
        The derivation must construct the web URL from repo + number."""
        raw = make_raw_event(event_type="PullRequestEvent", action="opened", repo_name="o/r")
        raw["payload"] = {
            "action": "opened",
            "number": 42,
            "pull_request": {
                "number": 42,
                "url": "https://api.github.com/repos/o/r/pulls/42",
                "id": 1,
            },
        }
        result = normalize_event(raw, "u", 1, 1)
        assert result.url == "https://github.com/o/r/pull/42"
        assert "opened" in (result.details or "")
        assert "PR #42" in (result.details or "")


class TestNormalizeStar:
    def test_basic_star(self) -> None:
        raw = make_raw_star(full_name="owner/repo", language="Rust", stargazers_count=42)
        result = normalize_star(raw)
        assert result is not None
        assert result.full_name == "owner/repo"
        assert result.language == "Rust"
        assert result.stargazers_count == 42

    def test_missing_full_name_falls_back_to_constructed(self) -> None:
        raw = make_raw_star()
        del raw["repo"]["full_name"]
        result = normalize_star(raw)
        assert result is not None
        assert "/" in result.full_name

    def test_completely_empty_repo(self) -> None:
        raw = {"starred_at": "2024-01-01T00:00:00Z", "repo": {}}
        result = normalize_star(raw)
        assert result is None

    def test_star_timestamps_preserved(self) -> None:
        raw = make_raw_star(starred_at="2024-06-15T12:00:00Z")
        result = normalize_star(raw)
        assert result is not None
        assert result.starred_at == "2024-06-15T12:00:00Z"


class TestNormalizeUser:
    def test_basic_user(self) -> None:
        raw = make_raw_user(login="testfan", user_id=42)
        result = _normalize_user(raw)
        assert result.login == "testfan"
        assert result.user_id == 42

    def test_missing_fields(self) -> None:
        result = _normalize_user({})
        assert result.login == ""
        assert result.user_id is None


class TestNormalizeRepo:
    def test_basic_repo(self) -> None:
        raw = make_raw_repo(full_name="owner/project", language="Go")
        result = normalize_repo(raw)
        assert result is not None
        assert result.full_name == "owner/project"
        assert result.language == "Go"

    def test_missing_full_name(self) -> None:
        raw = make_raw_repo()
        del raw["full_name"]
        result = normalize_repo(raw)
        assert result is None

    def test_fork_flag(self) -> None:
        raw = make_raw_repo(fork=True)
        result = normalize_repo(raw)
        assert result is not None
        assert result.is_fork is True
