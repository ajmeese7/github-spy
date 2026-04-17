"""CLI entry point for github-spy."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import click

from github_spy import __version__
from github_spy.client import GitHubClient
from github_spy.monitor import snapshot, watch
from github_spy.output import (
    console as output_console,
)
from github_spy.output import (
    export_data,
    render_diff,
    render_event_detail,
    render_inspect,
    render_snapshot,
    render_stats,
    render_users,
)
from github_spy.storage import DEFAULT_DB_DIR, DEFAULT_DB_NAME, Storage

log = logging.getLogger("github_spy")

VALID_COLLECTORS = ("events", "stars", "followers", "following", "repos", "profile", "all")


def _load_dotenv(env_path: Path) -> dict[str, str]:
    """Parse a .env file, returning key-value pairs."""
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
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


def _resolve_token(token: str | None, token_env: str, env_file: str) -> str | None:
    """Resolve the GitHub token from CLI arg, env var, or .env file."""
    if token:
        return token
    if token_env in os.environ:
        return os.environ[token_env]
    env_path = Path(env_file).expanduser().resolve()
    env_values = _load_dotenv(env_path)
    return env_values.get(token_env)


def _parse_collect(collect: str) -> tuple[str, ...]:
    """Parse the --collect comma-separated string into a validated tuple."""
    parts = [c.strip().lower() for c in collect.split(",") if c.strip()]
    for p in parts:
        if p not in VALID_COLLECTORS:
            raise click.BadParameter(
                f"Unknown collector '{p}'. Valid: {', '.join(VALID_COLLECTORS)}"
            )
    return tuple(parts) if parts else ("all",)


@click.group()
@click.version_option(__version__, prog_name="github-spy")
@click.option("--token", envvar="GH_TOKEN", default=None, help="GitHub personal access token.")
@click.option("--token-env", default="GH_TOKEN", help="Env var name for the token.")
@click.option("--env-file", default=".env", help="Path to .env file.")
@click.option(
    "--db",
    type=click.Path(),
    default=None,
    help=f"Path to SQLite database. Default: {DEFAULT_DB_DIR / DEFAULT_DB_NAME}",
)
@click.option("--json", "as_json", is_flag=True, help="Output machine-readable JSON.")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging.")
@click.pass_context
def app(
    ctx: click.Context,
    token: str | None,
    token_env: str,
    env_file: str,
    db: str | None,
    as_json: bool,
    verbose: bool,
) -> None:
    """Archive GitHub users' public activity into durable local history."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    resolved_token = _resolve_token(token, token_env, env_file)
    db_path = Path(db) if db else DEFAULT_DB_DIR / DEFAULT_DB_NAME

    ctx.ensure_object(dict)
    ctx.obj["token"] = resolved_token
    ctx.obj["db_path"] = db_path
    ctx.obj["as_json"] = as_json


@app.command()
@click.argument("usernames", nargs=-1, required=True)
@click.option(
    "--collect",
    default="all",
    help="Comma-separated collectors: events,stars,followers,following,repos,profile,all",
)
@click.option("--events-pages", type=int, default=3, help="Pages of events to fetch.")
@click.option(
    "--stars-pages",
    type=int,
    default=None,
    help="Cap on pages of stars (default: paginate to exhaustion).",
)
@click.option(
    "--followers-pages",
    type=int,
    default=None,
    help="Cap on pages of followers/following (default: paginate to exhaustion).",
)
@click.option(
    "--repos-pages",
    type=int,
    default=None,
    help="Cap on pages of repos (default: paginate to exhaustion).",
)
@click.pass_context
def snapshot_cmd(
    ctx: click.Context,
    usernames: tuple[str, ...],
    collect: str,
    events_pages: int,
    stars_pages: int | None,
    followers_pages: int | None,
    repos_pages: int | None,
) -> None:
    """Take a one-time snapshot of user activity."""
    collectors = _parse_collect(collect)
    as_json = ctx.obj["as_json"]

    with (
        GitHubClient(token=ctx.obj["token"]) as client,
        Storage(ctx.obj["db_path"]) as storage,
    ):
        for username in usernames:
            try:
                summary = snapshot(
                    client,
                    storage,
                    username,
                    collect=collectors,
                    events_pages=events_pages,
                    stars_pages=stars_pages,
                    followers_pages=followers_pages,
                    repos_pages=repos_pages,
                )
                render_snapshot(summary, as_json=as_json)
            except Exception as exc:
                output_console.print(f"[red]Error collecting {username}: {exc}[/red]")
                if ctx.obj.get("verbose"):
                    log.exception("Failed to collect %s", username)


