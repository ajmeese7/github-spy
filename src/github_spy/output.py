"""Terminal output formatting and data export for github-spy."""

from __future__ import annotations

import csv
import json
from typing import TYPE_CHECKING, Any, TextIO

from rich.console import Console
from rich.json import JSON
from rich.panel import Panel
from rich.table import Table

if TYPE_CHECKING:
    from github_spy.models import CollectionResult, SnapshotSummary

console = Console()


# ---------------------------------------------------------------------------
# Snapshot summary
# ---------------------------------------------------------------------------


def render_snapshot(summary: SnapshotSummary, as_json: bool = False) -> None:
    """Print a snapshot summary to the terminal."""
    if as_json:
        console.print_json(json.dumps(_snapshot_to_dict(summary), sort_keys=True))
        return

    profile = summary.profile
    title = f"[bold]{summary.username}[/bold]"
    if profile and profile.name:
        title += f" ({profile.name})"

    lines: list[str] = [f"[dim]{summary.ran_at}[/dim]"]

    if profile:
        meta_parts = []
        if profile.public_repos:
            meta_parts.append(f"{profile.public_repos} repos")
        if profile.followers:
            meta_parts.append(f"{profile.followers} followers")
        if profile.following:
            meta_parts.append(f"{profile.following} following")
        if meta_parts:
            lines.append("  " + " / ".join(meta_parts))

    for result in summary.results:
        lines.append(_format_collector_result(result))

    rl = summary.rate_limit
    if rl and rl.remaining > 0:
        color = "green" if rl.remaining > 100 else "yellow" if rl.remaining > 20 else "red"
        lines.append(f"  [{color}]API quota: {rl.remaining}/{rl.limit} remaining[/{color}]")

    console.print(Panel("\n".join(lines), title=title, border_style="blue"))


def _format_collector_result(result: CollectionResult) -> str:
    name = result.collector_name
    if not result.changed:
        return f"  [dim]{name}: no changes[/dim]"

    parts = [f"  [bold]{name}[/bold]:"]

    if result.new_count > 0 and name == "events":
        parts.append(f"[green]+{result.new_count} new[/green]")
    elif result.new_count > 0:
        parts.append(f"{result.new_count} total")

    if result.change_count > 0:
        parts.append(f"[yellow]{result.change_count} changes[/yellow]")

    line = " ".join(parts)

    # Show detail lines
    details = result.details
    detail_lines: list[str] = []

    for ev in details.get("new_events", [])[:5]:
        detail_lines.append(
            f"    [green]+[/green] {ev.get('created_at', '?')} "
            f"{ev.get('type', '?')} {ev.get('repo', '')}"
        )

    for ch in details.get("changes", [])[:5]:
        kind = ch.get("kind", ch.get("event_kind", ""))
        if "added" in kind or "created" in kind or "followed" in kind:
            icon, color = "+", "green"
        else:
            icon, color = "-", "red"
        label = ch.get("full_name") or ch.get("repo_name") or ch.get("login", "?")
        detail_lines.append(f"    [{color}]{icon}[/{color}] {label}")

    for fc in details.get("field_changes", [])[:5]:
        detail_lines.append(
            f"    [yellow]~[/yellow] {fc['field']}: "
            f"{fc.get('old', 'null')} -> {fc.get('new', 'null')}"
        )

    extra = (
        len(details.get("new_events", []))
        + len(details.get("changes", []))
        + len(details.get("field_changes", []))
        - len(detail_lines)
    )
    if extra > 0:
        detail_lines.append(f"    [dim]... and {extra} more[/dim]")

    if detail_lines:
        line += "\n" + "\n".join(detail_lines)

    return line


def _snapshot_to_dict(summary: SnapshotSummary) -> dict[str, Any]:
    return {
        "ran_at": summary.ran_at,
        "username": summary.username,
        "profile": {
            "name": summary.profile.name if summary.profile else None,
            "followers": summary.profile.followers if summary.profile else None,
            "following": summary.profile.following if summary.profile else None,
            "public_repos": summary.profile.public_repos if summary.profile else None,
        },
        "results": [
            {
                "collector": r.collector_name,
                "changed": r.changed,
                "new_count": r.new_count,
                "change_count": r.change_count,
                "details": r.details,
            }
            for r in summary.results
        ],
        "rate_limit": {
            "remaining": summary.rate_limit.remaining,
            "limit": summary.rate_limit.limit,
        }
        if summary.rate_limit
        else None,
    }


# ---------------------------------------------------------------------------
# Inspect command
# ---------------------------------------------------------------------------


_INSPECT_COLUMN_SPECS: dict[str, list[tuple[str, str, dict[str, Any]]]] = {
    # data_type: [(row_key, header, rich_column_kwargs), ...]
    "events": [
        ("created_at", "When", {"style": "dim", "no_wrap": True}),
        ("event_type", "Type", {"style": "cyan", "no_wrap": True}),
        ("repo_name", "Repo", {"style": "green"}),
        ("action", "Action", {"style": "yellow"}),
        ("details", "Details", {"overflow": "fold"}),
        ("url", "URL", {"overflow": "fold", "style": "blue"}),
    ],
}


