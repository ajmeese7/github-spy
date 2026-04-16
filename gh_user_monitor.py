#!/usr/bin/env python3
"""
gh_user_monitor.py

Monitor a GitHub user's public activity and starred repositories.

Features:
- Polls public user events
- Polls current starred repositories with `starred_at` timestamps when available
- Stores durable local state in SQLite
- Uses conditional requests with ETag / Last-Modified caching
- Supports one-shot snapshots and continuous polling
- Supports `.env` loading for GH_TOKEN
- Supports local inspection without calling GitHub

Examples:
    python gh_user_monitor.py snapshot octocat
    python gh_user_monitor.py snapshot octocat --mode all --state-dir ./gh_state
    python gh_user_monitor.py watch octocat --interval 900 --token-env GH_TOKEN
    python gh_user_monitor.py inspect octocat --limit 20
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


API_BASE = "https://api.github.com"
API_VERSION = "2026-03-10"
DEFAULT_USER_AGENT = "gh-user-monitor/2.0"
DEFAULT_STATE_DIR = "./gh_monitor_state"
DEFAULT_DB_NAME = "monitor.db"
DEFAULT_ENV_FILE = ".env"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def load_dotenv(env_path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not env_path.exists():
        return values

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


class StopRequested(Exception):
    pass


@dataclass(frozen=True)
class CacheEntry:
    etag: Optional[str]
    last_modified: Optional[str]


class GitHubClient:
    def __init__(
        self,
        token: Optional[str] = None,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: int = 30,
    ) -> None:
        self.token = token
        self.user_agent = user_agent
        self.timeout = timeout

    def _headers(self, accept: str) -> Dict[str, str]:
        headers = {
            "Accept": accept,
            "User-Agent": self.user_agent,
            "X-GitHub-Api-Version": API_VERSION,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def get_json(
        self,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        accept: str = "application/vnd.github+json",
        cache_entry: Optional[CacheEntry] = None,
    ) -> Tuple[int, Optional[Any], Dict[str, str]]:
        url = f"{API_BASE}{path}"
        if params:
            query = urllib.parse.urlencode(params)
            url = f"{url}?{query}"

        headers = self._headers(accept)
        if cache_entry:
            if cache_entry.etag:
                headers["If-None-Match"] = cache_entry.etag
            if cache_entry.last_modified:
                headers["If-Modified-Since"] = cache_entry.last_modified

        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
                data = json.loads(body) if body else None
                response_headers = {k.lower(): v for k, v in response.headers.items()}
                return response.status, data, response_headers
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                response_headers = {k.lower(): v for k, v in exc.headers.items()}
                return 304, None, response_headers
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitHub API error {exc.code} for {url}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Network error for {url}: {exc}") from exc


class Storage:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        ensure_dir(db_path.parent)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS api_cache (
                cache_key TEXT PRIMARY KEY,
                etag TEXT,
                last_modified TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS profiles (
                username TEXT PRIMARY KEY,
                profile_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                event_type TEXT,
                created_at TEXT,
                repo_name TEXT,
                actor_login TEXT,
                action TEXT,
                public INTEGER,
                payload_json TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                source_page INTEGER,
                source_rank INTEGER
            );

            CREATE INDEX IF NOT EXISTS idx_events_username_created_at
                ON events (username, created_at DESC);

            CREATE INDEX IF NOT EXISTS idx_events_username_type_created_at
                ON events (username, event_type, created_at DESC);

            CREATE INDEX IF NOT EXISTS idx_events_username_repo_created_at
                ON events (username, repo_name, created_at DESC);

            CREATE TABLE IF NOT EXISTS stars_current (
                username TEXT NOT NULL,
                full_name TEXT NOT NULL,
                repo_id INTEGER,
                html_url TEXT,
                private INTEGER,
                fork INTEGER,
                language TEXT,
                stargazers_count INTEGER,
                watchers_count INTEGER,
                starred_at TEXT,
                snapshot_at TEXT NOT NULL,
                repo_json TEXT NOT NULL,
                PRIMARY KEY (username, full_name)
            );

            CREATE INDEX IF NOT EXISTS idx_stars_current_username_starred_at
                ON stars_current (username, starred_at DESC);

            CREATE TABLE IF NOT EXISTS star_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                full_name TEXT NOT NULL,
                repo_id INTEGER,
                html_url TEXT,
                event_kind TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                starred_at TEXT,
                details_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_star_history_username_detected_at
                ON star_history (username, detected_at DESC);

            CREATE INDEX IF NOT EXISTS idx_star_history_username_repo_detected_at
                ON star_history (username, full_name, detected_at DESC);
            """
        )
        self.conn.commit()

    def set_metadata(self, key: str, value: str) -> None:
        self.conn.execute(
            """
            INSERT INTO metadata (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        self.conn.commit()

    def get_metadata(self, key: str) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def get_cache_entry(self, cache_key: str) -> CacheEntry:
        row = self.conn.execute(
            "SELECT etag, last_modified FROM api_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if row is None:
            return CacheEntry(etag=None, last_modified=None)
        return CacheEntry(etag=row["etag"], last_modified=row["last_modified"])

    def upsert_cache_entry(self, cache_key: str, headers: Dict[str, str]) -> None:
        self.conn.execute(
            """
            INSERT INTO api_cache (cache_key, etag, last_modified, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                etag = excluded.etag,
                last_modified = excluded.last_modified,
                updated_at = excluded.updated_at
            """,
            (
                cache_key,
                headers.get("etag"),
                headers.get("last-modified"),
                utc_now_iso(),
            ),
        )
        self.conn.commit()

    def save_profile(self, username: str, profile: Dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO profiles (username, profile_json, fetched_at)
            VALUES (?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                profile_json = excluded.profile_json,
                fetched_at = excluded.fetched_at
            """,
            (username, json.dumps(profile, sort_keys=True), utc_now_iso()),
        )
        self.conn.commit()

    def insert_events(self, username: str, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        inserted: List[Dict[str, Any]] = []
        now = utc_now_iso()
        cursor = self.conn.cursor()
        for event in events:
            payload = event.get("raw", event)
            cursor.execute(
                """
                INSERT OR IGNORE INTO events (
                    event_id,
                    username,
                    event_type,
                    created_at,
                    repo_name,
                    actor_login,
                    action,
                    public,
                    payload_json,
                    first_seen_at,
                    source_page,
                    source_rank
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event.get("event_id")),
                    username,
                    event.get("type"),
                    event.get("created_at"),
                    event.get("repo"),
                    event.get("actor"),
                    event.get("action"),
                    1 if event.get("public") else 0,
                    json.dumps(payload, sort_keys=True),
                    now,
                    event.get("source_page"),
                    event.get("source_rank"),
                ),
            )
            if cursor.rowcount == 1:
                inserted.append(event)
        self.conn.commit()
        return inserted

    def get_current_stars(self, username: str) -> Dict[str, Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT full_name, repo_id, html_url, private, fork, language,
                   stargazers_count, watchers_count, starred_at, snapshot_at, repo_json
            FROM stars_current
            WHERE username = ?
            """,
            (username,),
        ).fetchall()
        result: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            result[str(row["full_name"])] = {
                "repo_id": row["repo_id"],
                "html_url": row["html_url"],
                "private": bool(row["private"]) if row["private"] is not None else None,
                "fork": bool(row["fork"]) if row["fork"] is not None else None,
                "language": row["language"],
                "stargazers_count": row["stargazers_count"],
                "watchers_count": row["watchers_count"],
                "starred_at": row["starred_at"],
                "snapshot_at": row["snapshot_at"],
                "raw": json.loads(row["repo_json"]),
            }
        return result

    def replace_current_stars(self, username: str, stars: List[Dict[str, Any]]) -> None:
        now = utc_now_iso()
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM stars_current WHERE username = ?", (username,))
        for star in stars:
            cursor.execute(
                """
                INSERT INTO stars_current (
                    username,
                    full_name,
                    repo_id,
                    html_url,
                    private,
                    fork,
                    language,
                    stargazers_count,
                    watchers_count,
                    starred_at,
                    snapshot_at,
                    repo_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    username,
                    star.get("full_name"),
                    star.get("repo_id"),
                    star.get("html_url"),
                    1 if star.get("private") else 0,
                    1 if star.get("fork") else 0,
                    star.get("language"),
                    star.get("stargazers_count"),
                    star.get("watchers_count"),
                    star.get("starred_at"),
                    now,
                    json.dumps(star.get("raw", star), sort_keys=True),
                ),
            )
        self.conn.commit()

    def insert_star_history(self, rows: List[Dict[str, Any]]) -> None:
        if not rows:
            return
        self.conn.executemany(
            """
            INSERT INTO star_history (
                username,
                full_name,
                repo_id,
                html_url,
                event_kind,
                detected_at,
                starred_at,
                details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["username"],
                    row["full_name"],
                    row.get("repo_id"),
                    row.get("html_url"),
                    row["event_kind"],
                    row["detected_at"],
                    row.get("starred_at"),
                    json.dumps(row, sort_keys=True),
                )
                for row in rows
            ],
        )
        self.conn.commit()

    def recent_events(self, username: str, limit: int = 10) -> List[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT event_id, event_type, created_at, repo_name, action, first_seen_at
            FROM events
            WHERE username = ?
            ORDER BY created_at DESC, first_seen_at DESC
            LIMIT ?
            """,
            (username, limit),
        ).fetchall()

    def recent_star_history(self, username: str, limit: int = 10) -> List[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT full_name, event_kind, starred_at, detected_at
            FROM star_history
            WHERE username = ?
            ORDER BY detected_at DESC, id DESC
            LIMIT ?
            """,
            (username, limit),
        ).fetchall()


def normalize_event(event: Dict[str, Any], source_page: int, source_rank: int) -> Dict[str, Any]:
    payload = event.get("payload", {}) or {}
    return {
        "event_id": str(event.get("id")),
        "type": event.get("type"),
        "created_at": event.get("created_at"),
        "repo": (event.get("repo") or {}).get("name"),
        "public": event.get("public"),
        "actor": (event.get("actor") or {}).get("login"),
        "action": payload.get("action"),
        "source_page": source_page,
        "source_rank": source_rank,
        "raw": event,
    }


def normalize_star(row: Dict[str, Any]) -> Dict[str, Any]:
    repo = row.get("repo", {}) or {}
    owner = (repo.get("owner") or {}).get("login")
    full_name = repo.get("full_name") or (
        f"{owner}/{repo.get('name')}" if owner and repo.get("name") else None
    )
    return {
        "repo_id": repo.get("id"),
        "full_name": full_name,
        "html_url": repo.get("html_url"),
        "private": repo.get("private"),
        "fork": repo.get("fork"),
        "language": repo.get("language"),
        "stargazers_count": repo.get("stargazers_count"),
        "watchers_count": repo.get("watchers_count"),
        "starred_at": row.get("starred_at"),
        "raw": row,
    }


class Monitor:
    def __init__(self, client: GitHubClient, storage: Storage) -> None:
        self.client = client
        self.storage = storage

    def fetch_profile(self, username: str) -> Tuple[bool, Dict[str, Any]]:
        cache_key = f"profile:{username}"
        status, data, headers = self.client.get_json(
            f"/users/{username}",
            cache_entry=self.storage.get_cache_entry(cache_key),
        )
        if status == 304:
            row = self.storage.conn.execute(
                "SELECT profile_json FROM profiles WHERE username = ?",
                (username,),
            ).fetchone()
            if row is None:
                raise RuntimeError("Received 304 for profile but no cached profile exists")
            return False, json.loads(row["profile_json"])

        if data is None:
            raise RuntimeError("GitHub returned an empty profile payload")

        self.storage.upsert_cache_entry(cache_key, headers)
        self.storage.save_profile(username, data)
        return True, data

    def fetch_events(self, username: str, max_pages: int, per_page: int = 100) -> Tuple[bool, List[Dict[str, Any]]]:
        all_events: List[Dict[str, Any]] = []
        changed_any = False

        for page in range(1, max_pages + 1):
            cache_key = f"events:{username}:page:{page}:per:{per_page}"
            status, data, headers = self.client.get_json(
                f"/users/{username}/events/public",
                params={"per_page": per_page, "page": page},
                cache_entry=self.storage.get_cache_entry(cache_key),
            )

            if status == 304:
                continue

            changed_any = True
            self.storage.upsert_cache_entry(cache_key, headers)
            rows = data or []
            if not rows:
                break
            for rank, event in enumerate(rows, start=1):
                all_events.append(normalize_event(event, source_page=page, source_rank=rank))
            if len(rows) < per_page:
                break

        return changed_any, all_events

    def fetch_stars(self, username: str, max_pages: int, per_page: int = 100) -> Tuple[bool, List[Dict[str, Any]]]:
        all_stars: List[Dict[str, Any]] = []
        changed_any = False

        for page in range(1, max_pages + 1):
            cache_key = f"stars:{username}:page:{page}:per:{per_page}"
            status, data, headers = self.client.get_json(
                f"/users/{username}/starred",
                params={
                    "per_page": per_page,
                    "page": page,
                    "sort": "created",
                    "direction": "desc",
                },
                accept="application/vnd.github.star+json",
                cache_entry=self.storage.get_cache_entry(cache_key),
            )

            if status == 304:
                continue

            changed_any = True
            self.storage.upsert_cache_entry(cache_key, headers)
            rows = data or []
            if not rows:
                break
            for row in rows:
                normalized = normalize_star(row)
                if normalized.get("full_name"):
                    all_stars.append(normalized)
            if len(rows) < per_page:
                break

        return changed_any, all_stars

    def snapshot(
        self,
        username: str,
        mode: str,
        events_pages: int,
        stars_pages: int,
    ) -> Dict[str, Any]:
        existing_username = self.storage.get_metadata("username")
        if existing_username and existing_username != username:
            raise RuntimeError(
                f"State database belongs to username={existing_username!r}, not {username!r}"
            )
        self.storage.set_metadata("username", username)
        self.storage.set_metadata("api_version", API_VERSION)

        summary: Dict[str, Any] = {
            "ran_at": utc_now_iso(),
            "username": username,
            "profile_refreshed": False,
            "events_changed": False,
            "stars_changed": False,
            "new_events": [],
            "star_changes": [],
        }

        profile_refreshed, profile = self.fetch_profile(username)
        summary["profile_refreshed"] = profile_refreshed
        summary["profile"] = {
            "login": profile.get("login"),
            "name": profile.get("name"),
            "html_url": profile.get("html_url"),
            "public_repos": profile.get("public_repos"),
            "followers": profile.get("followers"),
            "following": profile.get("following"),
            "created_at": profile.get("created_at"),
            "updated_at": profile.get("updated_at"),
        }

        if mode in {"events", "all"}:
            events_changed, events = self.fetch_events(username, max_pages=events_pages)
            summary["events_changed"] = events_changed
            if events:
                new_events = self.storage.insert_events(username, events)
                summary["new_events"] = new_events

        if mode in {"stars", "all"}:
            stars_changed, stars = self.fetch_stars(username, max_pages=stars_pages)
            summary["stars_changed"] = stars_changed
            if stars_changed:
                previous = self.storage.get_current_stars(username)
                current = {row["full_name"]: row for row in stars}
                changes: List[Dict[str, Any]] = []

                for full_name, row in current.items():
                    if full_name not in previous:
                        changes.append(
                            {
                                "username": username,
                                "full_name": full_name,
                                "repo_id": row.get("repo_id"),
                                "html_url": row.get("html_url"),
                                "event_kind": "star_added",
                                "detected_at": utc_now_iso(),
                                "starred_at": row.get("starred_at"),
                                "language": row.get("language"),
                            }
                        )

                for full_name, old in previous.items():
                    if full_name not in current:
                        changes.append(
                            {
                                "username": username,
                                "full_name": full_name,
                                "repo_id": old.get("repo_id"),
                                "html_url": old.get("html_url"),
                                "event_kind": "star_removed",
                                "detected_at": utc_now_iso(),
                                "starred_at": old.get("starred_at"),
                            }
                        )

                self.storage.replace_current_stars(username, stars)
                self.storage.insert_star_history(changes)
                summary["star_changes"] = changes

        return summary


def format_event_line(event: Dict[str, Any]) -> str:
    parts = [
        str(event.get("created_at") or "?"),
        str(event.get("type") or "?"),
    ]
    if event.get("action"):
        parts.append(f"action={event['action']}")
    if event.get("repo"):
        parts.append(f"repo={event['repo']}")
    return " | ".join(parts)


def format_summary(summary: Dict[str, Any]) -> str:
    lines = [
        f"[{summary['ran_at']}] user={summary['username']}",
        f"  profile refreshed: {summary['profile_refreshed']}",
        f"  events endpoint changed: {summary['events_changed']}",
        f"  stars endpoint changed: {summary['stars_changed']}",
        f"  new events inserted: {len(summary['new_events'])}",
        f"  star changes detected: {len(summary['star_changes'])}",
    ]

    for event in summary["new_events"][:10]:
        lines.append(f"    + {format_event_line(event)}")
    extra_events = len(summary["new_events"]) - 10
    if extra_events > 0:
        lines.append(f"    ... and {extra_events} more event(s)")

    for change in summary["star_changes"][:10]:
        if change["event_kind"] == "star_added":
            lines.append(
                f"    + STAR {change['full_name']} @ {change.get('starred_at') or 'unknown-time'}"
            )
        else:
            lines.append(
                f"    - STAR {change['full_name']} removed (detected {change['detected_at']})"
            )
    extra_star_changes = len(summary["star_changes"]) - 10
    if extra_star_changes > 0:
        lines.append(f"    ... and {extra_star_changes} more star change(s)")

    return "\n".join(lines)


class GracefulTerminator:
    def __init__(self) -> None:
        self.stop = False
        signal.signal(signal.SIGINT, self._handle)
        signal.signal(signal.SIGTERM, self._handle)

    def _handle(self, signum: int, _frame: Any) -> None:
        self.stop = True


def resolve_token(args: argparse.Namespace, env_values: Dict[str, str]) -> Optional[str]:
    if args.token:
        return args.token
    if args.token_env in os.environ:
        return os.environ.get(args.token_env)
    return env_values.get(args.token_env)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitor a GitHub user's public events and starred repositories."
    )
    parser.add_argument(
        "--token",
        default=None,
        help="GitHub token. Prefer environment variables or a .env file instead.",
    )
    parser.add_argument(
        "--token-env",
        default="GH_TOKEN",
        help="Environment variable name containing the GitHub token. Default: GH_TOKEN",
    )
    parser.add_argument(
        "--env-file",
        default=DEFAULT_ENV_FILE,
        help="Path to a .env-style file. Default: ./.env",
    )
    parser.add_argument(
        "--state-dir",
        default=DEFAULT_STATE_DIR,
        help=f"Directory to store monitor state. Default: {DEFAULT_STATE_DIR}",
    )
    parser.add_argument(
        "--db-name",
        default=DEFAULT_DB_NAME,
        help=f"SQLite database filename inside --state-dir. Default: {DEFAULT_DB_NAME}",
    )
    parser.add_argument(
        "--events-pages",
        type=int,
        default=3,
        help="How many pages of /events/public to request. Default: 3",
    )
    parser.add_argument(
        "--stars-pages",
        type=int,
        default=10,
        help="How many pages of /starred to request. Default: 10",
    )
    parser.add_argument(
        "--user-agent",
        default=DEFAULT_USER_AGENT,
        help=f"HTTP User-Agent header. Default: {DEFAULT_USER_AGENT}",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="HTTP timeout in seconds. Default: 30",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON summaries instead of text summaries.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("username", help="GitHub username to monitor")
    common.add_argument(
        "--mode",
        choices=("events", "stars", "all"),
        default="all",
        help="What to collect. Default: all",
    )
    common.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-run output",
    )

    snapshot_parser = subparsers.add_parser(
        "snapshot",
        parents=[common],
        help="Run a single collection pass",
    )
    snapshot_parser.set_defaults(command="snapshot")

    watch_parser = subparsers.add_parser(
        "watch",
        parents=[common],
        help="Continuously poll on an interval",
    )
    watch_parser.add_argument(
        "--interval",
        type=int,
        default=900,
        help="Polling interval in seconds. Default: 900",
    )
    watch_parser.set_defaults(command="watch")

    inspect_parser = subparsers.add_parser(
        "inspect",
        help="Inspect the local SQLite state without calling GitHub.",
    )
    inspect_parser.add_argument("username", help="GitHub username stored in the database")
    inspect_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="How many recent rows to show from each category. Default: 10",
    )
    inspect_parser.set_defaults(command="inspect")

    return parser.parse_args(argv)


