"""Tests for GitHubClient.paginate, specifically its termination behaviour.

The collector truncation guard relies on paginate(max_pages=None) looping
until GitHub returns a short/empty page, so that behaviour is exercised here.
"""

from __future__ import annotations

from typing import Any

from github_spy.client import GitHubClient


class _StubClient(GitHubClient):
    """GitHubClient with get_json swapped out for a canned page sequence."""

    def __init__(self, pages: list[list[dict[str, Any]]]) -> None:
        super().__init__(token=None)
        self._pages = pages
        self._calls = 0

    def get_json(self, _path: str, **_kw: Any):  # type: ignore[override]
        idx = self._calls
        self._calls += 1
        if idx >= len(self._pages):
            return 200, [], {}
        return 200, self._pages[idx], {}


class TestPaginateExhaustion:
    def test_stops_on_short_page_with_unlimited_max(self) -> None:
        client = _StubClient(pages=[[{"id": i} for i in range(100)], [{"id": 999}]])
        try:
            results = list(client.paginate("/x", max_pages=None, per_page=100))
        finally:
            client.close()
        # Two pages fetched: a full one (100) then a short one (1).
        assert len(results) == 2
        assert [p[2] for p in results] == [1, 2]

    def test_stops_at_max_pages_when_set(self) -> None:
        client = _StubClient(pages=[[{"id": i} for i in range(100)]] * 5)
        try:
            results = list(client.paginate("/x", max_pages=3, per_page=100))
        finally:
            client.close()
        # Cap honoured even though upstream has more full pages.
        assert len(results) == 3

    def test_empty_first_page_terminates(self) -> None:
        client = _StubClient(pages=[[]])
        try:
            results = list(client.paginate("/x", max_pages=None, per_page=100))
        finally:
            client.close()
        assert len(results) == 1
        assert results[0][1] == []