def render_inspect(
    username: str,
    data_type: str,
    rows: list[dict[str, Any]],
    as_json: bool = False,
) -> None:
    """Render inspect results for a specific data type."""
    if as_json:
        console.print_json(json.dumps({"username": username, "type": data_type, "rows": rows}))
        return

    if not rows:
        console.print(f"[dim]No {data_type} data for {username}[/dim]")
        return

    table = Table(title=f"{username} / {data_type}", show_lines=False, border_style="blue")

    spec = _INSPECT_COLUMN_SPECS.get(data_type)
    if spec:
        for _, header, kwargs in spec:
            table.add_column(header, **kwargs)
        for row in rows:
            table.add_row(
                *[
                    str(row.get(key)) if row.get(key) is not None else "[dim]null[/dim]"
                    for key, _, _ in spec
                ]
            )
    else:
        for key in rows[0]:
            table.add_column(key, overflow="fold")
        for row in rows:
            table.add_row(*[str(v) if v is not None else "[dim]null[/dim]" for v in row.values()])

    console.print(table)


# ---------------------------------------------------------------------------
# Event detail (show command)
# ---------------------------------------------------------------------------


def render_event_detail(event: dict[str, Any] | None, as_json: bool = False) -> None:
    """Render a single event in detail, including its full payload."""
    if as_json:
        console.print_json(json.dumps(event or {}, sort_keys=True, default=str))
        return

    if event is None:
        console.print("[red]Event not found.[/red]")
        return

    header_parts = [
        f"[cyan]{event.get('event_type') or 'Unknown'}[/cyan]",
        f"[green]{event.get('repo_name') or '-'}[/green]",
        f"[dim]{event.get('created_at') or ''}[/dim]",
    ]
    header = " · ".join(header_parts)

    body_lines: list[str] = [header]
    if event.get("actor_login"):
        body_lines.append(f"[dim]actor:[/dim] {event['actor_login']}")
    if event.get("action"):
        body_lines.append(f"[dim]action:[/dim] {event['action']}")
    if event.get("details"):
        body_lines.append(f"[bold]{event['details']}[/bold]")
    if event.get("url"):
        body_lines.append(f"[blue]{event['url']}[/blue]")

    console.print(
        Panel(
            "\n".join(body_lines),
            title=f"event {event.get('event_id', '?')}",
            border_style="blue",
        )
    )

    payload = event.get("payload") or {}
    if payload:
        console.print("[bold]Payload[/bold]")
        try:
            console.print(JSON.from_data(payload))
        except (TypeError, ValueError):
            console.print(json.dumps(payload, indent=2, default=str, sort_keys=True))


# ---------------------------------------------------------------------------
# Stats command
# ---------------------------------------------------------------------------


def render_stats(
    username: str,
    event_counts: list[tuple[str, int]],
    language_dist: list[tuple[str, int]],
    top_repos: list[tuple[str, int]],
    activity_by_day: list[tuple[str, int]],
    as_json: bool = False,
) -> None:
    """Render analytics for a user."""
    if as_json:
        console.print_json(
            json.dumps(
                {
                    "username": username,
                    "event_counts": dict(event_counts),
                    "language_distribution": dict(language_dist),
                    "top_repos": dict(top_repos),
                    "activity_by_day": dict(activity_by_day),
                }
            )
        )
        return

    console.print(f"\n[bold blue]Stats for {username}[/bold blue]\n")

    # Event type breakdown
    if event_counts:
        console.print("[bold]Event Types[/bold]")
        _render_bar_chart(event_counts)
        console.print()

    # Language distribution of starred repos
    if language_dist:
        console.print("[bold]Starred Repos by Language[/bold]")
        _render_bar_chart(language_dist[:15])
        console.print()

    # Most active repos
    if top_repos:
        table = Table(title="Most Active Repos", show_lines=False, border_style="dim")
        table.add_column("Repository", style="cyan")
        table.add_column("Events", justify="right")
        for repo, count in top_repos:
            table.add_row(repo, str(count))
        console.print(table)
        console.print()

    # Activity timeline
    if activity_by_day:
        console.print("[bold]Daily Activity (last 30 days)[/bold]")
        _render_sparkline(activity_by_day)
        console.print()


def _render_bar_chart(data: list[tuple[str, int]], max_width: int = 40) -> None:
    """Render a horizontal bar chart in the terminal."""
    if not data:
        return
    max_val = max(v for _, v in data)
    max_label = max(len(label) for label, _ in data)

    for label, value in data:
        bar_len = int((value / max_val) * max_width) if max_val > 0 else 0
        bar = "█" * bar_len
        console.print(f"  {label:<{max_label}}  [cyan]{bar}[/cyan] {value}")