@app.command("watch")
@click.argument("usernames", nargs=-1, required=True)
@click.option("--interval", type=int, default=900, help="Polling interval in seconds.")
@click.option("--collect", default="all", help="Comma-separated collectors.")
@click.option("--events-pages", type=int, default=3)
@click.option("--stars-pages", type=int, default=None)
@click.option("--followers-pages", type=int, default=None)
@click.option("--repos-pages", type=int, default=None)
@click.pass_context
def watch_cmd(
    ctx: click.Context,
    usernames: tuple[str, ...],
    interval: int,
    collect: str,
    events_pages: int,
    stars_pages: int | None,
    followers_pages: int | None,
    repos_pages: int | None,
) -> None:
    """Continuously poll for user activity on an interval."""
    collectors = _parse_collect(collect)
    as_json = ctx.obj["as_json"]

    def on_snapshot(summary):  # noqa: ANN001, ANN202
        render_snapshot(summary, as_json=as_json)

    with (
        GitHubClient(token=ctx.obj["token"]) as client,
        Storage(ctx.obj["db_path"]) as storage,
    ):
        watch(
            client,
            storage,
            list(usernames),
            interval=interval,
            collect=collectors,
            events_pages=events_pages,
            stars_pages=stars_pages,
            followers_pages=followers_pages,
            repos_pages=repos_pages,
            on_snapshot=on_snapshot,
        )


@app.command()
@click.argument("username")
@click.option("--limit", type=int, default=20, help="Number of recent rows to show.")
@click.option(
    "--type",
    "data_type",
    type=click.Choice(["events", "stars", "followers", "following", "repos", "profile"]),
    default="events",
    help="Type of data to inspect.",
)
@click.option(
    "--event-type",
    "event_type_filter",
    default=None,
    help="(events only) Comma-separated event types to filter (e.g. PushEvent,PullRequestEvent).",
)
@click.option(
    "--repo",
    "repo_filter",
    default=None,
    help="(events only) Filter to a single repo full_name (e.g. owner/name).",
)
@click.option(
    "--since",
    default=None,
    help="(events only) Lower bound on created_at (YYYY-MM-DD or ISO).",
)
@click.option(
    "--until",
    "until_date",
    default=None,
    help="(events only) Upper bound on created_at (YYYY-MM-DD or ISO).",
)
@click.pass_context
def inspect(
    ctx: click.Context,
    username: str,
    limit: int,
    data_type: str,
    event_type_filter: str | None,
    repo_filter: str | None,
    since: str | None,
    until_date: str | None,
) -> None:
    """View locally stored data without calling GitHub.

    For --type events, supports --event-type, --repo, --since, --until filters.
    Filters are ignored for non-event types (stars, followers, etc.).
    """
    as_json = ctx.obj["as_json"]

    event_types: list[str] | None = None
    if event_type_filter:
        event_types = [t.strip() for t in event_type_filter.split(",") if t.strip()]

    with Storage(ctx.obj["db_path"]) as storage:
        if data_type == "events":
            if event_types or repo_filter or since or until_date:
                rows = storage.query_events(
                    username,
                    event_types=event_types,
                    repo=repo_filter,
                    since=since,
                    until=until_date,
                    limit=limit,
                )
            else:
                rows = storage.recent_events(username, limit=limit)
        elif data_type == "stars":
            rows = storage.recent_star_changes(username, limit=limit)
        elif data_type == "followers":
            rows = storage.recent_follower_changes(username, limit=limit)
        elif data_type == "following":
            rows = storage.recent_following_changes(username, limit=limit)
        elif data_type == "repos":
            rows = storage.recent_repo_changes(username, limit=limit)
        elif data_type == "profile":
            rows = storage.recent_profile_changes(username, limit=limit)
        else:
            rows = []

        render_inspect(username, data_type, rows, as_json=as_json)


