"""Tests for export functionality."""

from __future__ import annotations

import csv
import io
import json

from github_spy.output import export_data


class TestExportJSON:
    def test_json_format(self) -> None:
        rows = [{"a": 1, "b": "hello"}, {"a": 2, "b": "world"}]
        buf = io.StringIO()
        export_data(rows, "json", buf)
        result = json.loads(buf.getvalue())
        assert len(result) == 2
        assert result[0]["a"] == 1

    def test_empty_json(self) -> None:
        buf = io.StringIO()
        export_data([], "json", buf)
        result = json.loads(buf.getvalue())
        assert result == []


class TestExportJSONL:
    def test_jsonl_format(self) -> None:
        rows = [{"x": 1}, {"x": 2}, {"x": 3}]
        buf = io.StringIO()
        export_data(rows, "jsonl", buf)
        lines = buf.getvalue().strip().split("\n")
        assert len(lines) == 3
        for line in lines:
            parsed = json.loads(line)
            assert "x" in parsed


class TestExportCSV:
    def test_csv_format(self) -> None:
        rows = [
            {"name": "alice", "score": 10},
            {"name": "bob", "score": 20},
        ]
        buf = io.StringIO()
        export_data(rows, "csv", buf)
        buf.seek(0)
        reader = csv.DictReader(buf)
        csv_rows = list(reader)
        assert len(csv_rows) == 2
        assert csv_rows[0]["name"] == "alice"
        assert csv_rows[1]["score"] == "20"

    def test_empty_csv(self) -> None:
        buf = io.StringIO()
        export_data([], "csv", buf)
        assert buf.getvalue() == ""
