#!/usr/bin/env python3
"""
gh_user_monitor.py

Monitor a GitHub user's public activity and starred repositories.

Features:
- Polls public user events
- Polls current starred repositories with `starred_at` timestamps
- Stores current state locally
- Appends deltas to JSONL logs
- Supports one-shot snapshots and continuous polling

Examples:
    python gh_user_monitor.py snapshot octocat
    python gh_user_monitor.py snapshot octocat --mode all --state-dir ./gh_state
    python gh_user_monitor.py watch octocat --interval 900 --token-env GH_TOKEN
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, sort_keys=True)
        f.write("\n")
    tmp.replace(path)


def append_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        return
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True))
            f.write("\n")


class GitHubClient:
    def __init__(self, token: Optional[str] = None, user_agent: str = "gh-user-monitor/1.0") -> None:
        self.token = token
        self.user_agent = user_agent

    def _request(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        accept: str = "application/vnd.github+json",
    ) -> Tuple[Any, Dict[str, str]]:
        url = f"{API_BASE}{path}"
        if params:
            qs = urllib.parse.urlencode(params)
            url = f"{url}?{qs}"

        headers = {
            "Accept": accept,
            "User-Agent": self.user_agent,
            "X-GitHub-Api-Version": API_VERSION,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                data = json.loads(body)
                resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                return data, resp_headers
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitHub API error {e.code} for {url}: {detail}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Network error for {url}: {e}") from e

    def get_user(self, username: str) -> Dict[str, Any]:
        data, _ = self._request(f"/users/{username}")
        return data

    def get_public_events(self, username: str, max_pages: int = 3, per_page: int = 100) -> List[Dict[str, Any]]:
        all_rows: List[Dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            rows, _ = self._request(
                f"/users/{username}/events/public",
                params={"per_page": per_page, "page": page},
            )
            if not rows:
                break
            all_rows.extend(rows)
            if len(rows) < per_page:
                break
        return all_rows

    def get_starred(self, username: str, max_pages: int = 10, per_page: int = 100) -> List[Dict[str, Any]]:
        all_rows: List[Dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            rows, _ = self._request(
                f"/users/{username}/starred",
                params={"per_page": per_page, "page": page, "sort": "created", "direction": "desc"},
                accept="application/vnd.github.star+json",
            )
            if not rows:
                break
            all_rows.extend(rows)
            if len(rows) < per_page:
                break
        return all_rows


def normalize_event(event: Dict[str, Any]) -> Dict[str, Any]:
    payload = event.get("payload", {}) or {}
    return {
        "event_id": str(event.get("id")),
        "type": event.get("type"),
        "created_at": event.get("created_at"),
        "repo": (event.get("repo") or {}).get("name"),
        "public": event.get("public"),
        "actor": (event.get("actor") or {}).get("login"),
        "action": payload.get("action"),
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


def summarize_event(ev: Dict[str, Any]) -> str:
    bits = [
        ev.get("created_at", "?"),
        ev.get("type", "?"),
    ]
    if ev.get("action"):
        bits.append(f"action={ev['action']}")
    if ev.get("repo"):
        bits.append(f"repo={ev['repo']}")
    return " | ".join(bits)


def build_state_paths(state_dir: Path) -> Dict[str, Path]:
    return {
        "state": state_dir / "state.json",
        "events_log": state_dir / "events.jsonl",
        "stars_log": state_dir / "stars.jsonl",
    }


def collect_once(
    client: GitHubClient,
    username: str,
    state_dir: Path,
    mode: str,
    events_pages: int,
    stars_pages: int,
    quiet: bool = False,
) -> int:
    ensure_dir(state_dir)
    paths = build_state_paths(state_dir)
    state = load_json(
        paths["state"],
        default={
            "username": username,
            "last_run": None,
            "profile": {},
            "events_seen": {},
            "stars_current": {},
        },
    )

    if state.get("username") not in (None, username):
        raise RuntimeError(
            f"State directory belongs to username={state.get('username')!r}, not {username!r}"
        )

    profile = client.get_user(username)

    new_event_rows: List[Dict[str, Any]] = []
    new_star_rows: List[Dict[str, Any]] = []

    if mode in ("events", "all"):
        events = [normalize_event(e) for e in client.get_public_events(username, max_pages=events_pages)]
        seen_events: Dict[str, Dict[str, Any]] = state.get("events_seen", {})
        current_ids = set()

        for ev in events:
            event_id = ev["event_id"]
            current_ids.add(event_id)
            if event_id not in seen_events:
                row = {
                    "detected_at": utc_now_iso(),
                    "kind": "event_new",
                    **ev,
                }
                new_event_rows.append(row)
                seen_events[event_id] = {
                    "type": ev["type"],
                    "created_at": ev["created_at"],
                    "repo": ev["repo"],
                    "action": ev["action"],
                }

        state["events_seen"] = seen_events

    if mode in ("stars", "all"):
        starred = [normalize_star(s) for s in client.get_starred(username, max_pages=stars_pages)]
        prev_stars: Dict[str, Dict[str, Any]] = state.get("stars_current", {})
        curr_stars: Dict[str, Dict[str, Any]] = {}

        for star in starred:
            full_name = star["full_name"]
            if not full_name:
                continue
            curr_stars[full_name] = {
                "repo_id": star["repo_id"],
                "html_url": star["html_url"],
                "starred_at": star["starred_at"],
                "language": star["language"],
                "fork": star["fork"],
            }

            if full_name not in prev_stars:
                new_star_rows.append({
                    "detected_at": utc_now_iso(),
                    "kind": "star_added",
                    **star,
                })

        removed = sorted(set(prev_stars) - set(curr_stars))
        for full_name in removed:
            old = prev_stars[full_name]
            new_star_rows.append({
                "detected_at": utc_now_iso(),
                "kind": "star_removed",
                "full_name": full_name,
                "repo_id": old.get("repo_id"),
                "html_url": old.get("html_url"),
                "previous_starred_at": old.get("starred_at"),
            })

        state["stars_current"] = curr_stars

    state["username"] = username
    state["last_run"] = utc_now_iso()
    state["profile"] = {
        "id": profile.get("id"),
        "login": profile.get("login"),
        "name": profile.get("name"),
        "type": profile.get("type"),
        "site_admin": profile.get("site_admin"),
        "public_repos": profile.get("public_repos"),
        "followers": profile.get("followers"),
        "following": profile.get("following"),
        "created_at": profile.get("created_at"),
        "updated_at": profile.get("updated_at"),
        "html_url": profile.get("html_url"),
    }

    append_jsonl(paths["events_log"], new_event_rows)
    append_jsonl(paths["stars_log"], new_star_rows)
    save_json(paths["state"], state)

    if not quiet:
        print(f"[{utc_now_iso()}] user={username}")
        print(f"  state_dir: {state_dir}")
        print(f"  new events: {len(new_event_rows)}")
        for row in new_event_rows[:10]:
            print(f"    + {summarize_event(row)}")
        if len(new_event_rows) > 10:
            print(f"    ... and {len(new_event_rows) - 10} more")

        print(f"  star changes: {len(new_star_rows)}")
        for row in new_star_rows[:10]:
            if row["kind"] == "star_added":
                print(f"    + STAR {row['full_name']} @ {row.get('starred_at')}")
            else:
                print(f"    - STAR {row['full_name']} removed (detected {row['detected_at']})")
        if len(new_star_rows) > 10:
            print(f"    ... and {len(new_star_rows) - 10} more")

    return 0


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitor a GitHub user's public events and starred repositories."
    )
    parser.add_argument(
        "--token",
        default=None,
        help="GitHub token. If omitted, unauthenticated requests are used unless --token-env is set.",
    )
    parser.add_argument(
        "--token-env",
        default="GH_TOKEN",
        help="Environment variable containing a GitHub token. Default: GH_TOKEN",
    )
    parser.add_argument(
        "--state-dir",
        default="./gh_monitor_state",
        help="Directory to store state and JSONL logs. Default: ./gh_monitor_state",
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
        help="Suppress per-run summary output",
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

    return parser.parse_args(argv)


def resolve_token(args: argparse.Namespace) -> Optional[str]:
    if args.token:
        return args.token
    if args.token_env:
        return os.environ.get(args.token_env)
    return None


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    token = resolve_token(args)
    state_dir = Path(args.state_dir).expanduser().resolve()
    client = GitHubClient(token=token)

    try:
        if args.command == "snapshot":
            return collect_once(
                client=client,
                username=args.username,
                state_dir=state_dir,
                mode=args.mode,
                events_pages=args.events_pages,
                stars_pages=args.stars_pages,
                quiet=args.quiet,
            )

        if args.command == "watch":
            while True:
                try:
                    collect_once(
                        client=client,
                        username=args.username,
                        state_dir=state_dir,
                        mode=args.mode,
                        events_pages=args.events_pages,
                        stars_pages=args.stars_pages,
                        quiet=args.quiet,
                    )
                except Exception as e:
                    print(f"[{utc_now_iso()}] error: {e}", file=sys.stderr)
                time.sleep(args.interval)

        raise RuntimeError(f"Unhandled command: {args.command}")

    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"fatal: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