@app.command()
@click.argument("username")
@click.pass_context
def stats(ctx: click.Context, username: str) -> None:
    """Show analytics for a tracked user."""
    as_json = ctx.obj["as_json"]

    with Storage(ctx.obj["db_path"]) as storage:
        event_counts = storage.event_counts_by_type(username)
        language_dist = storage.star_language_distribution(username)
        top_repos = storage.top_repos_by_events(username)
        activity = storage.events_by_day(username)

        render_stats(
            username,
            event_counts=event_counts,
            language_dist=language_dist,
            top_repos=top_repos,
            activity_by_day=activity,
            as_json=as_json,
        )


@app.command()
@click.argument("username")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["csv", "json", "jsonl"]),
    default="json",
    help="Export format.",
)
@click.option(
    "--type",
    "data_type",
    type=click.Choice(["events", "stars", "followers", "repos"]),
    default="events",
    help="Type of data to export.",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(),
    default=None,
    help="Output file path (default: stdout).",
)
@click.pass_context
def export(
    ctx: click.Context,
    username: str,
    fmt: str,
    data_type: str,
    output_path: str | None,
) -> None:
    """Export collected data as CSV, JSON, or JSONL."""
    with Storage(ctx.obj["db_path"]) as storage:
        if data_type == "events":
            rows = storage.all_events(username)
        elif data_type == "stars":
            rows = storage.all_star_history(username)
        elif data_type == "followers":
            rows = storage.all_follower_history(username)
        elif data_type == "repos":
            rows = storage.all_repo_history(username)
        else:
            rows = []

        if output_path:
            with open(output_path, "w", encoding="utf-8") as f:
                export_data(rows, fmt, f)
            output_console.print(f"[green]Exported {len(rows)} rows to {output_path}[/green]")
        else:
            export_data(rows, fmt, sys.stdout)


@app.command()
@click.argument("username")
@click.option("--since", required=True, help="Start date (YYYY-MM-DD or ISO datetime).")
@click.option("--until", "until_date", default=None, help="End date (YYYY-MM-DD or ISO datetime).")
@click.pass_context
def diff(ctx: click.Context, username: str, since: str, until_date: str | None) -> None:
    """Show what changed for a user between two points in time."""
    as_json = ctx.obj["as_json"]

    with Storage(ctx.obj["db_path"]) as storage:
        # Query changes within the date range
        star_changes = _filter_by_date(
            storage.all_star_history(username), "detected_at", since, until_date
        )
        follower_changes = _filter_by_date(
            storage.all_follower_history(username), "detected_at", since, until_date
        )
        repo_changes = _filter_by_date(
            storage.all_repo_history(username), "detected_at", since, until_date
        )
        profile_changes = _filter_by_date(
            storage.recent_profile_changes(username, limit=1000),
            "detected_at",
            since,
            until_date,
        )
        events = storage.events_between(username, since=since, until=until_date)

        render_diff(
            username,
            star_changes=star_changes,
            follower_changes=follower_changes,
            repo_changes=repo_changes,
            profile_changes=profile_changes,
            events=events,
            as_json=as_json,
        )


@app.command()
@click.argument("event_id")
@click.pass_context
def show(ctx: click.Context, event_id: str) -> None:
    """Show the full detail of a single stored event by its event_id."""
    as_json = ctx.obj["as_json"]

    with Storage(ctx.obj["db_path"]) as storage:
        event = storage.get_event(event_id)
        if event is None and not as_json:
            output_console.print(f"[red]No event with id {event_id} found locally.[/red]")
            raise click.exceptions.Exit(code=1)
        render_event_detail(event, as_json=as_json)


@app.command()
@click.pass_context
def users(ctx: click.Context) -> None:
    """List all tracked users in the database."""
    as_json = ctx.obj["as_json"]

    with Storage(ctx.obj["db_path"]) as storage:
        user_list = storage.list_users()
        render_users(user_list, as_json=as_json)


def _filter_by_date(rows: list[dict], date_field: str, since: str, until: str | None) -> list[dict]:
    """Filter rows where date_field falls between since and until."""
    filtered = []
    for row in rows:
        val = row.get(date_field, "")
        if val and val >= since and (until is None or val <= until):
            filtered.append(row)
    return filtered