def inspect_local_state(storage: Storage, username: str, limit: int, as_json: bool) -> int:
    recent_events = [dict(row) for row in storage.recent_events(username, limit=limit)]
    recent_star_history = [dict(row) for row in storage.recent_star_history(username, limit=limit)]
    result = {
        "username": username,
        "recent_events": recent_events,
        "recent_star_history": recent_star_history,
    }

    if as_json:
        print(json.dumps(result, indent=4, sort_keys=True))
        return 0

    print(f"user={username}")
    print(f"  recent events: {len(recent_events)}")
    for row in recent_events:
        print(
            f"    - {row.get('created_at')} | {row.get('event_type')} | "
            f"action={row.get('action')} | repo={row.get('repo_name')}"
        )

    print(f"  recent star history: {len(recent_star_history)}")
    for row in recent_star_history:
        print(
            f"    - {row.get('detected_at')} | {row.get('event_kind')} | "
            f"repo={row.get('full_name')} | starred_at={row.get('starred_at')}"
        )
    return 0


def run_snapshot(args: argparse.Namespace, monitor: Monitor) -> int:
    summary = monitor.snapshot(
        username=args.username,
        mode=args.mode,
        events_pages=args.events_pages,
        stars_pages=args.stars_pages,
    )
    if not args.quiet:
        if args.json:
            print(json.dumps(summary, indent=4, sort_keys=True))
        else:
            print(format_summary(summary))
    return 0