def _render_sparkline(data: list[tuple[str, int]]) -> None:
    """Render a simple ASCII sparkline for time-series data."""
    if not data:
        return

    blocks = " ▁▂▃▄▅▆▇█"
    values = [v for _, v in data]
    max_val = max(values) if values else 1

    spark = ""
    for v in values:
        idx = int((v / max_val) * (len(blocks) - 1)) if max_val > 0 else 0
        spark += blocks[idx]

    # Show date range
    first_date = data[0][0]
    last_date = data[-1][0]
    total = sum(values)

    console.print(f"  {first_date} [cyan]{spark}[/cyan] {last_date}")
    console.print(f"  [dim]{total} events total, peak {max_val}/day[/dim]")


# ---------------------------------------------------------------------------
# Users command
# ---------------------------------------------------------------------------


def render_users(users: list[dict[str, Any]], as_json: bool = False) -> None:
    """Render the list of tracked users."""
    if as_json:
        console.print_json(json.dumps(users))
        return

    if not users:
        console.print(
            "[dim]No users tracked yet. Run 'github-spy snapshot <username>' to start.[/dim]"
        )
        return

    table = Table(title="Tracked Users", border_style="blue")
    table.add_column("Username", style="bold cyan")
    table.add_column("Events", justify="right")
    table.add_column("Stars", justify="right")
    table.add_column("Followers", justify="right")
    table.add_column("Repos", justify="right")

    for u in users:
        table.add_row(
            u["username"],
            str(u.get("events", 0)),
            str(u.get("stars", 0)),
            str(u.get("followers", 0)),
            str(u.get("repos", 0)),
        )

    console.print(table)


# ---------------------------------------------------------------------------
# Diff command
# ---------------------------------------------------------------------------


def render_diff(
    username: str,
    star_changes: list[dict[str, Any]],
    follower_changes: list[dict[str, Any]],
    repo_changes: list[dict[str, Any]],
    profile_changes: list[dict[str, Any]],
    events: list[dict[str, Any]] | None = None,
    as_json: bool = False,
) -> None:
    """Render changes detected over a time range."""
    events = events or []

    if as_json:
        console.print_json(
            json.dumps(
                {
                    "username": username,
                    "events": events,
                    "star_changes": star_changes,
                    "follower_changes": follower_changes,
                    "repo_changes": repo_changes,
                    "profile_changes": profile_changes,
                }
            )
        )
        return

    console.print(f"\n[bold blue]Changes for {username}[/bold blue]\n")

    if not any([events, star_changes, follower_changes, repo_changes, profile_changes]):
        console.print("[dim]No changes found in the specified time range.[/dim]")
        return

    if events:
        console.print(f"[bold]Events[/bold] ({len(events)})")
        table = Table(show_lines=False, border_style="dim")
        table.add_column("When", style="dim", no_wrap=True)
        table.add_column("Type", style="cyan", no_wrap=True)
        table.add_column("Repo", style="green")
        table.add_column("Details", overflow="fold")
        table.add_column("URL", overflow="fold", style="blue")
        for ev in events:
            table.add_row(
                ev.get("created_at") or "",
                ev.get("event_type") or "",
                ev.get("repo_name") or "",
                ev.get("details") or "",
                ev.get("url") or "",
            )
        console.print(table)
        console.print()

    _render_change_section("Star Changes", star_changes, "full_name")
    _render_change_section("Follower Changes", follower_changes, "login")
    _render_change_section("Repo Changes", repo_changes, "repo_name")

    if profile_changes:
        console.print("[bold]Profile Changes[/bold]")
        for ch in profile_changes:
            console.print(
                f"  [yellow]~[/yellow] {ch['field_name']}: "
                f"{ch.get('old_value', 'null')} -> {ch.get('new_value', 'null')} "
                f"[dim]({ch['detected_at']})[/dim]"
            )
        console.print()


def _render_change_section(title: str, changes: list[dict[str, Any]], label_key: str) -> None:
    if not changes:
        return
    console.print(f"[bold]{title}[/bold]")
    for ch in changes:
        kind = ch.get("event_kind", "")
        label = ch.get(label_key, "?")
        if "added" in kind or "created" in kind or "followed" in kind:
            console.print(f"  [green]+[/green] {label} [dim]({ch.get('detected_at', '')})[/dim]")
        else:
            console.print(f"  [red]-[/red] {label} [dim]({ch.get('detected_at', '')})[/dim]")
    console.print()


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def export_data(
    rows: list[dict[str, Any]],
    fmt: str,
    output: TextIO,
) -> None:
    """Export data rows in the specified format."""
    if fmt == "json":
        json.dump(rows, output, indent=2, sort_keys=True)
        output.write("\n")
    elif fmt == "jsonl":
        for row in rows:
            output.write(json.dumps(row, sort_keys=True) + "\n")
    elif fmt == "csv":
        if not rows:
            return
        writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
