"""Domain models for github-spy. All data types are frozen dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class EventType(StrEnum):
    COMMIT_COMMENT = "CommitCommentEvent"
    CREATE = "CreateEvent"
    DELETE = "DeleteEvent"
    FORK = "ForkEvent"
    GOLLUM = "GollumEvent"
    ISSUE_COMMENT = "IssueCommentEvent"
    ISSUES = "IssuesEvent"
    MEMBER = "MemberEvent"
    PUBLIC = "PublicEvent"
    PULL_REQUEST = "PullRequestEvent"
    PULL_REQUEST_REVIEW = "PullRequestReviewEvent"
    PULL_REQUEST_REVIEW_COMMENT = "PullRequestReviewCommentEvent"
    PULL_REQUEST_REVIEW_THREAD = "PullRequestReviewThreadEvent"
    PUSH = "PushEvent"
    RELEASE = "ReleaseEvent"
    SPONSORSHIP = "SponsorshipEvent"
    WATCH = "WatchEvent"

    @classmethod
    def _missing_(cls, value: object) -> EventType:
        """Return the raw string wrapped as-is so unknown event types don't crash."""
        obj = str.__new__(cls, str(value))
        obj._name_ = str(value)
        obj._value_ = str(value)
        return obj


class StarChangeKind(StrEnum):
    ADDED = "star_added"
    REMOVED = "star_removed"


class FollowerChangeKind(StrEnum):
    FOLLOWED = "followed"
    UNFOLLOWED = "unfollowed"


class RepoChangeKind(StrEnum):
    CREATED = "created"
    DELETED = "deleted"


# ---------------------------------------------------------------------------
# HTTP / caching
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CacheEntry:
    etag: str | None = None
    last_modified: str | None = None


@dataclass(frozen=True)
class RateLimitInfo:
    limit: int = 0
    remaining: int = 0
    reset_at: datetime | None = None
    used: int = 0


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NormalizedEvent:
    event_id: str
    username: str
    event_type: str
    created_at: str | None
    repo_name: str | None
    actor_login: str | None
    action: str | None
    is_public: bool
    raw_json: str
    source_page: int
    source_rank: int
    url: str | None = None
    details: str | None = None


# ---------------------------------------------------------------------------
# Stars
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StarSnapshot:
    full_name: str
    repo_id: int | None = None
    html_url: str | None = None
    is_private: bool = False
    is_fork: bool = False
    language: str | None = None
    stargazers_count: int = 0
    starred_at: str | None = None
    raw_json: str = ""


@dataclass(frozen=True)
class StarChange:
    username: str
    full_name: str
    kind: StarChangeKind
    detected_at: str
    repo_id: int | None = None
    html_url: str | None = None
    starred_at: str | None = None
    language: str | None = None


# ---------------------------------------------------------------------------
# Followers / following
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FollowerSnapshot:
    login: str
    user_id: int | None = None
    html_url: str | None = None


@dataclass(frozen=True)
class FollowerChange:
    username: str
    login: str
    kind: FollowerChangeKind
    detected_at: str
    user_id: int | None = None


# ---------------------------------------------------------------------------
# Repos
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RepoSnapshot:
    full_name: str
    repo_id: int | None = None
    html_url: str | None = None
    language: str | None = None
    description: str | None = None
    is_fork: bool = False
    stargazers_count: int = 0
    raw_json: str = ""


@dataclass(frozen=True)
class RepoChange:
    username: str
    repo_name: str
    kind: RepoChangeKind
    detected_at: str
    language: str | None = None
    repo_id: int | None = None


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProfileSnapshot:
    username: str
    name: str | None = None
    bio: str | None = None
    company: str | None = None
    location: str | None = None
    blog: str | None = None
    public_repos: int = 0
    public_gists: int = 0
    followers: int = 0
    following: int = 0
    html_url: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    fetched_at: str = ""
    raw_json: str = ""


@dataclass(frozen=True)
class ProfileFieldChange:
    username: str
    field_name: str
    old_value: str | None
    new_value: str | None
    detected_at: str


# ---------------------------------------------------------------------------
# Collection results
# ---------------------------------------------------------------------------

TRACKED_PROFILE_FIELDS = (
    "name",
    "bio",
    "company",
    "location",
    "blog",
    "public_repos",
    "public_gists",
    "followers",
    "following",
)


@dataclass(frozen=True)
class CollectionResult:
    """Summary returned by a single collector run."""

    username: str
    collector_name: str
    changed: bool = False
    new_count: int = 0
    change_count: int = 0
    details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SnapshotSummary:
    """Aggregate result of a full snapshot across all collectors."""

    ran_at: str
    username: str
    profile: ProfileSnapshot | None = None
    results: tuple[CollectionResult, ...] = ()
    rate_limit: RateLimitInfo | None = None
