"""SQLite storage layer for github-spy."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from github_spy.models import (
    CacheEntry,
    FollowerChange,
    FollowerSnapshot,
    NormalizedEvent,
    ProfileFieldChange,
    ProfileSnapshot,
    RepoChange,
    RepoSnapshot,
    StarChange,
    StarSnapshot,
)

SCHEMA_VERSION = 2
# Bump when the url/details derivation changes so existing events get
# reprocessed. Unlike SCHEMA_VERSION (which tracks table shape), this tracks
# the *logic* that populates url/details, which can change independently.
DERIVATION_VERSION = 2

DEFAULT_DB_DIR = Path.home() / ".local" / "share" / "github-spy"
DEFAULT_DB_NAME = "github-spy.db"


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


class Storage:
    """SQLite-backed persistent storage for all github-spy data.

    Supports multiple users in a single database.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        _ensure_dir(db_path.parent)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Storage:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # -------------------------------------------------------------------
    # Schema
    # -------------------------------------------------------------------

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

            -- Profiles -------------------------------------------------

            CREATE TABLE IF NOT EXISTS profiles (
                username TEXT PRIMARY KEY,
                profile_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS profile_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                field_name TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                detected_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_profile_history_user_date
                ON profile_history (username, detected_at DESC);

            -- Events ---------------------------------------------------

            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                event_type TEXT,
                created_at TEXT,
                repo_name TEXT,
                actor_login TEXT,
                action TEXT,
                is_public INTEGER,
                payload_json TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                source_page INTEGER,
                source_rank INTEGER,
                url TEXT,
                details TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_events_user_date
                ON events (username, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_events_user_type_date
                ON events (username, event_type, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_events_user_repo_date
                ON events (username, repo_name, created_at DESC);

            -- Stars ----------------------------------------------------

            CREATE TABLE IF NOT EXISTS stars_current (
                username TEXT NOT NULL,
                full_name TEXT NOT NULL,
                repo_id INTEGER,
                html_url TEXT,
                is_private INTEGER,
                is_fork INTEGER,
                language TEXT,
                stargazers_count INTEGER,
                starred_at TEXT,
                snapshot_at TEXT NOT NULL,
                repo_json TEXT NOT NULL,
                PRIMARY KEY (username, full_name)
            );

            CREATE INDEX IF NOT EXISTS idx_stars_user_starred
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
                language TEXT,
                details_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_star_history_user_date
                ON star_history (username, detected_at DESC);
            CREATE INDEX IF NOT EXISTS idx_star_history_user_repo
                ON star_history (username, full_name, detected_at DESC);

            -- Followers ------------------------------------------------

            CREATE TABLE IF NOT EXISTS followers_current (
                username TEXT NOT NULL,
                follower_login TEXT NOT NULL,
                user_id INTEGER,
                html_url TEXT,
                snapshot_at TEXT NOT NULL,
                PRIMARY KEY (username, follower_login)
            );

            CREATE TABLE IF NOT EXISTS follower_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                login TEXT NOT NULL,
                user_id INTEGER,
                event_kind TEXT NOT NULL,
                detected_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_follower_history_user_date
                ON follower_history (username, detected_at DESC);

            -- Following ------------------------------------------------

            CREATE TABLE IF NOT EXISTS following_current (
                username TEXT NOT NULL,
                following_login TEXT NOT NULL,
                user_id INTEGER,
                html_url TEXT,
                snapshot_at TEXT NOT NULL,
                PRIMARY KEY (username, following_login)
            );

            CREATE TABLE IF NOT EXISTS following_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                login TEXT NOT NULL,
                user_id INTEGER,
                event_kind TEXT NOT NULL,
                detected_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_following_history_user_date
                ON following_history (username, detected_at DESC);

            -- Repos ----------------------------------------------------

            CREATE TABLE IF NOT EXISTS repos_current (
                username TEXT NOT NULL,
                full_name TEXT NOT NULL,
                repo_id INTEGER,
                html_url TEXT,
                language TEXT,
                description TEXT,
                is_fork INTEGER,
                stargazers_count INTEGER,
                snapshot_at TEXT NOT NULL,
                repo_json TEXT NOT NULL,
                PRIMARY KEY (username, full_name)
            );

            CREATE TABLE IF NOT EXISTS repo_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                repo_name TEXT NOT NULL,
                repo_id INTEGER,
                event_kind TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                language TEXT,
                details_json TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_repo_history_user_date
                ON repo_history (username, detected_at DESC);
            """
        )
        self.conn.commit()

        # Migration bookkeeping. Runs only the deltas newer than the DB's
        # recorded version; a fresh DB just stamps the current version.
        current = self.get_metadata("schema_version")
        if current is None:
            self.set_metadata("schema_version", str(SCHEMA_VERSION))
            self.set_metadata("derivation_version", str(DERIVATION_VERSION))
        else:
            self._migrate_schema(int(current))

        # If the derivation logic bumped since this DB was last opened,
        # re-compute url/details on ALL events (not just null ones).
        self._ensure_derivation_current()

    def _migrate_schema(self, from_version: int) -> None:
        """Apply schema migrations. Idempotent per recorded version."""
        cursor = self.conn.cursor()

        if from_version < 2:  # noqa: PLR2004
            # v2 adds url/details columns to events. ALTER TABLE ADD COLUMN is
            # safe and non-destructive in SQLite.
            existing_cols = {
                row["name"] for row in cursor.execute("PRAGMA table_info(events)").fetchall()
            }
            if "url" not in existing_cols:
                cursor.execute("ALTER TABLE events ADD COLUMN url TEXT")
            if "details" not in existing_cols:
                cursor.execute("ALTER TABLE events ADD COLUMN details TEXT")
            self.conn.commit()
            self._backfill_event_derivations()

            # Drop stale cache entries for endpoints that no longer use
            # conditional requests, so we don't keep dead rows around.
            cursor.execute(
                "DELETE FROM api_cache WHERE cache_key LIKE '%/starred:%' "
                "OR cache_key LIKE '%/followers:%' "
                "OR cache_key LIKE '%/following:%' "
                "OR cache_key LIKE '%/repos:%'"
            )
            self.conn.commit()

        self.set_metadata("schema_version", str(SCHEMA_VERSION))

    def _backfill_event_derivations(self, *, all_rows: bool = False) -> None:
        """Populate url/details on existing events by re-parsing payload_json.

        With all_rows=False (default), only rows missing url+details are
        processed — used right after adding the columns. With all_rows=True
        every event is re-derived — used when the derivation logic itself
        changes and existing values are stale.

        Imported lazily to avoid the storage -> collectors dependency cycle.
        """
        from github_spy.collectors.events import _derive_url_and_details

        cursor = self.conn.cursor()
        query = (
            "SELECT event_id, event_type, repo_name, action, payload_json FROM events"
            if all_rows
            else (
                "SELECT event_id, event_type, repo_name, action, payload_json "
                "FROM events WHERE url IS NULL AND details IS NULL"
            )
        )
        rows = cursor.execute(query).fetchall()
        for row in rows:
            try:
                raw = json.loads(row["payload_json"] or "{}")
            except (ValueError, TypeError):
                continue
            payload = raw.get("payload") or {}
            url, details = _derive_url_and_details(
                row["event_type"] or "", row["repo_name"], row["action"], payload
            )
            cursor.execute(
                "UPDATE events SET url = ?, details = ? WHERE event_id = ?",
                (url, details, row["event_id"]),
            )
        self.conn.commit()

    def _ensure_derivation_current(self) -> None:
        """Re-run event derivation when DERIVATION_VERSION has bumped."""
        stored = self.get_metadata("derivation_version")
        current = int(stored) if stored else 0
        if current < DERIVATION_VERSION:
            self._backfill_event_derivations(all_rows=True)
            self.set_metadata("derivation_version", str(DERIVATION_VERSION))

    # -------------------------------------------------------------------
    # Metadata
    # -------------------------------------------------------------------

    def set_metadata(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO metadata (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    def get_metadata(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None

    # -------------------------------------------------------------------
    # API cache
    # -------------------------------------------------------------------

    def get_cache_entry(self, cache_key: str) -> CacheEntry:
        row = self.conn.execute(
            "SELECT etag, last_modified FROM api_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if row is None:
            return CacheEntry()
        return CacheEntry(etag=row["etag"], last_modified=row["last_modified"])

    def upsert_cache_entry(self, cache_key: str, headers: Any) -> None:
        """Save ETag/Last-Modified from response headers."""
        etag = headers.get("etag") if hasattr(headers, "get") else None
        last_modified = headers.get("last-modified") if hasattr(headers, "get") else None
        self.conn.execute(
            "INSERT INTO api_cache (cache_key, etag, last_modified, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(cache_key) DO UPDATE SET "
            "etag=excluded.etag, last_modified=excluded.last_modified, "
            "updated_at=excluded.updated_at",
            (cache_key, etag, last_modified, utc_now_iso()),
        )
        self.conn.commit()

    # -------------------------------------------------------------------
    # Profiles
    # -------------------------------------------------------------------

    def save_profile(self, username: str, profile_json: str) -> None:
        self.conn.execute(
            "INSERT INTO profiles (username, profile_json, fetched_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(username) DO UPDATE SET "
            "profile_json=excluded.profile_json, fetched_at=excluded.fetched_at",
            (username, profile_json, utc_now_iso()),
        )
        self.conn.commit()

    def get_profile(self, username: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT profile_json FROM profiles WHERE username = ?", (username,)
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["profile_json"])

    def get_profile_snapshot(self, username: str) -> ProfileSnapshot | None:
        """Load the last stored profile as a typed snapshot."""
        data = self.get_profile(username)
        if data is None:
            return None
        row = self.conn.execute(
            "SELECT fetched_at FROM profiles WHERE username = ?", (username,)
        ).fetchone()
        return ProfileSnapshot(
            username=username,
            name=data.get("name"),
            bio=data.get("bio"),
            company=data.get("company"),
            location=data.get("location"),
            blog=data.get("blog"),
            public_repos=data.get("public_repos", 0),
            public_gists=data.get("public_gists", 0),
            followers=data.get("followers", 0),
            following=data.get("following", 0),
            html_url=data.get("html_url"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            fetched_at=row["fetched_at"] if row else "",
            raw_json=json.dumps(data, sort_keys=True),
        )

    def insert_profile_changes(self, changes: list[ProfileFieldChange]) -> None:
        if not changes:
            return
        self.conn.executemany(
            "INSERT INTO profile_history (username, field_name, old_value, new_value, detected_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [(c.username, c.field_name, c.old_value, c.new_value, c.detected_at) for c in changes],
        )
        self.conn.commit()

    def recent_profile_changes(self, username: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT field_name, old_value, new_value, detected_at "
            "FROM profile_history WHERE username = ? "
            "ORDER BY detected_at DESC, id DESC LIMIT ?",
            (username, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # -------------------------------------------------------------------
    # Events
    # -------------------------------------------------------------------

    def insert_events(self, events: list[NormalizedEvent]) -> list[NormalizedEvent]:
        """Insert events, returning only newly inserted ones (deduped by event_id)."""
        inserted: list[NormalizedEvent] = []
        now = utc_now_iso()
        cursor = self.conn.cursor()
        for ev in events:
            cursor.execute(
                "INSERT OR IGNORE INTO events "
                "(event_id, username, event_type, created_at, repo_name, actor_login, "
                "action, is_public, payload_json, first_seen_at, source_page, source_rank, "
                "url, details) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ev.event_id,
                    ev.username,
                    ev.event_type,
                    ev.created_at,
                    ev.repo_name,
                    ev.actor_login,
                    ev.action,
                    1 if ev.is_public else 0,
                    ev.raw_json,
                    now,
                    ev.source_page,
                    ev.source_rank,
                    ev.url,
                    ev.details,
                ),
            )
            if cursor.rowcount == 1:
                inserted.append(ev)
        self.conn.commit()
        return inserted

    def recent_events(self, username: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT event_id, event_type, created_at, repo_name, action, url, details, "
            "first_seen_at FROM events WHERE username = ? "
            "ORDER BY created_at DESC, first_seen_at DESC LIMIT ?",
            (username, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        """Return a single event by id, with parsed payload. None if missing."""
        row = self.conn.execute(
            "SELECT event_id, username, event_type, created_at, repo_name, actor_login, "
            "action, is_public, url, details, payload_json, first_seen_at "
            "FROM events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        try:
            result["payload"] = json.loads(result.pop("payload_json") or "{}")
        except (ValueError, TypeError):
            result["payload"] = {}
        return result

    def query_events(
        self,
        username: str,
        *,
        event_types: list[str] | None = None,
        repo: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Filterable events query powering `inspect --type events`."""
        clauses = ["username = ?"]
        args: list[Any] = [username]

        if event_types:
            placeholders = ", ".join("?" for _ in event_types)
            clauses.append(f"event_type IN ({placeholders})")
            args.extend(event_types)
        if repo:
            clauses.append("repo_name = ?")
            args.append(repo)
        if since:
            clauses.append("created_at >= ?")
            args.append(since)
        if until:
            clauses.append("created_at <= ?")
            args.append(until)

        where = " AND ".join(clauses)
        args.append(limit)
        rows = self.conn.execute(
            f"SELECT event_id, event_type, created_at, repo_name, action, url, details, "
            f"first_seen_at FROM events WHERE {where} "
            f"ORDER BY created_at DESC, first_seen_at DESC LIMIT ?",
            args,
        ).fetchall()
        return [dict(r) for r in rows]

    def events_between(
        self, username: str, since: str, until: str | None = None
    ) -> list[dict[str, Any]]:
        """Events in [since, until] for the `diff` command."""
        if until is None:
            rows = self.conn.execute(
                "SELECT event_id, event_type, created_at, repo_name, action, url, details "
                "FROM events WHERE username = ? AND created_at >= ? "
                "ORDER BY created_at ASC",
                (username, since),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT event_id, event_type, created_at, repo_name, action, url, details "
                "FROM events WHERE username = ? AND created_at >= ? AND created_at <= ? "
                "ORDER BY created_at ASC",
                (username, since, until),
            ).fetchall()
        return [dict(r) for r in rows]

    def event_count(self, username: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM events WHERE username = ?", (username,)
        ).fetchone()
        return row["cnt"] if row else 0

    # -------------------------------------------------------------------
    # Stars
    # -------------------------------------------------------------------

    def get_current_stars(self, username: str) -> dict[str, StarSnapshot]:
        rows = self.conn.execute(
            "SELECT full_name, repo_id, html_url, is_private, is_fork, language, "
            "stargazers_count, starred_at, repo_json "
            "FROM stars_current WHERE username = ?",
            (username,),
        ).fetchall()
        result: dict[str, StarSnapshot] = {}
        for row in rows:
            result[str(row["full_name"])] = StarSnapshot(
                full_name=str(row["full_name"]),
                repo_id=row["repo_id"],
                html_url=row["html_url"],
                is_private=bool(row["is_private"]),
                is_fork=bool(row["is_fork"]),
                language=row["language"],
                stargazers_count=row["stargazers_count"] or 0,
                starred_at=row["starred_at"],
                raw_json=row["repo_json"],
            )
        return result

    def replace_current_stars(self, username: str, stars: list[StarSnapshot]) -> None:
        now = utc_now_iso()
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM stars_current WHERE username = ?", (username,))
        for s in stars:
            cursor.execute(
                "INSERT INTO stars_current "
                "(username, full_name, repo_id, html_url, is_private, is_fork, "
                "language, stargazers_count, starred_at, snapshot_at, repo_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    username,
                    s.full_name,
                    s.repo_id,
                    s.html_url,
                    1 if s.is_private else 0,
                    1 if s.is_fork else 0,
                    s.language,
                    s.stargazers_count,
                    s.starred_at,
                    now,
                    s.raw_json,
                ),
            )
        self.conn.commit()

    def insert_star_changes(self, changes: list[StarChange]) -> None:
        if not changes:
            return
        self.conn.executemany(
            "INSERT INTO star_history "
            "(username, full_name, repo_id, html_url, event_kind, detected_at, "
            "starred_at, language, details_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    c.username,
                    c.full_name,
                    c.repo_id,
                    c.html_url,
                    c.kind.value,
                    c.detected_at,
                    c.starred_at,
                    c.language,
                    json.dumps(
                        {"full_name": c.full_name, "kind": c.kind.value, "language": c.language},
                        sort_keys=True,
                    ),
                )
                for c in changes
            ],
        )
        self.conn.commit()

    def recent_star_changes(self, username: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT full_name, event_kind, starred_at, detected_at, language "
            "FROM star_history WHERE username = ? "
            "ORDER BY detected_at DESC, id DESC LIMIT ?",
            (username, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def star_count(self, username: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM stars_current WHERE username = ?", (username,)
        ).fetchone()
        return row["cnt"] if row else 0

    # -------------------------------------------------------------------
    # Followers
    # -------------------------------------------------------------------

    def get_current_followers(self, username: str) -> dict[str, FollowerSnapshot]:
        rows = self.conn.execute(
            "SELECT follower_login, user_id, html_url FROM followers_current WHERE username = ?",
            (username,),
        ).fetchall()
        return {
            str(r["follower_login"]): FollowerSnapshot(
                login=str(r["follower_login"]),
                user_id=r["user_id"],
                html_url=r["html_url"],
            )
            for r in rows
        }

    def replace_current_followers(self, username: str, followers: list[FollowerSnapshot]) -> None:
        now = utc_now_iso()
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM followers_current WHERE username = ?", (username,))
        for f in followers:
            cursor.execute(
                "INSERT INTO followers_current "
                "(username, follower_login, user_id, html_url, snapshot_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (username, f.login, f.user_id, f.html_url, now),
            )
        self.conn.commit()

    def insert_follower_changes(self, changes: list[FollowerChange]) -> None:
        if not changes:
            return
        self.conn.executemany(
            "INSERT INTO follower_history (username, login, user_id, event_kind, detected_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [(c.username, c.login, c.user_id, c.kind.value, c.detected_at) for c in changes],
        )
        self.conn.commit()

    def recent_follower_changes(self, username: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT login, event_kind, detected_at "
            "FROM follower_history WHERE username = ? "
            "ORDER BY detected_at DESC, id DESC LIMIT ?",
            (username, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def follower_count(self, username: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM followers_current WHERE username = ?", (username,)
        ).fetchone()
        return row["cnt"] if row else 0

    # -------------------------------------------------------------------
    # Following
    # -------------------------------------------------------------------

    def get_current_following(self, username: str) -> dict[str, FollowerSnapshot]:
        rows = self.conn.execute(
            "SELECT following_login, user_id, html_url FROM following_current WHERE username = ?",
            (username,),
        ).fetchall()
        return {
            str(r["following_login"]): FollowerSnapshot(
                login=str(r["following_login"]),
                user_id=r["user_id"],
                html_url=r["html_url"],
            )
            for r in rows
        }

    def replace_current_following(self, username: str, following: list[FollowerSnapshot]) -> None:
        now = utc_now_iso()
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM following_current WHERE username = ?", (username,))
        for f in following:
            cursor.execute(
                "INSERT INTO following_current "
                "(username, following_login, user_id, html_url, snapshot_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (username, f.login, f.user_id, f.html_url, now),
            )
        self.conn.commit()

    def insert_following_changes(self, changes: list[FollowerChange]) -> None:
        if not changes:
            return
        self.conn.executemany(
            "INSERT INTO following_history (username, login, user_id, event_kind, detected_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [(c.username, c.login, c.user_id, c.kind.value, c.detected_at) for c in changes],
        )
        self.conn.commit()

    def recent_following_changes(self, username: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT login, event_kind, detected_at "
            "FROM following_history WHERE username = ? "
            "ORDER BY detected_at DESC, id DESC LIMIT ?",
            (username, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def following_count(self, username: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM following_current WHERE username = ?", (username,)
        ).fetchone()
        return row["cnt"] if row else 0

    # -------------------------------------------------------------------
    # Repos
    # -------------------------------------------------------------------

    def get_current_repos(self, username: str) -> dict[str, RepoSnapshot]:
        rows = self.conn.execute(
            "SELECT full_name, repo_id, html_url, language, description, is_fork, "
            "stargazers_count, repo_json "
            "FROM repos_current WHERE username = ?",
            (username,),
        ).fetchall()
        return {
            str(r["full_name"]): RepoSnapshot(
                full_name=str(r["full_name"]),
                repo_id=r["repo_id"],
                html_url=r["html_url"],
                language=r["language"],
                description=r["description"],
                is_fork=bool(r["is_fork"]),
                stargazers_count=r["stargazers_count"] or 0,
                raw_json=r["repo_json"],
            )
            for r in rows
        }

    def replace_current_repos(self, username: str, repos: list[RepoSnapshot]) -> None:
        now = utc_now_iso()
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM repos_current WHERE username = ?", (username,))
        for r in repos:
            cursor.execute(
                "INSERT INTO repos_current "
                "(username, full_name, repo_id, html_url, language, description, "
                "is_fork, stargazers_count, snapshot_at, repo_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    username,
                    r.full_name,
                    r.repo_id,
                    r.html_url,
                    r.language,
                    r.description,
                    1 if r.is_fork else 0,
                    r.stargazers_count,
                    now,
                    r.raw_json,
                ),
            )
        self.conn.commit()

    def insert_repo_changes(self, changes: list[RepoChange]) -> None:
        if not changes:
            return
        self.conn.executemany(
            "INSERT INTO repo_history "
            "(username, repo_name, repo_id, event_kind, detected_at, language, details_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    c.username,
                    c.repo_name,
                    c.repo_id,
                    c.kind.value,
                    c.detected_at,
                    c.language,
                    json.dumps({"repo_name": c.repo_name, "kind": c.kind.value}, sort_keys=True),
                )
                for c in changes
            ],
        )
        self.conn.commit()

    def recent_repo_changes(self, username: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT repo_name, event_kind, detected_at, language "
            "FROM repo_history WHERE username = ? "
            "ORDER BY detected_at DESC, id DESC LIMIT ?",
            (username, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def repo_count(self, username: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM repos_current WHERE username = ?", (username,)
        ).fetchone()
        return row["cnt"] if row else 0

    # -------------------------------------------------------------------
    # Analytics queries (for stats command)
    # -------------------------------------------------------------------

    def event_counts_by_type(self, username: str) -> list[tuple[str, int]]:
        rows = self.conn.execute(
            "SELECT event_type, COUNT(*) as cnt FROM events "
            "WHERE username = ? GROUP BY event_type ORDER BY cnt DESC",
            (username,),
        ).fetchall()
        return [(r["event_type"], r["cnt"]) for r in rows]

    def star_language_distribution(self, username: str) -> list[tuple[str, int]]:
        rows = self.conn.execute(
            "SELECT COALESCE(language, 'Unknown') as lang, COUNT(*) as cnt "
            "FROM stars_current WHERE username = ? "
            "GROUP BY lang ORDER BY cnt DESC",
            (username,),
        ).fetchall()
        return [(r["lang"], r["cnt"]) for r in rows]

    def events_by_day(self, username: str, days: int = 30) -> list[tuple[str, int]]:
        rows = self.conn.execute(
            "SELECT DATE(created_at) as day, COUNT(*) as cnt "
            "FROM events WHERE username = ? AND created_at >= DATE('now', ?) "
            "GROUP BY day ORDER BY day",
            (username, f"-{days} days"),
        ).fetchall()
        return [(r["day"], r["cnt"]) for r in rows]

    def top_repos_by_events(self, username: str, limit: int = 10) -> list[tuple[str, int]]:
        rows = self.conn.execute(
            "SELECT repo_name, COUNT(*) as cnt FROM events "
            "WHERE username = ? AND repo_name IS NOT NULL "
            "GROUP BY repo_name ORDER BY cnt DESC LIMIT ?",
            (username, limit),
        ).fetchall()
        return [(r["repo_name"], r["cnt"]) for r in rows]

    def star_changes_over_time(self, username: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT full_name, event_kind, detected_at, language "
            "FROM star_history WHERE username = ? "
            "ORDER BY detected_at DESC LIMIT ?",
            (username, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # -------------------------------------------------------------------
    # Multi-user queries
    # -------------------------------------------------------------------

    def list_users(self) -> list[dict[str, Any]]:
        """List all tracked users with counts of stored data."""
        users: dict[str, dict[str, int]] = {}

        for row in self.conn.execute(
            "SELECT username, COUNT(*) as cnt FROM events GROUP BY username"
        ).fetchall():
            users.setdefault(row["username"], {})["events"] = row["cnt"]

        for row in self.conn.execute(
            "SELECT username, COUNT(*) as cnt FROM stars_current GROUP BY username"
        ).fetchall():
            users.setdefault(row["username"], {})["stars"] = row["cnt"]

        for row in self.conn.execute(
            "SELECT username, COUNT(*) as cnt FROM followers_current GROUP BY username"
        ).fetchall():
            users.setdefault(row["username"], {})["followers"] = row["cnt"]

        for row in self.conn.execute(
            "SELECT username, COUNT(*) as cnt FROM repos_current GROUP BY username"
        ).fetchall():
            users.setdefault(row["username"], {})["repos"] = row["cnt"]

        return [
            {
                "username": u,
                "events": c.get("events", 0),
                "stars": c.get("stars", 0),
                "followers": c.get("followers", 0),
                "repos": c.get("repos", 0),
            }
            for u, c in sorted(users.items())
        ]

    # -------------------------------------------------------------------
    # Export helpers
    # -------------------------------------------------------------------

    def all_events(self, username: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT event_id, event_type, created_at, repo_name, actor_login, action, "
            "url, details, first_seen_at, payload_json "
            "FROM events WHERE username = ? ORDER BY created_at DESC",
            (username,),
        ).fetchall()
        return [dict(r) for r in rows]

    def all_star_history(self, username: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT full_name, event_kind, starred_at, detected_at, language, "
            "html_url, details_json "
            "FROM star_history WHERE username = ? ORDER BY detected_at DESC",
            (username,),
        ).fetchall()
        return [dict(r) for r in rows]

    def all_follower_history(self, username: str) -> list[dict[str, Any]]:
        # html_url from followers_current is best-effort: it's populated only
        # while the user is currently followed. Rows where the user has since
        # been unfollowed leave html_url as NULL, which is fine for export.
        rows = self.conn.execute(
            "SELECT fh.login, fh.event_kind, fh.detected_at, fh.user_id, "
            "fc.html_url "
            "FROM follower_history fh "
            "LEFT JOIN followers_current fc "
            "  ON fc.username = fh.username AND fc.follower_login = fh.login "
            "WHERE fh.username = ? ORDER BY fh.detected_at DESC",
            (username,),
        ).fetchall()
        return [dict(r) for r in rows]

    def all_repo_history(self, username: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT repo_name, event_kind, detected_at, language, details_json "
            "FROM repo_history WHERE username = ? ORDER BY detected_at DESC",
            (username,),
        ).fetchall()
        return [dict(r) for r in rows]
