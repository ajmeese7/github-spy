"""Tests for terminal output helpers that must stay quiet when piped."""

from __future__ import annotations

import pytest

from github_spy.output import collection_progress


class TestCollectionProgress:
    def test_no_output_when_stderr_is_not_a_tty(self, capsys: pytest.CaptureFixture[str]) -> None:
        # Arrange / Act: under pytest, stdout and stderr are captured (not TTYs),
        # which is the same situation as piping output to a file.
        with collection_progress("octocat") as update:
            update("events")
            update("stars")

        # Assert: nothing leaks to either stream, so a piped snapshot stays clean.
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_yields_a_callable_updater(self) -> None:
        # Act / Assert: callers always get something callable, TTY or not.
        with collection_progress("octocat") as update:
            assert callable(update)
            update("repos")  # must not raise
