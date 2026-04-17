"""Shared test fixtures for github-spy."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from github_spy.storage import Storage


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture()
def storage(db_path: Path) -> Iterator[Storage]:
    with Storage(db_path) as s:
        yield s


# ---------------------------------------------------------------------------
# Sample data factories
# ---------------------------------------------------------------------------


def make_raw_event(
    event_id: str = "123456",
    event_type: str = "PushEvent",
    created_at: str = "2024-01-15T10:00:00Z",
    repo_name: str = "octocat/hello-world",
    actor_login: str = "octocat",
    action: str | None = None,
) -> dict:
    raw = {
        "id": event_id,
        "type": event_type,
        "created_at": created_at,
        "repo": {"name": repo_name},
        "actor": {"login": actor_login},
        "public": True,
        "payload": {},
    }
    if action:
        raw["payload"]["action"] = action
    return raw


def make_raw_star(
    full_name: str = "octocat/hello-world",
    repo_id: int = 1,
    language: str | None = "Python",
    starred_at: str = "2024-01-15T10:00:00Z",
    stargazers_count: int = 100,
) -> dict:
    return {
        "starred_at": starred_at,
        "repo": {
            "id": repo_id,
            "full_name": full_name,
            "html_url": f"https://github.com/{full_name}",
            "private": False,
            "fork": False,
            "language": language,
            "stargazers_count": stargazers_count,
            "owner": {"login": full_name.split("/")[0]},
            "name": full_name.split("/")[1],
        },
    }


def make_raw_user(
    login: str = "octocat",
    user_id: int = 1,
) -> dict:
    return {
        "login": login,
        "id": user_id,
        "html_url": f"https://github.com/{login}",
    }


def make_raw_repo(
    full_name: str = "octocat/hello-world",
    repo_id: int = 1,
    language: str | None = "Python",
    description: str | None = "A test repo",
    fork: bool = False,
    stargazers_count: int = 42,
) -> dict:
    return {
        "id": repo_id,
        "full_name": full_name,
        "html_url": f"https://github.com/{full_name}",
        "language": language,
        "description": description,
        "fork": fork,
        "stargazers_count": stargazers_count,
        "owner": {"login": full_name.split("/")[0]},
        "name": full_name.split("/")[1],
    }


def make_raw_profile(
    login: str = "octocat",
    name: str = "The Octocat",
    bio: str | None = "GitHub mascot",
    company: str | None = "@github",
    location: str | None = "San Francisco",
    followers: int = 1000,
    following: int = 10,
    public_repos: int = 50,
) -> dict:
    return {
        "login": login,
        "name": name,
        "bio": bio,
        "company": company,
        "location": location,
        "blog": "https://github.blog",
        "public_repos": public_repos,
        "public_gists": 5,
        "followers": followers,
        "following": following,
        "html_url": f"https://github.com/{login}",
        "created_at": "2011-01-25T18:44:36Z",
        "updated_at": "2024-01-15T10:00:00Z",
    }