def run_watch(args: argparse.Namespace, monitor: Monitor) -> int:
    terminator = GracefulTerminator()
    while not terminator.stop:
        summary = monitor.snapshot(
            username=args.username,
            mode=args.mode,
            events_pages=args.events_pages,
            stars_pages=args.stars_pages,
        )
        if not args.quiet:
            if args.json:
                print(json.dumps(summary, indent=4, sort_keys=True))
            else:
                print(format_summary(summary))
        for _ in range(args.interval):
            if terminator.stop:
                break
            time.sleep(1)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    env_path = Path(args.env_file).expanduser().resolve()
    env_values = load_dotenv(env_path)
    token = resolve_token(args, env_values)

    state_dir = Path(args.state_dir).expanduser().resolve()
    db_path = state_dir / args.db_name

    storage = Storage(db_path)
    try:
        if args.command == "inspect":
            return inspect_local_state(storage, args.username, args.limit, args.json)

        client = GitHubClient(
            token=token,
            user_agent=args.user_agent,
            timeout=args.timeout,
        )
        monitor = Monitor(client, storage)

        if args.command == "snapshot":
            return run_snapshot(args, monitor)
        if args.command == "watch":
            return run_watch(args, monitor)
        raise RuntimeError(f"Unhandled command: {args.command}")
    except KeyboardInterrupt:
        return 130
    finally:
        storage.close()


if __name__ == "__main__":
    raise SystemExit(main())
