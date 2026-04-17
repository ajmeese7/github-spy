"""Orchestrator that coordinates data collection across all collectors."""

from __future__ import annotations

import logging
import signal
import time
from typing import TYPE_CHECKING, Any

from github_spy.collectors.events import fetch_events
from github_spy.collectors.followers import fetch_followers, fetch_following
from github_spy.collectors.profile import fetch_profile
from github_spy.collectors.repos import fetch_repos
from github_spy.collectors.stars import fetch_stars
from github_spy.models import CollectionResult, SnapshotSummary
from github_spy.storage import Storage, utc_now_iso

if TYPE_CHECKING:
    from github_spy.client import GitHubClient

log = logging.getLogger(__name__)

ALL_COLLECTORS = ("events", "stars", "followers", "following", "repos", "profile")


def _resolve_collectors(collect: tuple[str, ...]) -> tuple[str, ...]:
    """Expand 'all' into the full list of collector names."""
    if "all" in collect:
        return ALL_COLLECTORS
    return collect


def snapshot(
    client: GitHubClient,
    storage: Storage,
    username: str,
    collect: tuple[str, ...] = ("all",),
    events_pages: int = 3,
    stars_pages: int = 10,
    followers_pages: int = 10,
    repos_pages: int = 10,
) -> SnapshotSummary:
    """Run a single collection pass for one user."""
    collectors = _resolve_collectors(collect)
    results: list[CollectionResult] = []

    # Profile is always fetched (needed for context)
    profile_snap, profile_result = fetch_profile(client, storage, username)
    if "profile" in collectors:
        results.append(profile_result)

    if "events" in collectors:
        results.append(fetch_events(client, storage, username, max_pages=events_pages))

    if "stars" in collectors:
        results.append(fetch_stars(client, storage, username, max_pages=stars_pages))

    if "followers" in collectors:
        results.append(fetch_followers(client, storage, username, max_pages=followers_pages))

    if "following" in collectors:
        results.append(fetch_following(client, storage, username, max_pages=followers_pages))

    if "repos" in collectors:
        results.append(fetch_repos(client, storage, username, max_pages=repos_pages))

    return SnapshotSummary(
        ran_at=utc_now_iso(),
        username=username,
        profile=profile_snap,
        results=tuple(results),
        rate_limit=client.last_rate_limit,
    )


class GracefulTerminator:
    """Signal handler for clean shutdown in watch mode."""

    def __init__(self) -> None:
        self.stop = False
        signal.signal(signal.SIGINT, self._handle)
        signal.signal(signal.SIGTERM, self._handle)

    def _handle(self, signum: int, _frame: Any) -> None:
        self.stop = True


def watch(
    client: GitHubClient,
    storage: Storage,
    usernames: list[str],
    interval: int = 900,
    collect: tuple[str, ...] = ("all",),
    events_pages: int = 3,
    stars_pages: int = 10,
    followers_pages: int = 10,
    repos_pages: int = 10,
    on_snapshot: Any = None,
) -> None:
    """Continuously poll for all specified users on an interval.

    on_snapshot: optional callback(SnapshotSummary) called after each user's snapshot.
    """
    terminator = GracefulTerminator()

    while not terminator.stop:
        for username in usernames:
            if terminator.stop:
                break
            try:
                summary = snapshot(
                    client,
                    storage,
                    username,
                    collect=collect,
                    events_pages=events_pages,
                    stars_pages=stars_pages,
                    followers_pages=followers_pages,
                    repos_pages=repos_pages,
                )
                if on_snapshot:
                    on_snapshot(summary)
            except Exception:
                log.exception("Error collecting data for %s", username)

        # Rate-limit-aware sleep: if API quota is low, extend the interval
        effective_interval = interval
        rl = client.last_rate_limit
        if rl and rl.remaining < 100 and rl.remaining > 0:
            # Double the interval when running low on quota
            effective_interval = min(interval * 2, 3600)
            log.warning(
                "API quota low (%d remaining), extending interval to %ds",
                rl.remaining,
                effective_interval,
            )

        for _ in range(effective_interval):
            if terminator.stop:
                break
            time.sleep(1)
