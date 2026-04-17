"""CLI integration tests using click's CliRunner."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from github_spy.cli import app
from github_spy.models import NormalizedEvent, StarSnapshot
from github_spy.storage import Storage


def _seed_db(db_path: Path) -> None:
    """Populate a test database with sample data."""
    with Storage(db_path) as storage:
        storage.insert_events(
            [
                NormalizedEvent(
                    event_id=f"ev{i}",
                    username="testuser",
                    event_type="PushEvent" if i % 2 == 0 else "WatchEvent",
                    created_at=f"2024-01-{15 + i}T10:00:00Z",
                    repo_name="test/repo",
                    actor_login="testuser",
                    action=None,
                    is_public=True,
                    raw_json="{}",
                    source_page=1,
                    source_rank=i,
                )
                for i in range(5)
            ]
        )
        storage.replace_current_stars(
            "testuser",
            [
                StarSnapshot(full_name="lang/python", language="Python", raw_json="{}"),
                StarSnapshot(full_name="lang/rust", language="Rust", raw_json="{}"),
            ],
        )


class TestUsersCommand:
    def test_empty_db(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        runner = CliRunner()
        result = runner.invoke(app, ["--db", str(db), "users"])
        assert result.exit_code == 0
        assert "No users tracked" in result.output

    def test_with_data(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        _seed_db(db)
        runner = CliRunner()
        result = runner.invoke(app, ["--db", str(db), "users"])
        assert result.exit_code == 0
        assert "testuser" in result.output

    def test_json_output(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        _seed_db(db)
        runner = CliRunner()
        result = runner.invoke(app, ["--db", str(db), "--json", "users"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["username"] == "testuser"


class TestInspectCommand:
    def test_inspect_events(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        _seed_db(db)
        runner = CliRunner()
        result = runner.invoke(app, ["--db", str(db), "inspect", "testuser", "--type", "events"])
        assert result.exit_code == 0
        assert "PushEvent" in result.output

    def test_inspect_json(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        _seed_db(db)
        runner = CliRunner()
        result = runner.invoke(
            app, ["--db", str(db), "--json", "inspect", "testuser", "--type", "events"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["username"] == "testuser"
        assert len(data["rows"]) > 0

    def test_inspect_no_data(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        Storage(db).close()
        runner = CliRunner()
        result = runner.invoke(app, ["--db", str(db), "inspect", "nobody", "--type", "events"])
        assert result.exit_code == 0
        assert "No events data" in result.output


class TestStatsCommand:
    def test_stats_with_data(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        _seed_db(db)
        runner = CliRunner()
        result = runner.invoke(app, ["--db", str(db), "stats", "testuser"])
        assert result.exit_code == 0
        assert "Event Types" in result.output

    def test_stats_json(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        _seed_db(db)
        runner = CliRunner()
        result = runner.invoke(app, ["--db", str(db), "--json", "stats", "testuser"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "event_counts" in data
        assert "language_distribution" in data


class TestExportCommand:
    def test_export_json(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        _seed_db(db)
        runner = CliRunner()
        result = runner.invoke(
            app, ["--db", str(db), "export", "testuser", "--format", "json", "--type", "events"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 5

    def test_export_csv(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        _seed_db(db)
        runner = CliRunner()
        result = runner.invoke(
            app, ["--db", str(db), "export", "testuser", "--format", "csv", "--type", "events"]
        )
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        assert len(lines) == 6  # header + 5 rows
        assert "event_id" in lines[0]

    def test_export_csv_includes_url_details_payload(self, tmp_path: Path) -> None:
        """CSV exports must expose url, details, and payload_json so events are
        actually actionable (can see the PR URL, commit SHA, etc.)."""
        db = tmp_path / "test.db"
        _seed_db(db)
        runner = CliRunner()
        result = runner.invoke(
            app, ["--db", str(db), "export", "testuser", "--format", "csv", "--type", "events"]
        )
        assert result.exit_code == 0
        header = result.output.strip().split("\n")[0]
        assert "url" in header
        assert "details" in header
        assert "payload_json" in header

    def test_export_to_file(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        _seed_db(db)
        out_file = tmp_path / "export.json"
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "--db",
                str(db),
                "export",
                "testuser",
                "--format",
                "json",
                "--type",
                "events",
                "--output",
                str(out_file),
            ],
        )
        assert result.exit_code == 0
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert len(data) == 5


class TestDiffCommand:
    def test_diff_no_data(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        Storage(db).close()
        runner = CliRunner()
        result = runner.invoke(app, ["--db", str(db), "diff", "nobody", "--since", "2024-01-01"])
        assert result.exit_code == 0
        assert "No changes" in result.output

    def test_diff_includes_events_section(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        with Storage(db) as storage:
            storage.insert_events(
                [
                    NormalizedEvent(
                        event_id="e1",
                        username="testuser",
                        event_type="PullRequestEvent",
                        created_at="2024-02-01T10:00:00Z",
                        repo_name="o/r",
                        actor_login="testuser",
                        action="opened",
                        is_public=True,
                        raw_json="{}",
                        source_page=1,
                        source_rank=1,
                        url="https://github.com/o/r/pull/1",
                        details="opened PR #1",
                    )
                ]
            )
        runner = CliRunner()
        result = runner.invoke(app, ["--db", str(db), "diff", "testuser", "--since", "2024-01-01"])
        assert result.exit_code == 0
        assert "Events" in result.output
        assert "PullRequestEvent" in result.output


class TestShowCommand:
    def test_show_existing_event(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        _seed_db(db)
        runner = CliRunner()
        result = runner.invoke(app, ["--db", str(db), "show", "ev0"])
        assert result.exit_code == 0
        assert "ev0" in result.output

    def test_show_missing_event_exits_nonzero(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        Storage(db).close()
        runner = CliRunner()
        result = runner.invoke(app, ["--db", str(db), "show", "bogus-id"])
        assert result.exit_code == 1
        assert "bogus-id" in result.output

    def test_show_json_output(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        _seed_db(db)
        runner = CliRunner()
        result = runner.invoke(app, ["--db", str(db), "--json", "show", "ev0"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["event_id"] == "ev0"


class TestInspectFilters:
    def _seed_mixed(self, db: Path) -> None:
        with Storage(db) as storage:
            storage.insert_events(
                [
                    NormalizedEvent(
                        event_id="p1",
                        username="testuser",
                        event_type="PushEvent",
                        created_at="2024-01-10T10:00:00Z",
                        repo_name="a/one",
                        actor_login="testuser",
                        action=None,
                        is_public=True,
                        raw_json="{}",
                        source_page=1,
                        source_rank=1,
                    ),
                    NormalizedEvent(
                        event_id="pr1",
                        username="testuser",
                        event_type="PullRequestEvent",
                        created_at="2024-02-15T10:00:00Z",
                        repo_name="b/two",
                        actor_login="testuser",
                        action="opened",
                        is_public=True,
                        raw_json="{}",
                        source_page=1,
                        source_rank=2,
                    ),
                ]
            )

    def test_inspect_filter_by_event_type(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        self._seed_mixed(db)
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "--db",
                str(db),
                "--json",
                "inspect",
                "testuser",
                "--type",
                "events",
                "--event-type",
                "PullRequestEvent",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["rows"]) == 1
        assert data["rows"][0]["event_id"] == "pr1"

    def test_inspect_filter_by_repo(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        self._seed_mixed(db)
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "--db",
                str(db),
                "--json",
                "inspect",
                "testuser",
                "--type",
                "events",
                "--repo",
                "a/one",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert {r["event_id"] for r in data["rows"]} == {"p1"}

    def test_inspect_filter_since(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        self._seed_mixed(db)
        runner = CliRunner()
        result = runner.invoke(
            app,
            [
                "--db",
                str(db),
                "--json",
                "inspect",
                "testuser",
                "--type",
                "events",
                "--since",
                "2024-02-01",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert {r["event_id"] for r in data["rows"]} == {"pr1"}


class TestVersionFlag:
    def test_version(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output
