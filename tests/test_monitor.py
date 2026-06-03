"""Tests for the snapshot orchestrator, focused on the progress callback wiring.

Collectors are replaced with network-free stubs via monkeypatch so the test
exercises orchestration order, not GitHub's API.
"""

from __future__ import annotations

from typing import Any

import github_spy.monitor as monitor
from github_spy.models import CollectionResult, ProfileSnapshot


class _StubClient:
    """Minimal stand-in: snapshot() reads client.last_rate_limit when summarizing."""

    last_rate_limit = None


def _stub_profile(
    _client: Any, _storage: Any, username: str
) -> tuple[ProfileSnapshot, CollectionResult]:
    return (
        ProfileSnapshot(username=username),
        CollectionResult(username=username, collector_name="profile"),
    )


def _make_stub(name: str):  # noqa: ANN202 - returns a collector-shaped stub
    def _stub(
        _client: Any, _storage: Any, username: str, max_pages: Any = None
    ) -> CollectionResult:
        return CollectionResult(username=username, collector_name=name)

    return _stub


def _patch_all_collectors(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(monitor, "fetch_profile", _stub_profile)
    monkeypatch.setattr(monitor, "fetch_events", _make_stub("events"))
    monkeypatch.setattr(monitor, "fetch_stars", _make_stub("stars"))
    monkeypatch.setattr(monitor, "fetch_followers", _make_stub("followers"))
    monkeypatch.setattr(monitor, "fetch_following", _make_stub("following"))
    monkeypatch.setattr(monitor, "fetch_repos", _make_stub("repos"))


class TestSnapshotProgress:
    def test_on_progress_fires_once_per_collector_in_order(self, monkeypatch) -> None:  # noqa: ANN001
        # Arrange
        _patch_all_collectors(monkeypatch)
        steps: list[str] = []

        # Act
        monitor.snapshot(_StubClient(), None, "octocat", collect=("all",), on_progress=steps.append)

        # Assert
        assert steps == ["profile", "events", "stars", "followers", "following", "repos"]

    def test_on_progress_respects_collector_subset(self, monkeypatch) -> None:  # noqa: ANN001
        # Arrange: profile is always fetched first for context, then only events.
        _patch_all_collectors(monkeypatch)
        steps: list[str] = []

        # Act
        monitor.snapshot(
            _StubClient(), None, "octocat", collect=("events",), on_progress=steps.append
        )

        # Assert
        assert steps == ["profile", "events"]

    def test_snapshot_runs_without_a_callback(self, monkeypatch) -> None:  # noqa: ANN001
        # Arrange
        _patch_all_collectors(monkeypatch)

        # Act
        summary = monitor.snapshot(_StubClient(), None, "octocat")

        # Assert
        assert summary.username == "octocat"
        assert len(summary.results) == 6
